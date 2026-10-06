"""프로젝트 진입점. Modbus 장비의 메모리 맵 포인트를 주기적으로 읽어 ZeroMQ 로 게이트웨이에 보낸다.

    python main.py

수집 스레드는 연결 → 읽기 → data PUB 을 돌고 연결 상태가 바뀌면 health 를 PUB 한다.
메인 스레드는 게이트웨이 cmd 를 SUB 하고 ack 를 PUB 한다.
"""
import json
import logging
import signal
import threading
import time
from uuid import uuid4

from pydantic import BaseModel

from config import settings
from core.modbus_client import BaseModbusClient, MemoryMap, ModbusClientError, ModbusRtuClient, ModbusTcpClient
from core.zeromq_client import ZeroMqClient, ZmqClientError
from model import (
    AckCode,
    MsgAckDTO,
    MsgAckStatusEnum,
    MsgCmdREnum,
    MsgCmdWEnum,
    MsgDataDTO,
    MsgHealthDTO,
    MsgSampleDTO,
    MsgTypeEnum,
    ProtocolHeaderDTO,
    Sample,
)

logger = logging.getLogger("collector")


def now_ms() -> int:
    return int(time.time() * 1000)


def build_client() -> BaseModbusClient:
    if settings.modbus_mode == "rtu":
        return ModbusRtuClient(settings.modbus_rtu)
    return ModbusTcpClient(settings.modbus_tcp)


def load_memory_map() -> MemoryMap:
    path = settings.memory_map_file
    if not path.is_file():
        raise FileNotFoundError(
            f"메모리 맵 파일이 없습니다: {path} (memory_map.example.json 을 복사하거나 MEMORY_MAP_FILE 을 확인하세요)"
        )
    return MemoryMap.from_json(path)


def report(samples: list[Sample]) -> None:
    logger.info("%s", json.dumps(MemoryMap.values(samples), ensure_ascii=False))
    errors = {s.name: s.error for s in samples if s.error}
    if errors:
        logger.warning("read errors: %s", errors)


def emit(zmq: ZeroMqClient, msg_type: MsgTypeEnum, body: BaseModel, *, gateway_address: str | None = None) -> None:
    """헤더를 씌워 PUB 한다. 송신 실패는 수집을 멈추지 않도록 로그만 남긴다."""
    header = ProtocolHeaderDTO(
        msg_id=uuid4(),
        gateway_address=gateway_address or settings.gateway_address,
        collector_address=settings.device_key,
        msg_type=msg_type,
        msg_body=body.model_dump(mode="json"),
        timestamp_ms=now_ms(),
    )
    logger.debug("[%s] %s", msg_type.value.upper(), header.model_dump_json())
    try:
        zmq.send(header)
    except ZmqClientError as e:
        logger.warning("zmq pub failed: %s", e)


def emit_health(zmq: ZeroMqClient, *, device_ok: bool, reason: str | None = None) -> None:
    body = MsgHealthDTO(
        device_key=settings.device_key, ipc_ok=True, device_ok=device_ok, reason=reason, observed_ms=now_ms()
    )
    emit(zmq, MsgTypeEnum.HEALTH, body)


def emit_data(zmq: ZeroMqClient, samples: list[Sample]) -> None:
    body = MsgDataDTO(
        device_key=settings.device_key,
        sample_ms=now_ms(),
        samples=[MsgSampleDTO.model_validate(s.model_dump()) for s in samples],
    )
    emit(zmq, MsgTypeEnum.DATA, body)


def collect(stop: threading.Event, memory_map: MemoryMap, zmq: ZeroMqClient) -> None:
    client = build_client()
    device_ok = False
    try:
        while not stop.is_set():
            started = time.monotonic()
            try:
                client.connect()
                samples = memory_map.read(client)
            except ModbusClientError as e:
                # MemoryMap.read 는 연결 계열 에러만 올리므로 끊고 기다렸다 다시 연결한다
                logger.error("%s -> %.1fs 후 재연결", e, settings.reconnect_interval)
                emit_health(zmq, device_ok=False, reason=e.code.value)
                device_ok = False
                client.close()
                stop.wait(settings.reconnect_interval)
                continue
            if not device_ok:
                emit_health(zmq, device_ok=True)
                device_ok = True
            report(samples)
            emit_data(zmq, samples)
            stop.wait(max(0.0, settings.poll_interval - (time.monotonic() - started)))
    finally:
        client.close()
        # 예기치 않은 예외로 수집이 죽으면 프로세스도 내린다
        stop.set()


def handle_command(zmq: ZeroMqClient, header: ProtocolHeaderDTO) -> None:
    """cmd_r / cmd_w 는 아직 처리하지 않으므로 rejected(E-4102) ack 로 응답한다."""
    logger.info("[SUB %s] %s", header.msg_type.value.upper(), header.model_dump_json())
    if header.msg_type is MsgTypeEnum.CMD_R:
        actions = MsgCmdREnum
    elif header.msg_type is MsgTypeEnum.CMD_W:
        actions = MsgCmdWEnum
    else:
        return
    raw_action = header.msg_body.get("action")
    try:
        action = actions(raw_action)
    except ValueError:
        logger.warning("%s action 판별 불가, ack 생략: %r", header.msg_type.value, raw_action)
        return
    body = MsgAckDTO(
        ref_msg_id=header.msg_id,
        device_key=settings.device_key,
        action=action,
        status=MsgAckStatusEnum.REJECTED,
        code=AckCode.CMD_UNSUPPORTED_ACTION,
        reason=AckCode.CMD_UNSUPPORTED_ACTION.name,
    )
    emit(zmq, MsgTypeEnum.ACK, body, gateway_address=header.gateway_address)


def serve_commands(stop: threading.Event, zmq: ZeroMqClient) -> None:
    while not stop.is_set():
        try:
            header = zmq.recv()
        except ZmqClientError as e:
            logger.warning("zmq sub failed: %s", e)
            stop.wait(0.2)
            continue
        # cmd 는 모든 collector 에 같은 토픽으로 오므로 다른 collector 대상은 응답하지 않는다
        if header is None or header.collector_address != settings.device_key:
            continue
        handle_command(zmq, header)


def main() -> None:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    memory_map = load_memory_map()
    logger.info("memory map %s, read plan:\n%s", settings.memory_map_file.name, memory_map.describe_plan())

    zmq = ZeroMqClient(settings.zmq)
    zmq.connect()
    worker = threading.Thread(target=collect, args=(stop, memory_map, zmq), name="collector", daemon=True)
    worker.start()
    try:
        serve_commands(stop, zmq)
    finally:
        stop.set()
        worker.join(timeout=settings.reconnect_interval + 2.0)
        zmq.close()


if __name__ == "__main__":
    main()
