"""레지스터/코일 읽기 스모크 테스트.

예) python -m core.modbus_tcp_client tcp --host 192.168.0.10 --kind holding --address 0 --count 10
    python -m core.modbus_tcp_client rtu --port /dev/ttyUSB0 --baudrate 9600 --kind coil --address 0 --count 8
    python -m core.modbus_tcp_client tcp --host 192.168.0.10 --address 0 --count 4 --interval 1
"""
import argparse
import logging
import signal
import threading

from .base_client import BaseModbusClient
from .config import ModbusRtuConfig, ModbusTcpConfig
from .exceptions import ModbusClientError
from .rtu_client import ModbusRtuClient
from .tcp_client import ModbusTcpClient

_READERS = {
    "coil": BaseModbusClient.read_coils,
    "discrete": BaseModbusClient.read_discrete_inputs,
    "holding": BaseModbusClient.read_holding_registers,
    "input": BaseModbusClient.read_input_registers,
}


def _build_client(args: argparse.Namespace) -> BaseModbusClient:
    if args.mode == "tcp":
        config = ModbusTcpConfig.from_env()
        config.host = args.host or config.host
        config.port = args.port or config.port
    else:
        config = ModbusRtuConfig.from_env()
        config.port = args.port or config.port
        config.baudrate = args.baudrate or config.baudrate
        config.parity = args.parity or config.parity
    if args.device_id is not None:
        config.device_id = args.device_id
    return ModbusTcpClient(config) if args.mode == "tcp" else ModbusRtuClient(config)


def main() -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--device-id", type=int)
    common.add_argument("--kind", choices=_READERS, default="holding")
    common.add_argument("--address", type=int, default=0, help="0-based offset")
    common.add_argument("--count", type=int, default=1)
    common.add_argument("--interval", type=float, default=0.0, help="seconds between polls (0 = read once)")

    parser = argparse.ArgumentParser(description="Modbus client read test")
    sub = parser.add_subparsers(dest="mode", required=True)
    tcp = sub.add_parser("tcp", parents=[common])
    tcp.add_argument("--host")
    tcp.add_argument("--port", type=int)
    rtu = sub.add_parser("rtu", parents=[common])
    rtu.add_argument("--port", help="serial device, e.g. /dev/ttyUSB0")
    rtu.add_argument("--baudrate", type=int)
    rtu.add_argument("--parity", choices=["N", "E", "O"])
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    read = _READERS[args.kind]
    with _build_client(args) as client:
        while True:
            try:
                print(read(client, args.address, args.count), flush=True)
            except ModbusClientError as e:
                logging.error("%s", e)
            if args.interval <= 0 or stop.wait(args.interval):
                break


if __name__ == "__main__":
    main()
