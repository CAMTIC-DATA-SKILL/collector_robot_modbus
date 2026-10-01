"""Modbus TCP 클라이언트. 연결 설정은 생성자로 주입받는다."""
from pymodbus.client import ModbusTcpClient as PymodbusTcpClient

from .base_client import BaseModbusClient
from .config import ModbusTcpConfig


class ModbusTcpClient(BaseModbusClient):
    def __init__(self, config: ModbusTcpConfig) -> None:
        self.config = config
        super().__init__(device_id=self.config.device_id)

    def _build_client(self) -> PymodbusTcpClient:
        c = self.config
        return PymodbusTcpClient(
            host=c.host,
            port=c.port,
            timeout=c.timeout,
            retries=c.retries,
        )

    def _target(self) -> str:
        return f"{self.config.host}:{self.config.port}"
