"""Modbus 클라이언트 예외. 와이어에는 code 만, 로그에는 detail 을 쓴다.

코드 체계: E-<분류><일련번호>
  1xxx  연결 (connection)  - 재연결 대상
  2xxx  입출력 (io)        - E-2203 은 Modbus Illegal Data Address
  3xxx  프로토콜 (protocol)
  9xxx  분류 불가 (internal)
"""
from enum import Enum

from pymodbus.exceptions import ConnectionException, ModbusException

# Modbus exception code 02
MODBUS_ILLEGAL_DATA_ADDRESS = 2


class ModbusErrorCode(str, Enum):
    CONNECT_FAILED = "E-1001"
    CONNECTION_CLOSED = "E-1002"
    TIMEOUT = "E-1003"

    READ_FAILED = "E-2001"
    WRITE_FAILED = "E-2002"
    ADDR_OUT_OF_RANGE = "E-2203"

    PROTOCOL_ERROR = "E-3001"

    UNKNOWN = "E-9001"

    @property
    def requires_reconnect(self) -> bool:
        return self.value.startswith("E-1")


class ModbusClientError(Exception):
    def __init__(self, code: ModbusErrorCode, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}({code.name}) {detail}".rstrip())


def to_client_error(
    exc: BaseException,
    *,
    default: ModbusErrorCode = ModbusErrorCode.UNKNOWN,
) -> ModbusClientError:
    """pymodbus/표준 예외를 ModbusClientError 로 정규화한다."""
    if isinstance(exc, ModbusClientError):
        return exc

    detail = str(exc)

    if isinstance(exc, TimeoutError) or "timeout" in detail.lower():
        return ModbusClientError(ModbusErrorCode.TIMEOUT, detail)

    if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
        return ModbusClientError(ModbusErrorCode.CONNECTION_CLOSED, detail)

    if isinstance(exc, (ConnectionError, ConnectionException, OSError)):
        return ModbusClientError(ModbusErrorCode.CONNECT_FAILED, detail)

    if _is_addr_out_of_range(exc, detail):
        return ModbusClientError(ModbusErrorCode.ADDR_OUT_OF_RANGE, detail)

    if isinstance(exc, ModbusException) and default is ModbusErrorCode.UNKNOWN:
        return ModbusClientError(ModbusErrorCode.PROTOCOL_ERROR, detail)

    return ModbusClientError(default, detail)


def _is_addr_out_of_range(exc: BaseException, detail: str) -> bool:
    if getattr(exc, "exception_code", None) == MODBUS_ILLEGAL_DATA_ADDRESS:
        return True
    upper = detail.upper()
    return "ILLEGAL DATA ADDRESS" in upper or "EXCEPTION_CODE=2" in upper
