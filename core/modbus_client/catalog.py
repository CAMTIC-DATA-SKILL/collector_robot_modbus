"""command 카탈로그 CSV 로더. 스키마는 model.catalog_model 참고.

CSV 한 행이 parameter 하나 또는 result 값 하나다. command 하나가 여러 행을 차지하므로
command / command_name 은 행마다 반복해서 적는다 (docs/command_catalog.example.csv 참고).

    command,command_name,section,offset,type,name,scale,format,unit,description
    120,GET_AXIS_INFO,param,2,s2,type,1,decimal,,0: 현재 위치 1: 지령 위치
    120,GET_AXIS_INFO,param,4,s2,start_axis_no,1,decimal,,시작 축 번호
    120,GET_AXIS_INFO,data,8,f4,axis_1,1,decimal,deg,

- offset: 소켓 첫 block(command)을 0 으로 센 block 위치. 매뉴얼 표의 위치를 그대로 적는다.
- type: HI6 표기 s1 s2 s4 s8 u1 u2 u4 u8 f4 f8 byte (숫자는 byte 수). 배열은 byte[16] 처럼 쓴다.
- param 행 순서가 메모리 맵 sockets 의 params 값 순서다. data 행이 없으면 제어(SET) command 다.
- parameter 값에 따라 result 배치가 바뀌는 command 는 command_name 을 나눠 같은 command 번호를 쓴다
  (GET_CIFX_STATUS_1 ~ _5 = 1000).
- scale: 비우거나 1 이면 원시 값 그대로(정수는 정수로) 낸다.
- format: dec / hex / bin. 매뉴얼 표기 decimal / hexadecimal / binary 도 받는다.
- unit / description 과 그 밖의 컬럼: 사람이 보는 값으로 수집에는 쓰지 않는다.
"""
import csv
import logging
from pathlib import Path

from pydantic import ValidationError

from model import CatalogPoint, CommandCatalog, CommandDef

logger = logging.getLogger(__name__)

REQUIRED_COLUMNS = ("command", "command_name", "section", "offset", "type", "name")
SECTION_PARAM = "param"
SECTION_DATA = "data"
_SECTIONS = (SECTION_PARAM, SECTION_DATA)
_FORMAT_ALIASES = {"decimal": "dec", "hexadecimal": "hex", "binary": "bin"}


def load_catalog(path: str | Path) -> CommandCatalog:
    """CSV 를 읽어 카탈로그로 만든다. 형식 오류는 행 번호를 붙여 한 번에 모아 ValueError 로 올린다."""
    path = Path(path)
    # 엑셀이 저장한 BOM 을 떼고 읽는다
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or ())]
        if missing:
            raise ValueError(f"{path}: 필요한 컬럼이 없다 {missing} (있는 컬럼: {reader.fieldnames})")
        rows = [(i, row) for i, row in enumerate(reader, start=2) if any(_cell(row, c) for c in REQUIRED_COLUMNS)]

    if not rows:
        raise ValueError(f"{path}: 데이터 행이 없다")

    # 수백 개 command 를 한 줄씩 고치지 않도록 오류를 모두 모아 한 번에 알린다
    errors: list[str] = []
    numbers: dict[str, int] = {}
    first_lines: dict[str, int] = {}
    sections: dict[str, dict[str, list[CatalogPoint]]] = {}
    for line, row in rows:
        try:
            name, number, section, point = _row(row)
        except ValueError as e:
            errors.append(f"{path}:{line}: {e}")
            continue
        first_lines.setdefault(name, line)
        if numbers.setdefault(name, number) != number:
            errors.append(f"{path}:{line}: {name} 의 command 가 {numbers[name]} 와 {number} 로 다르다")
            continue
        sections.setdefault(name, {s: [] for s in _SECTIONS})[section].append(point)

    commands = {}
    for name, by_section in sections.items():
        try:
            commands[name] = CommandDef(
                command=numbers[name],
                command_name=name,
                params=tuple(by_section[SECTION_PARAM]),
                data=tuple(by_section[SECTION_DATA]),
            )
        except ValidationError as e:
            errors.append(f"{path}:{first_lines[name]}: {_messages(e)}")

    if errors:
        raise ValueError(f"카탈로그 오류 {len(errors)}건\n" + "\n".join(errors))
    logger.info("command catalog %s: %d commands", path.name, len(commands))
    return CommandCatalog(source=path.name, commands=commands)


def _row(row: dict[str, str | None]) -> tuple[str, int, str, CatalogPoint]:
    name = _cell(row, "command_name")
    if not name:
        raise ValueError("command_name 이 비었다")
    section = _cell(row, "section").lower()
    if section not in _SECTIONS:
        raise ValueError(f"section 은 {' / '.join(_SECTIONS)} 중 하나 ({section!r})")
    if not _cell(row, "name"):
        raise ValueError("name 이 비었다")
    return name, _int(row, "command"), section, _point(row)


def _cell(row: dict[str, str | None], column: str) -> str:
    return (row.get(column) or "").strip()


def _int(row: dict[str, str | None], column: str) -> int:
    text = _cell(row, column)
    try:
        return int(text, 0)
    except ValueError as e:
        raise ValueError(f"{column} 는 정수여야 한다 ({text!r})") from e


def _point(row: dict[str, str | None]) -> CatalogPoint:
    scale_text = _cell(row, "scale")
    try:
        scale = float(scale_text) if scale_text else None
    except ValueError as e:
        raise ValueError(f"scale 은 숫자여야 한다 ({scale_text!r})") from e
    fmt = _cell(row, "format").lower()
    try:
        return CatalogPoint(
            name=_cell(row, "name"),
            offset=_int(row, "offset"),
            type=_cell(row, "type"),
            # scale 1 을 곱하면 정수가 실수로 바뀌므로 적용하지 않는다
            scale=None if scale == 1 else scale,
            format=_FORMAT_ALIASES.get(fmt, fmt) or None,
        )
    except ValidationError as e:
        raise ValueError(_messages(e)) from e


def _messages(e: ValidationError) -> str:
    return "; ".join(err["msg"].removeprefix("Value error, ") for err in e.errors())
