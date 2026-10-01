"""프로젝트 진입점. .env 설정으로 Modbus 장비에 연결하고 메모리 맵 JSON 의 포인트를 주기적으로 읽는다.

    python main.py
"""
import json
import logging
import signal
import threading
import time

from config import settings
from core.modbus_client import BaseModbusClient, MemoryMap, ModbusClientError, ModbusRtuClient, ModbusTcpClient
from model import Sample

logger = logging.getLogger("collector")


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


def run(stop: threading.Event) -> None:
    memory_map = load_memory_map()
    logger.info("memory map %s, read plan:\n%s", settings.memory_map_file.name, memory_map.describe_plan())
    client = build_client()
    try:
        while not stop.is_set():
            started = time.monotonic()
            try:
                client.connect()
                samples = memory_map.read(client)
            except ModbusClientError as e:
                # MemoryMap.read 는 연결 계열 에러만 올리므로 끊고 기다렸다 다시 연결한다
                logger.error("%s -> %.1fs 후 재연결", e, settings.reconnect_interval)
                client.close()
                stop.wait(settings.reconnect_interval)
                continue
            report(samples)
            stop.wait(max(0.0, settings.poll_interval - (time.monotonic() - started)))
    finally:
        client.close()


def main() -> None:
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    run(stop)


if __name__ == "__main__":
    main()
