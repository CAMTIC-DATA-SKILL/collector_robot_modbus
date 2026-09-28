"""Modbus RTU 클라이언트. 연결 설정은 생성자로 주입받는다."""
from pymodbus.client import ModbusSerialClient

from .base_client import BaseModbusClient
from .config import ModbusRtuConfig


class ModbusRtuClient(BaseModbusClient):
    def __init__(self, config: ModbusRtuConfig | None = None) -> None:
        self.config = config or ModbusRtuConfig.from_env()
        super().__init__(device_id=self.config.device_id)

    def _build_client(self) -> ModbusSerialClient:
        c = self.config
        return ModbusSerialClient(
            port=c.port,
            baudrate=c.baudrate,
            bytesize=c.bytesize,
            parity=c.parity,
            stopbits=c.stopbits,
            handle_local_echo=c.handle_local_echo,
            timeout=c.timeout,
            retries=c.retries,
        )

    def _target(self) -> str:
        return self.config.port
