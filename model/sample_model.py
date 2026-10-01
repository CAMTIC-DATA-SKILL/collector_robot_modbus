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
