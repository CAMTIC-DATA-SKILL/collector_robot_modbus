"""Modbus RTU 클라이언트 스모크 테스트.

--port 를 주지 않으면 pty 로 만든 가상 시리얼 라인에 내장 시뮬레이터를 띄워
읽기/쓰기/에러 매핑까지 전체 점검한다 (Linux 전용, 장비 불필요).
--port 를 주면 실장비를 대상으로 읽기 전용 점검을 하고, --write 를 줄 때만
지정 번지에 값을 써 본 뒤 원래 값으로 복원한다.

예) python script/smoke_modbus_rtu.py
    python script/smoke_modbus_rtu.py --port /dev/ttyUSB0 --baudrate 9600 --parity N --device-id 1 --count 10
    python script/smoke_modbus_rtu.py --port /dev/ttyUSB0 --kind coil --address 0 --write
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
    start_rtu_simulator,
    stop_simulator,
)

from core.modbus_tcp_client import ModbusRtuClient, ModbusRtuConfig

UNREACHABLE_PORT = "/dev/tty-modbus-smoke-missing"


def main() -> int:
    config = ModbusRtuConfig.from_env()

    parser = argparse.ArgumentParser(description="Modbus RTU client smoke test")
    parser.add_argument("--port", help="serial device, e.g. /dev/ttyUSB0 (omit to run against built-in simulator)")
    parser.add_argument("--baudrate", type=int, default=config.baudrate)
    parser.add_argument("--parity", choices=["N", "E", "O"], default=config.parity)
    parser.add_argument("--stopbits", type=int, choices=[1, 2], default=config.stopbits)
    parser.add_argument("--local-echo", action="store_true", default=config.handle_local_echo)
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

    config.baudrate, config.parity, config.stopbits = args.baudrate, args.parity, args.stopbits
    config.handle_local_echo = args.local_echo
    config.device_id, config.timeout, config.retries = args.device_id, args.timeout, args.retries
    simulate = args.port is None

    runner = SmokeRunner(
        f"Modbus RTU {'simulator' if simulate else 'device'} "
        f"{args.port or 'virtual pty'} {config.baudrate}{config.parity}{config.stopbits}"
    )
    try:
        if simulate:
            config.port = runner.check(
                "start simulator (virtual serial)",
                lambda: start_rtu_simulator(config.device_id, config.baudrate, config.parity, config.stopbits),
                required=True,
            )
            run_simulator_suite(runner, ModbusRtuClient(config))
            unreachable = ModbusRtuConfig(port=UNREACHABLE_PORT, timeout=0.5, retries=0)
            expect_connect_failed(runner, ModbusRtuClient(unreachable))
        else:
            config.port = args.port
            run_device_suite(runner, ModbusRtuClient(config), args.kind, args.address, args.count, args.write)
    except SmokeAbort:
        pass
    finally:
        if simulate:
            stop_simulator()
    return runner.finish()


if __name__ == "__main__":
    sys.exit(main())
