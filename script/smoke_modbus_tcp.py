"""Modbus TCP 클라이언트 스모크 테스트.

--host 를 주지 않으면 내장 시뮬레이터를 띄워 읽기/쓰기/에러 매핑까지 전체 점검한다.
--host 를 주면 실장비를 대상으로 읽기 전용 점검을 하고, --write 를 줄 때만
지정 번지에 값을 써 본 뒤 원래 값으로 복원한다 (--keep 이면 쓴 값을 유지).

예) python script/smoke_modbus_tcp.py
    python script/smoke_modbus_tcp.py --host 192.168.0.10 --kind holding --address 0 --count 10
    python script/smoke_modbus_tcp.py --host 192.168.0.10 --kind holding --address 100 --write
    python script/smoke_modbus_tcp.py --host 192.168.0.10 --address 100 --type f32 --write --value 1.5 --keep
"""
import argparse
import dataclasses
import logging
import sys

from _smoke import SmokeAbort, SmokeRunner
from _modbus_smoke import (
    add_target_args,
    expect_connect_failed,
    parse_target,
    run_device_suite,
    run_simulator_suite,
    start_tcp_simulator,
    stop_simulator,
)

from config import settings
from core.modbus_client import ModbusTcpClient, ModbusTcpConfig

SIM_HOST = "127.0.0.1"
SIM_PORT = 15020
UNREACHABLE_PORT = 1


def main() -> int:
    config = dataclasses.replace(settings.modbus_tcp)

    parser = argparse.ArgumentParser(description="Modbus TCP client smoke test")
    parser.add_argument("--host", help="device host (omit to run against built-in simulator)")
    parser.add_argument("--port", type=int, help=f"device port (default {config.port}, simulator {SIM_PORT})")
    parser.add_argument("--device-id", type=int, default=config.device_id)
    parser.add_argument("--timeout", type=float, default=config.timeout)
    parser.add_argument("--retries", type=int, default=config.retries)
    add_target_args(parser)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    target = parse_target(parser, args)

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
            run_device_suite(runner, ModbusTcpClient(config), target)
    except SmokeAbort:
        pass
    finally:
        if simulate:
            stop_simulator()
    return runner.finish()


if __name__ == "__main__":
    sys.exit(main())
