"""HI6 S relay socket 의 block 단위 인코딩/디코딩과 메모리 맵 sockets 펼치기. 스키마는 model.catalog_model 참고.

소켓 한 자리(10 레지스터)를 20 block 바이트열로 펼쳐 다룬다. 레지스터 하나는 [하위 바이트, 상위 바이트] 순서라
짝수 S block 이 .L, 홀수 S block 이 .H 이고, 여러 block 값은 이 바이트열을 little endian 으로 읽는다.
"""
import struct
from collections.abc import Sequence

from model import (
    COMMAND_BLOCKS,
    MAX_REGISTER,
    SOCKET_BLOCKS,
    SOCKET_REGISTERS,
    CatalogPoint,
    CommandCatalog,
    SocketEntry,
    SocketSetup,
    Value,
    ValueFormat,
)

from .base_client import BaseModbusClient

# 생성되는 result 이름 "{socket}.{point}" 의 구분자
SOCKET_NAME_SEPARATOR = "."


def registers_to_blocks(registers: Sequence[int]) -> bytes:
    return b"".join(r.to_bytes(2, "little") for r in registers)


def blocks_to_registers(blocks: bytes) -> list[int]:
    if len(blocks) % 2:
        raise ValueError(f"block 수가 홀수다 ({len(blocks)})")
    return [int.from_bytes(blocks[i : i + 2], "little") for i in range(0, len(blocks), 2)]


def decode_point(point: CatalogPoint, blocks: bytes) -> Value:
    """소켓 20 block 에서 포인트 값을 꺼낸다."""
    size = point.catalog_type.size
    items = []
    for i in range(point.length):
        start = point.offset + i * size
        items.append(_convert(point, blocks[start : start + size]))
    return items[0] if point.length == 1 else items


def encode_point(point: CatalogPoint, value: Value) -> bytes:
    """decode_point 의 역. "0x.." / "0b.." 문자열은 scale 없이 원시 비트로 쓴다."""
    items = value if isinstance(value, list) else [value]
    if len(items) != point.length:
        raise ValueError(f"{point.name}: 값 {len(items)}개, 필요 {point.length}개")
    return b"".join(_to_bytes(point, item) for item in items)


def expand_socket(entry: SocketEntry, catalog: CommandCatalog, s_relay_base: int) -> SocketSetup:
    """소켓 배치를 카탈로그로 풀어 전제조건 block 과 result 포인트로 바꾼다."""
    command = catalog.get(entry.command)
    if command.is_control:
        raise ValueError(
            f"소켓 {entry.name}: {entry.command} 는 result 가 없는 제어 command 라 수집 소켓에 쓸 수 없다"
        )
    if len(entry.params) != len(command.params):
        raise ValueError(
            f"소켓 {entry.name}: parameter {len(entry.params)}개를 적었지만 "
            f"{entry.command} 는 {len(command.params)}개가 필요하다 "
            f"({', '.join(p.name for p in command.params) or '없음'})"
        )
    base = s_relay_base + entry.s_block // 2
    if base + SOCKET_REGISTERS - 1 > MAX_REGISTER:
        raise ValueError(f"소켓 {entry.name}: 레지스터 범위 초과 ({base}~{base + SOCKET_REGISTERS - 1})")

    setup: list[int | None] = [None] * SOCKET_BLOCKS
    setup[:COMMAND_BLOCKS] = command.command.to_bytes(COMMAND_BLOCKS, "little")
    for point, value in zip(command.params, entry.params):
        try:
            setup[point.offset : point.end] = encode_point(point, value)
        except (ValueError, OverflowError, struct.error) as e:
            raise ValueError(f"소켓 {entry.name}: parameter {point.name}={value!r} -> {e}") from e

    return SocketSetup(
        name=entry.name,
        s_block=entry.s_block,
        base=base,
        command=command,
        setup_blocks=tuple(setup),
        points=tuple(p.model_copy(update={"name": f"{entry.name}{SOCKET_NAME_SEPARATOR}{p.name}"}) for p in command.data),
    )


def setup_mismatch(socket: SocketSetup, blocks: bytes) -> list[int]:
    """command / parameter 자리 중 기대값과 다른 block 위치."""
    return [i for i, expected in enumerate(socket.setup_blocks) if expected is not None and blocks[i] != expected]


def write_socket(client: BaseModbusClient, socket: SocketSetup) -> None:
    """HI6 는 command 를 먼저 쓰고 parameter 를 써야 한다."""
    command, *params = socket.setup_registers
    client.write_register(socket.base, command)
    if params:
        client.write_registers(socket.base + 1, params)


def describe_socket(socket: SocketSetup) -> str:
    command = socket.command
    setup_end = socket.base + len(socket.setup_registers) - 1
    points = ", ".join(f"{p.name}(S{socket.s_block + p.offset:04d} {p.type})" for p in socket.points)
    return (
        f"socket {socket.name} {socket.s_range} -> holding {socket.base}~{socket.end - 1} "
        f"{command.command_name}(={command.command}): setup {socket.base}~{setup_end}={list(socket.setup_registers)}, "
        f"result: {points}"
    )


def _format(point: CatalogPoint) -> ValueFormat:
    return point.format or ValueFormat.DEC


def _convert(point: CatalogPoint, chunk: bytes) -> int | float | str:
    point_type = point.catalog_type
    fmt = _format(point)
    if fmt is ValueFormat.HEX:
        return f"0x{int.from_bytes(chunk, 'little'):0{len(chunk) * 2}X}"
    if fmt is ValueFormat.BIN:
        return f"0b{int.from_bytes(chunk, 'little'):0{len(chunk) * 8}b}"
    value = struct.unpack("<" + point_type.struct_format, chunk)[0]
    return value if point.scale is None else value * point.scale


def _to_bytes(point: CatalogPoint, value: int | float | str) -> bytes:
    point_type = point.catalog_type
    size = point_type.size
    if isinstance(value, str):
        text = value.strip().lower()
        raw = int(text, 2 if text.startswith("0b") else 16)
        if not 0 <= raw < 1 << (size * 8):
            raise ValueError(f"{point.name}: {value} 는 {size} byte 를 넘는다")
        return raw.to_bytes(size, "little")
    if point_type.is_float:
        return struct.pack("<" + point_type.struct_format, value if point.scale is None else value / point.scale)
    if point.scale is not None:
        value = round(value / point.scale)
    elif isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{point.name}: {point_type.value} 에 소수 {value} 는 쓸 수 없다")
    value = int(value)
    low = -(1 << (size * 8 - 1)) if point_type.signed else 0
    high = low + (1 << (size * 8)) - 1
    if not low <= value <= high:
        raise ValueError(f"{point.name}: {value} 는 {point_type.value} 범위({low}~{high}) 밖")
    return struct.pack("<" + point_type.struct_format, value)
