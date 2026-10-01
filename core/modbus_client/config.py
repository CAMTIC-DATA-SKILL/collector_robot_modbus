from dataclasses import dataclass


@dataclass(slots=True)
class ModbusConfig:
    """TCP/RTU 공통 설정. 환경 변수 값은 루트 config.py 에서 채운다."""

    # Modbus unit id (slave id)
    device_id: int = 1
    timeout: float = 3.0
    retries: int = 3


@dataclass(slots=True)
class ModbusTcpConfig(ModbusConfig):
    host: str = "127.0.0.1"
    port: int = 502


@dataclass(slots=True)
class ModbusRtuConfig(ModbusConfig):
    port: str = "/dev/ttyUSB0"
    baudrate: int = 9600
    bytesize: int = 8
    # "N" | "E" | "O"
    parity: str = "N"
    stopbits: int = 1
    # RS-485 어댑터가 송신 프레임을 에코하는 경우 True
    handle_local_echo: bool = False
