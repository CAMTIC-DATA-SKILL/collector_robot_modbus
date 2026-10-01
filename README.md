# collector_robot_modbus
로봇 데이터 수집을 전담하는 컬렉터 레포지토리로 modbus로 구축함을 전제로 한다

## 구성

```
core/
├── modbus_tcp_client/   # Modbus 클라이언트 (BaseModbusClient ← ModbusTcpClient / ModbusRtuClient), 메모리 맵 읽기
└── ros2_client/         # ROS 2(rclpy) 클라이언트
model/                   # pydantic 모델 (메모리 맵 스키마, 읽기 요청, 읽기 결과)
config/                  # 메모리 맵 JSON 예시
script/                  # 스모크 테스트
```

## 환경 준비

ROS 2 Humble 과 Python 3.10 기준이다. rclpy 등 ROS 파이썬 패키지는 apt(`ros-humble-*`)로 설치되어 있어야 한다.

```bash
python3.10 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

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
```

### Modbus RTU

```bash
# 가상 시리얼 라인(pty 브리지)에 시뮬레이터를 띄워 전체 점검 (Linux 전용)
python script/smoke_modbus_rtu.py

# 실장비: 읽기 전용 점검
python script/smoke_modbus_rtu.py --port /dev/ttyUSB0 --baudrate 9600 --parity N --device-id 1 --count 10

# 실장비: coil 쓰기/복원 점검
python script/smoke_modbus_rtu.py --port /dev/ttyUSB0 --kind coil --address 0 --write
```

시리얼 포트 권한이 없으면 `sudo usermod -aG dialout $USER` 후 재로그인한다.

### Modbus 공통 옵션

| 옵션 | 설명 |
| --- | --- |
| `--kind` | `coil` / `discrete` / `holding`(기본) / `input` |
| `--address` | 시작 번지. 0-based 오프셋이다 (40001 → 0) |
| `--count` | 읽을 개수 (기본 1) |
| `--write` | 실장비 모드에서 `--address` 한 곳에 값을 바꿔 쓰고 확인한 뒤 원래 값으로 복원. `holding`, `coil` 만 가능 |
| `--device-id` | Modbus unit id (slave id) |
| `--timeout`, `--retries` | 요청 타임아웃(초) / 재시도 횟수 |
| `-v`, `--verbose` | pymodbus 디버그 로그(송수신 프레임) 출력 |

TCP 전용: `--host`, `--port` / RTU 전용: `--port`, `--baudrate`, `--parity`(N/E/O), `--stopbits`(1/2), `--local-echo`

옵션을 주지 않은 값은 환경변수를 따른다.

| TCP | RTU |
| --- | --- |
| `MODBUS_TCP_HOST`, `MODBUS_TCP_PORT` | `MODBUS_RTU_PORT`, `MODBUS_RTU_BAUDRATE`, `MODBUS_RTU_PARITY`, `MODBUS_RTU_STOPBITS`, `MODBUS_RTU_BYTESIZE`, `MODBUS_RTU_LOCAL_ECHO` |
| `MODBUS_TCP_DEVICE_ID`, `MODBUS_TCP_TIMEOUT`, `MODBUS_TCP_RETRIES` | `MODBUS_RTU_DEVICE_ID`, `MODBUS_RTU_TIMEOUT`, `MODBUS_RTU_RETRIES` |

단, 시뮬레이터 모드 여부는 `--host` / `--port` 인자로만 결정된다.

### Modbus 점검 항목

- **시뮬레이터 모드**: 연결, 4종 읽기(coil/discrete/holding/input), 4종 쓰기 후 되읽기 비교,
  범위 밖 읽기/쓰기가 `ADDR_OUT_OF_RANGE`(E-2203)로 매핑되는지, 끊었다 다시 연결 후 읽기,
  연결 불가 대상이 `CONNECT_FAILED`(E-1001, 재연결 대상)로 매핑되는지.
  시뮬레이터 초기값은 coil=False, discrete=True, holding=5, input=7 이고 레지스터 0~99 번지를 가진다.
- **실장비 모드**: 연결, 지정 영역 읽기, (`--write` 시) 쓰기/되읽기/복원, 재연결 후 읽기.

> `--write` 는 실제 로봇 레지스터 값을 잠시 바꾼다. 복원은 `finally` 로 보장하지만
> 명령/트리거용 레지스터에는 쓰지 말고, 영향이 없는 번지로만 사용한다.

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

도메인은 `ROS_DOMAIN_ID` 환경변수를 따른다. 로봇과 같은 도메인에서 외부 토픽을 점검해야 한다.

점검 항목: 노드 시작, 자기 토픽 publish/subscribe 루프백, 중복 구독(`RosClientError`),
타입 충돌·미존재 타입(`RosMessageTypeError`), 구독 해제, `std_srvs/srv/Trigger` 서비스 호출,
구독 콜백 안에서 서비스 호출 시 교착이 없는지, 없는 서비스 호출(`RosTimeoutError`),
노드 종료 후 호출(`RosConnectionError`), (옵션) 외부 토픽 수신.

### 빠른 확인용 CLI

통과/실패 판정 없이 값만 계속 보고 싶을 때는 패키지 CLI 를 쓴다.

```bash
python -m core.modbus_tcp_client tcp --host 192.168.0.10 --kind holding --address 0 --count 10 --interval 1
python -m core.modbus_tcp_client rtu --port /dev/ttyUSB0 --baudrate 9600 --kind coil --count 8
python -m core.modbus_tcp_client tcp --host 192.168.0.10 --map config/memory_map.example.json --interval 1
python -m core.ros2_client --topic /chatter --type std_msgs/msg/String
```

## 메모리 맵 (8bit/16bit/32bit 혼합 레지스터)

장비 매뉴얼의 레지스터 맵을 JSON 에 포인트마다 `addr` / `type` / `name` 으로 옮겨 적는다 (`config/memory_map.example.json` 참고).
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
| `addr` | 0-based 레지스터 번호. `100.5` 는 비트 하나, `100.8~F` 는 비트 구간 (0 = LSB, 10~15 는 A~F) |
| `type` | `u16` `i16` `u32` `i32` `u64` `i64` `f32` `f64` 는 레지스터 단위, `u8` `i8` `bit` 는 비트 구간으로 지정. `i16[6]` 처럼 배열 가능 |
| `scale` | 디코딩 값에 곱할 배율 |
| `format` | `dec`(기본) / `hex`. 최상위에 두면 전체 기본값, 포인트에 두면 그 포인트만. hex 는 scale 을 적용하지 않은 원시 비트를 `"0xFF38"` 처럼 낸다 |
| `word_endian` | 2레지스터 이상 값의 순서. `le`(기본, 앞 레지스터가 하위 워드) / `be`. collector-plc 와 같은 의미 |
| `max_gap` | 빈 번지가 이 개수 이하면 같은 요청으로 묶는다 (기본 8). 한 요청은 최대 125 레지스터 |

장비가 묶은 범위를 `ILLEGAL DATA ADDRESS` 로 거부하면 레지스터를 공유하는 포인트 단위로 쪼개 다시 읽고,
쪼갠 계획을 다음 읽기부터 그대로 쓴다. 그래도 실패한 포인트만 `Sample.error` 에 코드가 남고 나머지 값은 살아 있다.

```python
from core.modbus_tcp_client import MemoryMap, ModbusTcpClient, ModbusTcpConfig

memory_map = MemoryMap.from_json("config/memory_map.example.json")
with ModbusTcpClient(ModbusTcpConfig(host="192.168.0.10")) as client:
    samples = memory_map.read(client)   # [Sample(name="status_code", value=1, error=None, ...), ...]
    values = MemoryMap.values(samples)  # {"status_code": 1, "temperature": -20.0, ...} (실패 포인트 제외)
```

실제로 나갈 요청은 장비 없이 확인할 수 있다.

```bash
python -m core.modbus_tcp_client tcp --map config/memory_map.example.json --plan
# holding 0~13 (14 regs): status_code, sensor_mode, temperature, humidity, servo_on, joint_torque, run_counter, alarm_code
# input 100~102 (3 regs): battery_voltage, error_flags
```
