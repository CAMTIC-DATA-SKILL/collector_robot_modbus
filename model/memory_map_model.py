"""메모리 맵 JSON 스키마와 읽기 요청(Modbus FC03/FC04 프레임 단위) 모델.

- addr: 0-based 레지스터 번호. "100.5" 는 비트 하나, "100.8~F" 는 비트 구간 (0 = LSB, 10~15 는 A~F).
- type: u8 i8 bit 는 비트 구간으로만, u16 ~ f64 는 레지스터 단위로 지정한다.
  레지스터 단위 타입은 "i16[6]" 처럼 연속 배열로 쓸 수 있다.
"""
import re
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

# FC03/FC04 한 요청에서 읽을 수 있는 최대 레지스터 수
MAX_READ_REGISTERS = 125
MAX_REGISTER = 0xFFFF

_ADDR_RE = re.compile(r"^(\d+)(?:\.([0-9A-F])(?:~([0-9A-F]))?)?$", re.IGNORECASE)
_TYPE_RE = re.compile(r"^([a-z]+\d*)(?:\[(\d+)\])?$")


class RegisterKind(str, Enum):
    HOLDING = "holding"
    INPUT = "input"


class WordEndian(str, Enum):
    # 앞 레지스터가 하위 워드 (collector-plc 와 동일)
    LE = "le"
    BE = "be"


class ValueFormat(str, Enum):
    DEC = "dec"
    # scale 을 적용하지 않은 원시 비트를 "0xFF38" 처럼 낸다
    HEX = "hex"


class PointType(str, Enum):
    BIT = "bit"
    U8 = "u8"
    I8 = "i8"
    U16 = "u16"
    I16 = "i16"
    U32 = "u32"
    I32 = "i32"
    U64 = "u64"
    I64 = "i64"
    F32 = "f32"
    F64 = "f64"

    @property
    def width(self) -> int:
        return _TYPE_SPECS[self][0]

    @property
    def struct_format(self) -> str | None:
        """None 이면 레지스터 안의 비트 구간으로 읽는 타입."""
        return _TYPE_SPECS[self][1]


_TYPE_SPECS: dict[PointType, tuple[int, str | None]] = {
    PointType.BIT: (1, None),
    PointType.U8: (8, None),
    PointType.I8: (8, None),
    PointType.U16: (16, "H"),
    PointType.I16: (16, "h"),
    PointType.U32: (32, "I"),
    PointType.I32: (32, "i"),
    PointType.U64: (64, "Q"),
    PointType.I64: (64, "q"),
    PointType.F32: (32, "f"),
    PointType.F64: (64, "d"),
}


class MemoryMapPoint(BaseModel):
    """메모리 맵 한 점. addr/type 문자열은 검증 시 레지스터 위치·타입·길이로 풀어 둔다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    addr: str
    type: str
    scale: float | None = None
    # None 이면 맵의 format 을 따른다
    format: ValueFormat | None = None

    _register: int = PrivateAttr()
    _point_type: PointType = PrivateAttr()
    _length: int = PrivateAttr()
    _bits: tuple[int, int] | None = PrivateAttr()

    @field_validator("addr", "type", mode="before")
    @classmethod
    def _to_str(cls, value: object) -> str:
        return str(value).strip()

    @model_validator(mode="after")
    def _parse(self) -> "MemoryMapPoint":
        m_addr = _ADDR_RE.match(self.addr)
        m_type = _TYPE_RE.match(self.type.lower())
        if not m_addr:
            raise ValueError(f"{self.name}: addr 형식 오류 {self.addr!r} (예: 100, 100.5, 100.8~F)")
        if not m_type or m_type.group(1) not in PointType._value2member_map_:
            raise ValueError(f"{self.name}: 알 수 없는 type {self.type!r} (가능: {', '.join(t.value for t in PointType)})")

        register = int(m_addr.group(1))
        point_type = PointType(m_type.group(1))
        length = int(m_type.group(2) or 1)
        bits = None
        if m_addr.group(2) is not None:
            lo = int(m_addr.group(2), 16)
            hi = int(m_addr.group(3), 16) if m_addr.group(3) is not None else lo
            if hi < lo or hi - lo + 1 != point_type.width or point_type.struct_format or length != 1:
                raise ValueError(f"{self.name}: 비트 구간 {self.addr!r} 은 bit(1비트) / u8·i8(8비트) 단일 값에만 쓴다")
            bits = (lo, hi)
        elif not point_type.struct_format:
            raise ValueError(f"{self.name}: {point_type.value} 은 비트 구간으로 지정한다 (예: {register}.8~F)")
        if length < 1:
            raise ValueError(f"{self.name}: 배열 길이는 1 이상: {self.type!r}")

        self._register, self._point_type, self._length, self._bits = register, point_type, length, bits
        if self.end - 1 > MAX_REGISTER:
            raise ValueError(f"{self.name}: 레지스터 범위 초과 ({register}~{self.end - 1})")
        return self

    @property
    def register(self) -> int:
        return self._register

    @property
    def point_type(self) -> PointType:
        return self._point_type

    @property
    def length(self) -> int:
        """배열 길이. 스칼라는 1."""
        return self._length

    @property
    def bits(self) -> tuple[int, int] | None:
        """비트 구간 (lo, hi). None 이면 레지스터 단위 타입."""
        return self._bits

    @property
    def register_count(self) -> int:
        return 1 if self._bits else self._point_type.width // 16 * self._length

    @property
    def end(self) -> int:
        return self._register + self.register_count


class MemoryMapSettings(BaseModel):
    """메모리 맵 JSON 최상위."""

    model_config = ConfigDict(extra="forbid")

    word_endian: WordEndian = WordEndian.LE
    # 빈 번지가 이 개수 이하면 같은 요청으로 묶는다
    max_gap: int = Field(default=8, ge=0)
    format: ValueFormat = ValueFormat.DEC
    holding: list[MemoryMapPoint] = Field(default_factory=list)
    input: list[MemoryMapPoint] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_names(self) -> "MemoryMapSettings":
        names = [p.name for p in self.holding + self.input]
        duplicated = sorted({n for n in names if names.count(n) > 1})
        if duplicated:
            raise ValueError(f"name 중복: {duplicated}")
        return self

    def points_of(self, kind: RegisterKind) -> list[MemoryMapPoint]:
        return self.holding if kind is RegisterKind.HOLDING else self.input


class ReadRequest(BaseModel):
    """한 번의 FC03/FC04 요청으로 읽을 범위와 그 안에 든 포인트."""

    model_config = ConfigDict(frozen=True)

    kind: RegisterKind
    start: int
    count: int
    points: tuple[MemoryMapPoint, ...]

    @property
    def end(self) -> int:
        return self.start + self.count
