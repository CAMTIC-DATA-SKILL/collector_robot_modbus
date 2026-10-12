"""HI6 S relay socket command 카탈로그 스키마. 원본은 매뉴얼을 전사한 CSV 이고 core.modbus_client.catalog 가 읽는다.

로봇 모델별 자산이므로 현장마다 고치지 않는다. 어느 S block 에 어떤 command 를 쓸지는 메모리 맵의 sockets 가 정한다.

- 위치(offset)와 크기는 모두 block(8bit) 단위다. offset 은 소켓 첫 block(command 시작)을 0 으로 센 위치로,
  매뉴얼 표의 위치를 그대로 옮긴다. command 가 0~1 을 쓰므로 parameter·result 는 2 부터 온다.
- type 은 HI6 표기를 그대로 쓴다. 숫자는 byte(block) 수다 (s2 = 부호 있는 2 byte, f4 = 4 byte 실수).
- 여러 block 에 걸친 값은 S 번호가 작은 block 이 하위 바이트인 little endian 이다. 레지스터 경계에 걸쳐도 된다.
"""
import re
from collections import Counter
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from .memory_map_model import MAX_REGISTER, SOCKET_BLOCKS, SOCKET_REGISTERS, PointType, ValueFormat

# command 가 차지하는 block 수 (소켓 offset 0~1)
COMMAND_BLOCKS = 2

_TYPE_RE = re.compile(r"^([a-z]+\d*)(?:\[(\d+)\])?$")


class CatalogType(str, Enum):
    """HI6 매뉴얼 자료형. 숫자는 bit 가 아니라 byte 수다."""

    S1 = "s1"
    S2 = "s2"
    S4 = "s4"
    S8 = "s8"
    U1 = "u1"
    U2 = "u2"
    U4 = "u4"
    U8 = "u8"
    F4 = "f4"
    F8 = "f8"
    # 원시 바이트. byte[16] 처럼 배열로 쓴다
    BYTE = "byte"

    @property
    def size(self) -> int:
        return _TYPE_SPECS[self][0]

    @property
    def struct_format(self) -> str:
        return _TYPE_SPECS[self][1]

    @property
    def signed(self) -> bool:
        return self.struct_format in "bhiq"

    @property
    def is_float(self) -> bool:
        return self.struct_format in "fd"

    @property
    def point_type(self) -> PointType:
        """메모리 맵(bit 수) 표기. 수집 결과에는 이 값을 실어 "u8" 이 8 bit / 8 byte 로 엇갈리지 않게 한다."""
        return _TYPE_SPECS[self][2]


_TYPE_SPECS: dict[CatalogType, tuple[int, str, PointType]] = {
    CatalogType.S1: (1, "b", PointType.I8),
    CatalogType.S2: (2, "h", PointType.I16),
    CatalogType.S4: (4, "i", PointType.I32),
    CatalogType.S8: (8, "q", PointType.I64),
    CatalogType.U1: (1, "B", PointType.U8),
    CatalogType.U2: (2, "H", PointType.U16),
    CatalogType.U4: (4, "I", PointType.U32),
    CatalogType.U8: (8, "Q", PointType.U64),
    CatalogType.F4: (4, "f", PointType.F32),
    CatalogType.F8: (8, "d", PointType.F64),
    CatalogType.BYTE: (1, "B", PointType.U8),
}


class CatalogPoint(BaseModel):
    """카탈로그 한 행. parameter 하나 또는 result 값 하나."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    # 소켓 첫 block 기준 block 위치
    offset: int = Field(ge=0)
    type: str
    scale: float | None = None
    format: ValueFormat | None = None

    _catalog_type: CatalogType = PrivateAttr()
    _length: int = PrivateAttr()

    @model_validator(mode="after")
    def _parse(self) -> "CatalogPoint":
        m = _TYPE_RE.match(self.type.strip().lower())
        if not m or m.group(1) not in CatalogType._value2member_map_:
            raise ValueError(
                f"{self.name}: 알 수 없는 type {self.type!r} (가능: {', '.join(t.value for t in CatalogType)}, 배열은 byte[16])"
            )
        length = int(m.group(2) or 1)
        if length < 1:
            raise ValueError(f"{self.name}: 배열 길이는 1 이상: {self.type!r}")
        self._catalog_type, self._length = CatalogType(m.group(1)), length
        return self

    @property
    def catalog_type(self) -> CatalogType:
        return self._catalog_type

    @property
    def length(self) -> int:
        """배열 길이. 스칼라는 1."""
        return self._length

    @property
    def size(self) -> int:
        """차지하는 block 수."""
        return self._catalog_type.size * self._length

    @property
    def end(self) -> int:
        return self.offset + self.size


class CommandDef(BaseModel):
    """command 하나의 layout. data 가 없으면 결과 없이 동작만 하는 제어(SET) command 다."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    command: int = Field(ge=0, le=MAX_REGISTER)
    command_name: str
    params: tuple[CatalogPoint, ...] = ()
    data: tuple[CatalogPoint, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> "CommandDef":
        names = [p.name for p in self.params + self.data]
        duplicated = sorted({n for n, count in Counter(names).items() if count > 1})
        if duplicated:
            raise ValueError(f"{self.command_name}: name 중복 {duplicated}")
        for p in self.params + self.data:
            if p.offset < COMMAND_BLOCKS:
                raise ValueError(f"{self.command_name}.{p.name}: offset {p.offset} 은 command 자리(0~{COMMAND_BLOCKS - 1})다")
            if p.end > SOCKET_BLOCKS:
                raise ValueError(
                    f"{self.command_name}.{p.name}: offset {p.offset} + {p.size} block 이 "
                    f"소켓 {SOCKET_BLOCKS} block(0~{SOCKET_BLOCKS - 1})을 넘는다"
                )
        # parameter 는 소켓에 써 넣는 값이라 겹치면 서로 덮어쓴다. result 는 같은 자리를 다르게 볼 수 있어 허용한다
        ordered = sorted(self.params, key=lambda p: p.offset)
        for a, b in zip(ordered, ordered[1:]):
            if a.end > b.offset:
                raise ValueError(
                    f"{self.command_name}: parameter {a.name}({a.offset}~{a.end - 1}) 와 "
                    f"{b.name}({b.offset}~{b.end - 1}) 가 겹친다"
                )
        return self

    @property
    def is_control(self) -> bool:
        return not self.data

    @property
    def setup_end(self) -> int:
        """command 와 parameter 가 끝나는 block 위치."""
        return max((p.end for p in self.params), default=COMMAND_BLOCKS)


class CommandCatalog(BaseModel):
    """command_name 으로 찾는 CommandDef 모음."""

    model_config = ConfigDict(extra="forbid")

    source: str
    commands: dict[str, CommandDef]

    def get(self, command_name: str) -> CommandDef:
        command = self.commands.get(command_name)
        if command is None:
            raise ValueError(f"{self.source} 에 없는 command: {command_name!r}")
        return command


class SocketSetup(BaseModel):
    """메모리 맵 SocketEntry 를 카탈로그로 풀어 둔 결과.

    setup_blocks 는 소켓 20 block 중 command·parameter 자리에 있어야 하는 값이고 나머지는 None 이다 (전제조건).
    points 는 result 값을 "{socket}.{point}" 이름으로 바꿔 둔 것이다. offset 은 그대로 소켓 기준 block 이다.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    s_block: int
    # s_block 의 Modbus 레지스터
    base: int
    command: CommandDef
    setup_blocks: tuple[int | None, ...]
    points: tuple[CatalogPoint, ...]

    @property
    def end(self) -> int:
        """소켓 한 자리는 값이 적어도 항상 SOCKET_REGISTERS 개를 차지한다."""
        return self.base + SOCKET_REGISTERS

    @property
    def s_range(self) -> str:
        return f"S{self.s_block:04d}~S{self.s_block + SOCKET_BLOCKS - 1:04d}"

    @property
    def setup_registers(self) -> tuple[int, ...]:
        """base 부터 command·parameter 를 덮는 레지스터 값. 사이의 result 자리는 0 으로 채운다."""
        count = (self.command.setup_end + 1) // 2
        blocks = [b or 0 for b in self.setup_blocks[: count * 2]]
        return tuple(blocks[i] | blocks[i + 1] << 8 for i in range(0, len(blocks), 2))
