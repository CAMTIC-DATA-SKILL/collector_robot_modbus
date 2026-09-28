import os
from dataclasses import dataclass


@dataclass(slots=True)
class ModbusConfig:
    """TCP/RTU 공통 설정."""

    # Modbus unit id (slave id)
    device_id: int = 1
    timeout: float = 3.0
    retries: int = 3


@dataclass(slots=True)
class ModbusTcpConfig(ModbusConfig):
    host: str = "127.0.0.1"
    port: int = 502

    @classmethod
    def from_env(cls) -> "ModbusTcpConfig":
        defaults = cls()
        return cls(
            device_id=int(os.getenv("MODBUS_TCP_DEVICE_ID", defaults.device_id)),
            timeout=float(os.getenv("MODBUS_TCP_TIMEOUT", defaults.timeout)),
            retries=int(os.getenv("MODBUS_TCP_RETRIES", defaults.retries)),
            host=os.getenv("MODBUS_TCP_HOST", defaults.host),
            port=int(os.getenv("MODBUS_TCP_PORT", defaults.port)),
        )


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

    @classmethod
    def from_env(cls) -> "ModbusRtuConfig":
        defaults = cls()
        return cls(
            device_id=int(os.getenv("MODBUS_RTU_DEVICE_ID", defaults.device_id)),
            timeout=float(os.getenv("MODBUS_RTU_TIMEOUT", defaults.timeout)),
            retries=int(os.getenv("MODBUS_RTU_RETRIES", defaults.retries)),
            port=os.getenv("MODBUS_RTU_PORT", defaults.port),
            baudrate=int(os.getenv("MODBUS_RTU_BAUDRATE", defaults.baudrate)),
            bytesize=int(os.getenv("MODBUS_RTU_BYTESIZE", defaults.bytesize)),
            parity=os.getenv("MODBUS_RTU_PARITY", defaults.parity).upper(),
            stopbits=int(os.getenv("MODBUS_RTU_STOPBITS", defaults.stopbits)),
            handle_local_echo=os.getenv("MODBUS_RTU_LOCAL_ECHO", str(defaults.handle_local_echo)).lower()
            in ("1", "true", "yes"),
        )
