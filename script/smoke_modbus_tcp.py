"""Modbus TCP 클라이언트 스모크 테스트.

--host 를 주지 않으면 내장 시뮬레이터를 띄워 읽기/쓰기/에러 매핑까지 전체 점검한다.
--host 를 주면 실장비를 대상으로 읽기 전용 점검을 하고, --write 를 줄 때만
지정 번지에 값을 써 본 뒤 원래 값으로 복원한다.

예) python script/smoke_modbus_tcp.py
    python script/smoke_modbus_tcp.py --host 192.168.0.10 --kind holding --address 0 --count 10
    python script/smoke_modbus_tcp.py --host 192.168.0.10 --kind holding --address 100 --write
"""
import argparse
import logging
import sys

from _smoke import SmokeAbort, SmokeRunner
from _modbus_smoke import (
    READERS,
    expect_connect_failed,
    run_device_suite,
    run_simulator_suite,
    start_tcp_simulator,
    stop_simulator,
)

from core.modbus_tcp_client import ModbusTcpClient, ModbusTcpConfig

SIM_HOST = "127.0.0.1"
SIM_PORT = 15020
UNREACHABLE_PORT = 1


def main() -> int:
    config = ModbusTcpConfig.from_env()

    parser = argparse.ArgumentParser(description="Modbus TCP client smoke test")
    parser.add_argument("--host", help="device host (omit to run against built-in simulator)")
    parser.add_argument("--port", type=int, help=f"device port (default {config.port}, simulator {SIM_PORT})")
    parser.add_argument("--device-id", type=int, default=config.device_id)
    parser.add_argument("--timeout", type=float, default=config.timeout)
    parser.add_argument("--retries", type=int, default=config.retries)
    parser.add_argument("--kind", choices=READERS, default="holding")
    parser.add_argument("--address", type=int, default=0, help="0-based offset")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--write", action="store_true", help="write/readback/restore on --address (device only)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    if args.write and args.kind not in ("holding", "coil"):
        parser.error("--write supports only --kind holding or coil")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not args.verbose:
        logging.getLogger("pymodbus").setLevel(logging.CRITICAL)

    config.device_id, config.timeout, config.retries = args.device_id, args.timeout, args.retries
    simulate = args.host is None
    if simulate:
        config.host, config.port = SIM_HOST, args.port or SIM_PORT
    else:
        config.host, config.port = args.host, args.port or config.port

    runner = SmokeRunner(f"Modbus TCP {'simulator' if simulate else 'device'} {config.host}:{config.port}")
    try:
        if simulate:
            runner.check("start simulator", lambda: start_tcp_simulator(config.host, config.port, config.device_id))
            run_simulator_suite(runner, ModbusTcpClient(config))
            unreachable = ModbusTcpConfig(host=SIM_HOST, port=UNREACHABLE_PORT, timeout=0.5, retries=0)
            expect_connect_failed(runner, ModbusTcpClient(unreachable))
        else:
            run_device_suite(runner, ModbusTcpClient(config), args.kind, args.address, args.count, args.write)
    except SmokeAbort:
        pass
    finally:
        if simulate:
            stop_simulator()
    return runner.finish()


if __name__ == "__main__":
    sys.exit(main())
