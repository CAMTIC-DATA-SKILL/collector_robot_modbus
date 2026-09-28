"""Modbus TCP/RTU 가 공통으로 쓰는 연결 관리와 읽기/쓰기."""
import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from pymodbus.client import ModbusBaseSyncClient
from pymodbus.pdu import ModbusPDU

from .exceptions import MODBUS_ILLEGAL_DATA_ADDRESS, ModbusClientError, ModbusErrorCode, to_client_error

logger = logging.getLogger(__name__)


class BaseModbusClient(ABC):
    """pymodbus 동기 클라이언트 래퍼.

    - 하위 클래스는 `_build_client` 와 `_target` 만 구현한다.
    - pymodbus 동기 클라이언트는 스레드 안전하지 않으므로 요청 단위로 락을 잡는다.
    - 주소는 0-based 오프셋이다 (40001 → 0).
    - 실패는 모두 ModbusClientError(code, detail) 로 정규화된다.
    """

    def __init__(self, device_id: int) -> None:
        self.device_id = device_id
        self._lock = threading.RLock()
        self.client = self._build_client()

    @abstractmethod
    def _build_client(self) -> ModbusBaseSyncClient:
        """연결 설정으로 pymodbus 클라이언트를 생성한다."""

    @abstractmethod
    def _target(self) -> str:
        """로그/에러 메시지용 접속 대상 문자열."""

    @property
    def is_connected(self) -> bool:
        return self.client.connected

    def connect(self) -> None:
        with self._lock:
            if self.is_connected:
                return
            try:
                ok = self.client.connect()
            except Exception as e:
                raise to_client_error(e, default=ModbusErrorCode.CONNECT_FAILED) from e
            if not ok:
                raise ModbusClientError(ModbusErrorCode.CONNECT_FAILED, f"연결 실패: {self._target()}")
            logger.info("Modbus connected: %s (device_id=%d)", self._target(), self.device_id)

    def close(self) -> None:
        with self._lock:
            self.client.close()
            logger.info("Modbus closed: %s", self._target())

    def read_coils(self, address: int, count: int = 1) -> list[bool]:
        return self._read(
            lambda: self.client.read_coils(address, count=count, device_id=self.device_id)
        ).bits[:count]

    def read_discrete_inputs(self, address: int, count: int = 1) -> list[bool]:
        return self._read(
            lambda: self.client.read_discrete_inputs(address, count=count, device_id=self.device_id)
        ).bits[:count]

    def read_holding_registers(self, address: int, count: int = 1) -> list[int]:
        return self._read(
            lambda: self.client.read_holding_registers(address, count=count, device_id=self.device_id)
        ).registers

    def read_input_registers(self, address: int, count: int = 1) -> list[int]:
        return self._read(
            lambda: self.client.read_input_registers(address, count=count, device_id=self.device_id)
        ).registers

    def write_coil(self, address: int, value: bool) -> None:
        self._write(lambda: self.client.write_coil(address, value, device_id=self.device_id))

    def write_coils(self, address: int, values: list[bool]) -> None:
        self._write(lambda: self.client.write_coils(address, values, device_id=self.device_id))

    def write_register(self, address: int, value: int) -> None:
        self._write(lambda: self.client.write_register(address, value, device_id=self.device_id))

    def write_registers(self, address: int, values: list[int]) -> None:
        self._write(lambda: self.client.write_registers(address, values, device_id=self.device_id))

    def __enter__(self) -> "BaseModbusClient":
        self.connect()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def _read(self, call: Callable[[], ModbusPDU]) -> ModbusPDU:
        return self._execute(call, ModbusErrorCode.READ_FAILED)

    def _write(self, call: Callable[[], ModbusPDU]) -> None:
        self._execute(call, ModbusErrorCode.WRITE_FAILED)

    def _execute(self, call: Callable[[], ModbusPDU], code: ModbusErrorCode) -> ModbusPDU:
        with self._lock:
            try:
                result = call()
            except Exception as e:
                raise to_client_error(e, default=code) from e
        if result.isError():
            if getattr(result, "exception_code", None) == MODBUS_ILLEGAL_DATA_ADDRESS:
                raise ModbusClientError(ModbusErrorCode.ADDR_OUT_OF_RANGE, str(result))
            raise ModbusClientError(code, str(result))
        return result
