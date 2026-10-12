"""메모리 맵 읽기 결과 모델."""
from pydantic import BaseModel, ConfigDict

from .memory_map_model import PointType, RegisterKind

Value = int | float | str | list[int | float | str]


class Sample(BaseModel):
    """포인트 한 건의 읽기 결과. 실패하면 value 는 None, error 에 ModbusErrorCode 값 (예: "E-2203")."""

    model_config = ConfigDict(frozen=True)

    name: str
    kind: RegisterKind
    addr: str
    type: PointType
    value: Value | None = None
    error: str | None = None


class SocketSample(BaseModel):
    """소켓 한 자리의 읽기 결과. command 하나의 result 를 묶음으로 낸다.

    value 는 카탈로그 data 이름 → 값, blocks 는 읽은 20 block 원본(0~255)이다.
    소켓을 다시 쓴 읽기(E-3101)는 value 가 None 이고 blocks 는 다시 쓰기 전에 읽은 값이다.
    읽기 자체가 실패하면 blocks 도 None 이다.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    s_block: int
    command: int
    command_name: str
    # 카탈로그 param 이름 → 메모리 맵에 적은 값
    params: dict[str, int | float | str]
    value: dict[str, Value] | None = None
    blocks: list[int] | None = None
    error: str | None = None
