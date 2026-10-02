"""Modbus 스모크 테스트 공통: 시뮬레이터 장비, 가상 시리얼 포트, 점검 시나리오."""
import argparse
import os
import select
import socket
import threading
import time
import tty
from collections.abc import Callable
from dataclasses import dataclass

from _smoke import SmokeRunner, expect
from pydantic import ValidationError
from pymodbus.server import ServerStop, StartSerialServer, StartTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

from core.modbus_client import BaseModbusClient, MemoryMap, ModbusClientError, ModbusErrorCode
from model import MemoryMapPoint, PointType, Value, WordEndian

SIM_SIZE = 100
SIM_HOLDING = 5
SIM_INPUT = 7

READERS: dict[str, Callable[[BaseModbusClient, int, int], list]] = {
    "coil": BaseModbusClient.read_coils,
    "discrete": BaseModbusClient.read_discrete_inputs,
    "holding": BaseModbusClient.read_holding_registers,
    "input": BaseModbusClient.read_input_registers,
}
WRITABLE = ("holding", "coil")


@dataclass(frozen=True)
class DeviceTarget:
    """실장비 점검 대상. holding 은 --address/--type 을 메모리 맵 포인트 하나로 풀어 쓰기에 사용한다."""

    kind: str
    address: int
    count: int
    write: bool = False
    value: str | None = None
    keep: bool = False
    memory_map: MemoryMap | None = None


def add_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--kind", choices=READERS, default="holding")
    parser.add_argument("--address", default="0", help="0-based offset. holding 은 100.5 / 100.8~F / 100.H 비트 구간도 가능")
    parser.add_argument("--count", type=int, help="registers/bits to read (default: size of --type, or 1)")
    parser.add_argument("--type", help="holding value type for --write (memory map type, e.g. u8 i16 u32 f32 i16[3]; default u16)")
    parser.add_argument("--word-endian", choices=[e.value for e in WordEndian], default=WordEndian.LE.value)
    parser.add_argument("--write", action="store_true", help="write/readback/restore on --address (device only)")
    parser.add_argument(
        "--value",
        help="value for --write (comma separated for arrays/coils, 0x.. = raw bits, negative: --value=-1)",
    )
    parser.add_argument("--keep", action="store_true", help="keep the written --value instead of restoring")


def parse_target(parser: argparse.ArgumentParser, args: argparse.Namespace) -> DeviceTarget:
    if args.write and args.kind not in WRITABLE:
        parser.error("--write supports only --kind holding or coil")
    if args.value is not None and not args.write:
        parser.error("--value requires --write")
    if args.keep and args.value is None:
        parser.error("--keep requires --write --value")
    if args.type is not None and args.kind != "holding":
        parser.error("--type supports only --kind holding")
    if args.kind != "holding":
        if not args.address.isdigit():
            parser.error("--address bit range supports only --kind holding")
        address = int(args.address)
        return DeviceTarget(args.kind, address, args.count or 1, args.write, args.value, args.keep)
    try:
        memory_map = MemoryMap.from_dict({
            "word_endian": args.word_endian,
            "holding": [{"addr": args.address, "type": args.type or PointType.U16.value, "name": "target"}],
        })
    except ValidationError as e:
        parser.error("; ".join(err["msg"].removeprefix("Value error, ") for err in e.errors()))
    point = memory_map.settings.holding[0]
    return DeviceTarget(
        args.kind, point.register, args.count or point.register_count, args.write, args.value, args.keep, memory_map
    )


def _sim_device(device_id: int) -> SimDevice:
    """coil=False, discrete=True, holding=5, input=7 로 채운 장비 (레지스터 0~99 번지)."""

    def block(value: int, datatype: DataType) -> list[SimData]:
        return [SimData(0, count=SIM_SIZE, values=value, datatype=datatype)]

    # BITS 블록의 value 는 16비트 워드 마스크다
    return SimDevice(
        id=device_id,
        simdata=(
            block(0x0000, DataType.BITS),
            block(0xFFFF, DataType.BITS),
            block(SIM_HOLDING, DataType.REGISTERS),
            block(SIM_INPUT, DataType.REGISTERS),
        ),
    )


def start_tcp_simulator(host: str, port: int, device_id: int, wait: float = 5.0) -> None:
    threading.Thread(
        target=StartTcpServer,
        kwargs={"context": _sim_device(device_id), "address": (host, port)},
        name="modbus-tcp-sim",
        daemon=True,
    ).start()
    deadline = time.monotonic() + wait
    while True:
        try:
            socket.create_connection((host, port), timeout=0.2).close()
            return
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def start_rtu_simulator(device_id: int, baudrate: int, parity: str, stopbits: int) -> str:
    """pty 두 개를 브리지한 가상 시리얼 라인에 RTU 서버를 띄우고 클라이언트용 포트 경로를 반환한다."""
    server_master, server_slave = os.openpty()
    client_master, client_slave = os.openpty()
    # 슬레이브 fd 는 닫지 않고 유지해야 상대가 닫혀도 브리지가 EIO 로 죽지 않는다
    tty.setraw(server_slave)
    tty.setraw(client_slave)

    def bridge() -> None:
        peers = {server_master: client_master, client_master: server_master}
        while True:
            readable, _, _ = select.select(list(peers), [], [])
            for fd in readable:
                os.write(peers[fd], os.read(fd, 1024))

    threading.Thread(target=bridge, name="virtual-serial-bridge", daemon=True).start()
    threading.Thread(
        target=StartSerialServer,
        kwargs={
            "context": _sim_device(device_id),
            "port": os.ttyname(server_slave),
            "baudrate": baudrate,
            "parity": parity,
            "stopbits": stopbits,
        },
        name="modbus-rtu-sim",
        daemon=True,
    ).start()
    # 시리얼 서버는 준비 완료를 알 수단이 없어 포트를 열 시간을 준다
    time.sleep(1.0)
    return os.ttyname(client_slave)


def stop_simulator() -> None:
    try:
        ServerStop()
    except RuntimeError:
        # 서버가 기동 전에 실패한 경우
        pass


def run_simulator_suite(runner: SmokeRunner, client: BaseModbusClient) -> None:
    """시뮬레이터 대상 읽기/쓰기/에러 매핑 전체 점검."""
    runner.check("connect", client.connect, required=True)
    runner.check("is_connected", lambda: expect(client.is_connected, True))

    runner.check("read_coils", lambda: expect(client.read_coils(0, 8), [False] * 8))
    runner.check("read_discrete_inputs", lambda: expect(client.read_discrete_inputs(0, 8), [True] * 8))
    runner.check("read_holding_registers", lambda: expect(client.read_holding_registers(0, 4), [SIM_HOLDING] * 4))
    runner.check("read_input_registers", lambda: expect(client.read_input_registers(0, 4), [SIM_INPUT] * 4))

    def write_coil() -> list[bool]:
        client.write_coil(1, True)
        return expect(client.read_coils(0, 3), [False, True, False])

    def write_coils() -> list[bool]:
        client.write_coils(4, [True, False, True])
        return expect(client.read_coils(4, 3), [True, False, True])

    def write_register() -> list[int]:
        client.write_register(10, 1234)
        return expect(client.read_holding_registers(10, 1), [1234])

    def write_registers() -> list[int]:
        client.write_registers(20, [1, 2, 65535])
        return expect(client.read_holding_registers(20, 3), [1, 2, 65535])

    runner.check("write_coil + readback", write_coil)
    runner.check("write_coils + readback", write_coils)
    runner.check("write_register + readback", write_register)
    runner.check("write_registers + readback", write_registers)

    def memory_map_read() -> str:
        client.write_registers(30, [0x1234, 0xFF38, 0x0002, 0x0001])
        client.write_registers(40, [1, 0xFFFF, 3])
        memory_map = MemoryMap.from_dict({
            "holding": [
                {"addr": "30.H", "type": "u8", "name": "high"},
                {"addr": "30.0~7", "type": "u8", "name": "low"},
                {"addr": "30.4", "type": "bit", "name": "flag"},
                {"addr": "31", "type": "i16", "name": "temp"},
                {"addr": "31", "type": "i16", "name": "temp_hex", "format": "hex"},
                {"addr": "32", "type": "u32", "name": "counter"},
                {"addr": "40", "type": "i16[3]", "name": "torque"},
            ],
        })
        expect(len(memory_map.plan), 1)
        expect(
            MemoryMap.values(memory_map.read(client)),
            {"high": 0x12, "low": 0x34, "flag": 1, "temp": -200, "temp_hex": "0xFF38", "counter": 65538, "torque": [1, -1, 3]},
        )
        return memory_map.describe_plan()

    def memory_map_split() -> str:
        memory_map = MemoryMap.from_dict({
            "holding": [
                {"addr": SIM_SIZE - 2, "type": "u16", "name": "valid"},
                {"addr": SIM_SIZE + 1, "type": "u16", "name": "invalid"},
            ],
        })
        expect(len(memory_map.plan), 1)
        samples = {s.name: (s.value, s.error) for s in memory_map.read(client)}
        expect(samples, {"valid": (SIM_HOLDING, None), "invalid": (None, ModbusErrorCode.ADDR_OUT_OF_RANGE.value)})
        expect(len(memory_map.plan), 2)
        return memory_map.describe_plan().replace("\n", " | ")

    def memory_map_write() -> str:
        memory_map = MemoryMap.from_dict({
            "holding": [
                {"addr": "50.8~F", "type": "u8", "name": "high"},
                {"addr": "50.l", "type": "i8", "name": "low"},
                {"addr": "50.4", "type": "bit", "name": "flag"},
                {"addr": "51", "type": "i16", "name": "temp", "scale": 0.1},
                {"addr": "52", "type": "u32", "name": "counter"},
                {"addr": "54", "type": "f32", "name": "ratio"},
                {"addr": "56", "type": "i16[3]", "name": "torque"},
                {"addr": "59", "type": "u16", "name": "raw", "format": "hex"},
            ],
        })
        values = {
            "high": 0x12, "low": -2, "flag": 1, "temp": -20.0, "counter": 65538,
            "ratio": 1.5, "torque": [1, -1, 3], "raw": "0xFF38",
        }
        for point in memory_map.settings.holding:
            current = client.read_holding_registers(point.register, point.register_count)
            client.write_registers(point.register, memory_map.encode(point, values[point.name], current))
        expect(
            client.read_holding_registers(50, 10),
            [0x12FE, 0xFF38, 2, 1, 0x0000, 0x3FC0, 1, 0xFFFF, 3, 0xFF38],
        )
        return str(expect(MemoryMap.values(memory_map.read(client)), values))

    def typed_device_write() -> str:
        u32 = MemoryMap.from_dict({"holding": [{"addr": "60", "type": "u32", "name": "target"}]})
        restored = _write_holding(client, u32, "70000", keep=False)
        expect(client.read_holding_registers(60, 2), [SIM_HOLDING, SIM_HOLDING])
        u8 = MemoryMap.from_dict({"holding": [{"addr": "62.8~F", "type": "u8", "name": "target"}]})
        kept = _write_holding(client, u8, "0xAB", keep=True)
        expect(client.read_holding_registers(62), [0xAB00 | SIM_HOLDING])
        return f"{restored} | {kept}"

    runner.check("memory map batch read + decode", memory_map_read)
    runner.check("memory map range rejected -> split + per-point error", memory_map_split)
    runner.check("memory map encode -> write -> decode", memory_map_write)
    runner.check("typed write/readback/restore + keep", typed_device_write)

    runner.expect_raises(
        "out-of-range read -> ADDR_OUT_OF_RANGE",
        ModbusClientError,
        lambda: client.read_holding_registers(SIM_SIZE - 1, 2),
        lambda e: e.code is ModbusErrorCode.ADDR_OUT_OF_RANGE,
    )
    runner.expect_raises(
        "out-of-range write -> ADDR_OUT_OF_RANGE",
        ModbusClientError,
        lambda: client.write_registers(SIM_SIZE - 1, [0, 0]),
        lambda e: e.code is ModbusErrorCode.ADDR_OUT_OF_RANGE,
    )

    _check_reconnect(runner, client, "holding", 0)


def run_device_suite(runner: SmokeRunner, client: BaseModbusClient, target: DeviceTarget) -> None:
    """실장비 점검. 기본은 읽기 전용이며 write=True 일 때만 쓰고, keep=True 가 아니면 원래 값으로 복원한다."""
    kind, address, count = target.kind, target.address, target.count
    runner.check("connect", client.connect, required=True)
    runner.check(f"read {kind} [{address}..{address + count - 1}]", lambda: READERS[kind](client, address, count))
    if target.write:
        def write() -> str:
            if target.memory_map is not None:
                return _write_holding(client, target.memory_map, target.value, target.keep)
            return _write_coils(client, address, target.value, target.keep)

        point = f"{kind}[{target.memory_map.settings.holding[0].addr}]" if target.memory_map else f"{kind}[{address}]"
        runner.check(f"write/readback/{'keep' if target.keep else 'restore'} {point}", write)
    _check_reconnect(runner, client, kind, address)


def expect_connect_failed(runner: SmokeRunner, client: BaseModbusClient) -> None:
    runner.expect_raises(
        "unreachable target -> CONNECT_FAILED (requires_reconnect)",
        ModbusClientError,
        client.connect,
        lambda e: e.code is ModbusErrorCode.CONNECT_FAILED and e.code.requires_reconnect,
    )


def _check_reconnect(runner: SmokeRunner, client: BaseModbusClient, kind: str, address: int) -> None:
    def reconnect() -> list:
        client.close()
        expect(client.is_connected, False)
        client.connect()
        return READERS[kind](client, address, 1)

    def close() -> None:
        client.close()
        expect(client.is_connected, False)

    runner.check("close + reconnect + read", reconnect)
    runner.check("close", close)


def _write_holding(client: BaseModbusClient, memory_map: MemoryMap, value: str | None, keep: bool) -> str:
    """메모리 맵 포인트 하나를 타입에 맞게 인코딩해 쓴다. value 가 없으면 최하위 비트만 뒤집어 본다."""
    point = memory_map.settings.holding[0]

    def read() -> list[int]:
        return client.read_holding_registers(point.register, point.register_count)

    def write(registers: list[int]) -> None:
        if len(registers) == 1:
            client.write_register(point.register, registers[0])
        else:
            client.write_registers(point.register, registers)

    original = read()
    if value is None:
        written = [original[0] ^ (1 << (point.bits[0] if point.bits else 0)), *original[1:]]
    else:
        written = memory_map.encode(point, _parse_value(point, value), original)
    _roundtrip(read, write, original, written, keep)
    before, after = _format(memory_map.decode(point, original)), _format(memory_map.decode(point, written))
    if keep:
        return f"{before} -> {after} (kept, restore with --value {before})"
    return f"{before} -> {after} -> {before}"


def _write_coils(client: BaseModbusClient, address: int, value: str | None, keep: bool) -> str:
    count = 1 if value is None else len(value.split(","))

    def read() -> list[bool]:
        return client.read_coils(address, count)

    def write(bits: list[bool]) -> None:
        if len(bits) == 1:
            client.write_coil(address, bits[0])
        else:
            client.write_coils(address, bits)

    original = read()
    written = [not b for b in original] if value is None else [_parse_bit(s) for s in value.split(",")]
    _roundtrip(read, write, original, written, keep)
    before, after = _format([int(b) for b in original]), _format([int(b) for b in written])
    if keep:
        return f"{before} -> {after} (kept, restore with --value {before})"
    return f"{before} -> {after} -> {before}"


def _roundtrip(read: Callable[[], list], write: Callable[[list], None], original: list, written: list, keep: bool) -> None:
    try:
        write(written)
        expect(read(), written)
    finally:
        if not keep:
            write(original)
    if not keep:
        expect(read(), original)


def _parse_value(point: MemoryMapPoint, text: str) -> Value:
    items: list[int | float | str] = []
    for item in (s.strip() for s in text.split(",")):
        if item.lower().startswith("0x"):
            items.append(item)
        elif point.point_type in (PointType.F32, PointType.F64):
            items.append(float(item))
        else:
            items.append(int(item))
    return items[0] if len(items) == 1 and point.length == 1 else items


def _parse_bit(text: str) -> bool:
    text = text.strip().lower()
    if text in ("1", "true", "on"):
        return True
    if text in ("0", "false", "off"):
        return False
    raise ValueError(f"coil value must be 0/1: {text!r}")


def _format(value: Value | list[int]) -> str:
    return ",".join(map(str, value)) if isinstance(value, list) else str(value)
