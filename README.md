# collector_robot_modbus
로봇 데이터 수집을 전담하는 컬렉터 레포지토리로 modbus로 구축함을 전제로 한다

## 아키텍처

```
로봇 Controller ──Modbus(TCP/RTU)──▶ collector_robot_modbus ──IPC(ZeroMQ)──▶ 게이트웨이 프로세스 ──▶ 상위 아키텍처
```

- **남향**: 로봇 Controller 와 Modbus(TCP/RTU)로 연동해 데이터 수집 및 제어 통신을 수행한다.
- **이 레포의 범위**: 수집(메모리 맵 기반 주기 읽기·디코딩)과 수집 데이터를 IPC 로 넘기는 데까지.
- **북향**: 상위 아키텍처로의 전송은 이 레포가 담당하지 않는다. IPC 로 연계된 별도 프로세스가 전달한다.

## 구성

```
main.py                  # 진입점: Modbus 주기 읽기 → ZeroMQ data/health PUB, 게이트웨이 cmd SUB
config.py                # .env 로딩. 환경 변수는 모두 여기서만 읽는다
memory_map.example.json  # 메모리 맵 예시 (memory_map.json 으로 복사해서 사용)
core/
├── modbus_client/       # Modbus 클라이언트 (BaseModbusClient ← ModbusTcpClient / ModbusRtuClient), 메모리 맵 읽기
├── zeromq_client/       # 게이트웨이 IPC (PUB + SUB 파사드 ZeroMqClient, 토픽 조립)
└── ros2_client/         # ROS 2(rclpy) 클라이언트
model/                   # pydantic 모델 (메모리 맵 스키마, 읽기 요청, 읽기 결과, IPC 프로토콜)
script/                  # 스모크 테스트
```

## 환경 준비

ROS 2 Humble 과 Python 3.10 기준이다. rclpy 등 ROS 파이썬 패키지는 apt(`ros-humble-*`)로 설치되어 있어야 한다.

```bash
python3.10 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cp memory_map.example.json memory_map.json
```

## 실행

```bash
python main.py
```

`.env` 의 `MODBUS_MODE`(tcp/rtu) 접속 정보로 연결하고, `MEMORY_MAP_FILE` 의 포인트를
`POLL_INTERVAL_SEC` 마다 읽어 로그로 출력하고 ZeroMQ 로 게이트웨이에 보낸다.
연결이 끊기면 `RECONNECT_INTERVAL_SEC` 후 다시 연결한다.
시작 시 실제로 나갈 읽기 요청(묶인 범위)을 먼저 출력한다.

```
INFO collector: memory map memory_map.json, read plan:
holding 0~13 (14 regs): status_code, sensor_mode, temperature, ...
INFO collector: {"status_code": 1, "sensor_mode": 2, "temperature": 25.8, ...}
WARNING collector: read errors: {'battery_voltage': 'E-2203'}
```

## 게이트웨이 IPC (ZeroMQ)

collector-plc 와 같은 방식이다. 소켓은 PUB + SUB 두 개이고 프레임은 multipart `[topic, ProtocolHeaderDTO JSON]` 이다.

| 방향 | 소켓 | 토픽 | msg_type |
| --- | --- | --- | --- |
| collector → gateway | PUB (`ZMQ_PUB_ENDPOINT`, bind) | `collector.robot.{DEVICE_KEY}.{msg_type}` | `data` / `health` / `ack` |
| gateway → collector | SUB (`ZMQ_SUB_ENDPOINT`, connect) | `middleware.gateway.cmd_` prefix | `cmd_r` / `cmd_w` |

- `data`: 매 주기 읽기 결과. `samples[]` 는 `Sample` 과 같은 필드(`name` `kind` `addr` `type` `value` `error`)이고 실패 포인트는 `value=null`, `error` 에 코드가 남는다.
- `health`: Modbus 연결 상태가 바뀔 때 (`device_ok`, 실패 시 `reason` 에 `E-1001` 등). 연결 실패 중에는 재시도마다 낸다.
- `ack`: cmd 처리 결과. cmd 는 아직 처리하지 않으므로 `status=rejected`, `code=E-4102`(CMD_UNSUPPORTED_ACTION) 로 응답한다.
  cmd 는 모든 collector 에 같은 토픽으로 오므로 헤더 `collector_address` 가 `DEVICE_KEY` 와 다르면 무시한다.

collector 마다 PUB 을 bind 하므로 같은 edge 에 collector 가 여럿이면 `ZMQ_PUB_ENDPOINT` 포트가 겹치면 안 되고
(기본 5557, collector-plc 는 5556), 게이트웨이는 collector 별 PUB 엔드포인트에 각각 connect 한다.
게이트웨이 PUB(cmd) 은 하나를 bind 하고 모든 collector 가 connect 한다 (기본 5555).

게이트웨이 없이 확인할 때는 게이트웨이 역할을 하는 CLI 로 PUB 메시지를 본다.

```bash
# collector.robot.* 수신 출력
python -m core.zeromq_client

# cmd_r 을 한 번 보내고 ack 까지 확인 (ZMQ_SUB_ENDPOINT 에 bind 하므로 실제 게이트웨이와 같이 띄우지 않는다)
python -m core.zeromq_client --device-key robot-1 --cmd cmd_r --action SET_SCAN
```

환경 변수 목록과 기본값은 `.env.example` 참고. 코드에서는 `from config import settings` 로만 참조한다.

ROS 2 스모크 테스트를 돌릴 때는 venv 활성화 전에 ROS 환경을 먼저 불러온다.

```bash
source /opt/ros/humble/setup.bash
source .venv/bin/activate
```

## 스모크 테스트

모든 명령은 저장소 루트에서 실행한다. 각 점검은 `[PASS]` / `[FAIL]` 로 출력되고
마지막 줄에 `== PASS n/n ==` 또는 `== FAIL k/n: <실패 항목> ==` 요약이 나온다.
하나라도 실패하면 종료 코드 1 을 반환하므로 CI 나 배포 스크립트에서 그대로 쓸 수 있다.
연결 같은 필수 점검이 실패하면 이후 점검은 건너뛴다.

| 스크립트 | 대상 | 장비 없이 실행 |
| --- | --- | --- |
| `script/smoke_modbus_tcp.py` | `ModbusTcpClient` | `--host` 생략 시 내장 시뮬레이터 |
| `script/smoke_modbus_rtu.py` | `ModbusRtuClient` | `--port` 생략 시 가상 시리얼(pty) + 시뮬레이터 |
| `script/smoke_ros2_client.py` | `RosClient` | 자기 자신과 통신 (외부 노드 불필요) |

### Modbus TCP

```bash
# 내장 시뮬레이터 대상 전체 점검 (127.0.0.1:15020)
python script/smoke_modbus_tcp.py

# 실장비: 읽기 전용 점검
python script/smoke_modbus_tcp.py --host 192.168.0.10 --kind holding --address 0 --count 10

# 실장비: 쓰기 → 다시 읽기 → 원래 값 복원까지 점검
python script/smoke_modbus_tcp.py --host 192.168.0.10 --kind holding --address 100 --write

# 실장비: 지정 값을 쓰고 복원하지 않고 유지 (f32 = 레지스터 2개)
python script/smoke_modbus_tcp.py --host 192.168.0.10 --address 100 --type f32 --write --value 1.5 --keep
```

### Modbus RTU

```bash
# 가상 시리얼 라인(pty 브리지)에 시뮬레이터를 띄워 전체 점검 (Linux 전용)
python script/smoke_modbus_rtu.py

# 실장비: 읽기 전용 점검
python script/smoke_modbus_rtu.py --port /dev/ttyUSB0 --baudrate 9600 --parity N --device-id 1 --count 10

# 실장비: coil 쓰기/복원 점검
python script/smoke_modbus_rtu.py --port /dev/ttyUSB0 --kind coil --address 0 --write

# 실장비: 레지스터 100 의 상위 8비트에 0x12 를 쓰고 유지 (하위 8비트는 보존)
python script/smoke_modbus_rtu.py --port /dev/ttyUSB0 --address 100.H --type u8 --write --value 0x12 --keep
```

시리얼 포트 권한이 없으면 `sudo usermod -aG dialout $USER` 후 재로그인한다.

### Modbus 공통 옵션

| 옵션 | 설명 |
| --- | --- |
| `--kind` | `coil` / `discrete` / `holding`(기본) / `input` |
| `--address` | 시작 번지. 0-based 오프셋이다 (40001 → 0). `holding` 은 메모리 맵 `addr` 처럼 `100.5`, `100.8~F`, `100.H` 비트 구간도 가능 |
| `--count` | 읽을 개수 (기본: `holding` 은 `--type` 이 차지하는 레지스터 수, 그 외 1) |
| `--write` | 실장비 모드에서 `--address` 에 값을 써 보고 다시 읽어 확인한 뒤 원래 값으로 복원. `holding`, `coil` 만 가능. `--value` 가 없으면 최하위 비트만 뒤집어 쓴다 |
| `--value` | `--write` 로 쓸 값. 배열/여러 coil 은 쉼표로 구분 (`1,0,1`), `0x..` 는 원시 비트, 음수는 `--value=-1` 처럼 `=` 로 붙인다 |
| `--keep` | 복원하지 않고 쓴 값을 유지. `--write --value` 와 함께만 쓸 수 있고, 결과에 복원용 `--value` 가 출력된다 |
| `--type` | `holding` 값 타입. 메모리 맵 `type` 과 같다 (`u16` 기본, `u8` `i8` `bit` 는 비트 구간, `u32` `i32` `f32` `u64` `i64` `f64`, `i16[3]` 배열) |
| `--word-endian` | 2레지스터 이상 값의 순서. `le`(기본) / `be`. 메모리 맵 `word_endian` 과 같다 |
| `--device-id` | Modbus unit id (slave id) |
| `--timeout`, `--retries` | 요청 타임아웃(초) / 재시도 횟수 |
| `-v`, `--verbose` | pymodbus 디버그 로그(송수신 프레임) 출력 |

TCP 전용: `--host`, `--port` / RTU 전용: `--port`, `--baudrate`, `--parity`(N/E/O), `--stopbits`(1/2), `--local-echo`

옵션을 주지 않은 값은 `.env` 의 `MODBUS_TCP_*` / `MODBUS_RTU_*` 값(`config.py`)을 따른다.

단, 시뮬레이터 모드 여부는 `--host` / `--port` 인자로만 결정된다.

### Modbus 점검 항목

- **시뮬레이터 모드**: 연결, 4종 읽기(coil/discrete/holding/input), 4종 쓰기 후 되읽기 비교,
  메모리 맵 타입별(8/16/32비트, 비트 구간, 배열, hex) 인코딩 → 쓰기 → 디코딩 왕복, 타입 지정 쓰기의 복원/유지,
  범위 밖 읽기/쓰기가 `ADDR_OUT_OF_RANGE`(E-2203)로 매핑되는지, 끊었다 다시 연결 후 읽기,
  연결 불가 대상이 `CONNECT_FAILED`(E-1001, 재연결 대상)로 매핑되는지.
  시뮬레이터 초기값은 coil=False, discrete=True, holding=5, input=7 이고 레지스터 0~99 번지를 가진다.
- **실장비 모드**: 연결, 지정 영역 읽기, (`--write` 시) 쓰기/되읽기/복원(`--keep` 이면 유지), 재연결 후 읽기.

> `--write` 는 실제 로봇 레지스터 값을 잠시 바꾼다. 복원은 `finally` 로 보장하지만
> 명령/트리거용 레지스터에는 쓰지 말고, 영향이 없는 번지로만 사용한다.
> `--keep` 은 복원하지 않으므로 값이 그대로 남는다. 되읽기가 실패해도 복원하지 않는다.
> 비트 구간 쓰기는 레지스터를 읽고 해당 비트만 바꿔 다시 쓰므로, 그 사이 장비가 같은 레지스터를 바꾸면 덮어쓸 수 있다.

### ROS 2

```bash
source /opt/ros/humble/setup.bash
source .venv/bin/activate

# 자기 자신과 통신하며 기본 기능 점검
python script/smoke_ros2_client.py

# 외부 토픽에서 메시지 1건 수신까지 확인
python script/smoke_ros2_client.py --topic /joint_states --type sensor_msgs/msg/JointState --sensor-qos
```

| 옵션 | 설명 |
| --- | --- |
| `--topic`, `--type` | 외부 토픽 수신 점검 (함께 지정) |
| `--sensor-qos` | `--topic` 구독에 `qos_profile_sensor_data`(best effort) 사용 |
| `--timeout` | 메시지/서비스 대기 시간(초, 기본 5) |
| `--node-name` | 노드 이름 (기본 `collector_smoke_<pid>`) |

도메인은 `.env` 의 `ROS_DOMAIN_ID` 를 따른다. 로봇과 같은 도메인에서 외부 토픽을 점검해야 한다.

점검 항목: 노드 시작, 자기 토픽 publish/subscribe 루프백, 중복 구독(`RosClientError`),
타입 충돌·미존재 타입(`RosMessageTypeError`), 구독 해제, `std_srvs/srv/Trigger` 서비스 호출,
구독 콜백 안에서 서비스 호출 시 교착이 없는지, 없는 서비스 호출(`RosTimeoutError`),
노드 종료 후 호출(`RosConnectionError`), (옵션) 외부 토픽 수신.

### ROS 2 토픽 확인용 CLI

통과/실패 판정 없이 토픽 메시지만 계속 보고 싶을 때 쓴다.

```bash
python -m core.ros2_client --topic /chatter --type std_msgs/msg/String
```

## 메모리 맵 (8bit/16bit/32bit 혼합 레지스터)

장비 매뉴얼의 레지스터 맵을 JSON 에 포인트마다 `addr` / `type` / `name` 으로 옮겨 적는다 (`memory_map.example.json` 참고).
읽기 요청은 주소를 보고 자동으로 묶으므로 시작 번지·개수를 따로 정하지 않는다.

```json
{
  "word_endian": "le",
  "max_gap": 8,
  "format": "dec",
  "holding": [
    {"addr": "0.8~F", "type": "u8",     "name": "status_code"},
    {"addr": "1",     "type": "i16",    "name": "temperature", "scale": 0.1},
    {"addr": "5",     "type": "i16[6]", "name": "joint_torque"},
    {"addr": "13",    "type": "u16",    "name": "alarm_code", "format": "hex"}
  ],
  "input": [{"addr": "100", "type": "f32", "name": "battery_voltage"}]
}
```

| 키 | 설명 |
| --- | --- |
| `addr` | 0-based 레지스터 번호. `100.5` 는 비트 하나, `100.8~F` 는 비트 구간 (0 = LSB, 10~15 는 A~F). `100.L` / `100.H` 는 하위 / 상위 바이트로 `100.0~7` / `100.8~F` 와 같고 섞어 써도 된다 |
| `type` | `u16` `i16` `u32` `i32` `u64` `i64` `f32` `f64` 는 레지스터 단위, `u8` `i8` `bit` 는 비트 구간으로 지정. `i16[6]` 처럼 배열 가능 |
| `scale` | 디코딩 값에 곱할 배율 |
| `format` | `dec`(기본) / `hex`. 최상위에 두면 전체 기본값, 포인트에 두면 그 포인트만. hex 는 scale 을 적용하지 않은 원시 비트를 `"0xFF38"` 처럼 낸다 |
| `word_endian` | 2레지스터 이상 값의 순서. `le`(기본, 앞 레지스터가 하위 워드) / `be`. collector-plc 와 같은 의미 |
| `max_gap` | 빈 번지가 이 개수 이하면 같은 요청으로 묶는다 (기본 8). 한 요청은 최대 125 레지스터 |

장비가 묶은 범위를 `ILLEGAL DATA ADDRESS` 로 거부하면 레지스터를 공유하는 포인트 단위로 쪼개 다시 읽고,
쪼갠 계획을 다음 읽기부터 그대로 쓴다. 그래도 실패한 포인트만 `Sample.error` 에 코드가 남고 나머지 값은 살아 있다.

```python
from config import settings
from core.modbus_client import MemoryMap, ModbusTcpClient

memory_map = MemoryMap.from_json(settings.memory_map_file)
print(memory_map.describe_plan())       # 실제로 나갈 요청 (장비 없이 확인 가능)
with ModbusTcpClient(settings.modbus_tcp) as client:
    samples = memory_map.read(client)   # [Sample(name="status_code", value=1, error=None, ...), ...]
    values = MemoryMap.values(samples)  # {"status_code": 1, "temperature": -20.0, ...} (실패 포인트 제외)
```
