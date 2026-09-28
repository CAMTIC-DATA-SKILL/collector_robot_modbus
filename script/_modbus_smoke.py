"""Modbus 스모크 테스트 공통: 시뮬레이터 장비, 가상 시리얼 포트, 점검 시나리오."""
import os
import select
import socket
import threading
import time
import tty
from collections.abc import Callable

from _smoke import SmokeRunner, expect
from pymodbus.server import ServerStop, StartSerialServer, StartTcpServer
from pymodbus.simulator import DataType, SimData, SimDevice

from core.modbus_tcp_client import BaseModbusClient, ModbusClientError, ModbusErrorCode

SIM_SIZE = 100
SIM_HOLDING = 5
SIM_INPUT = 7

READERS: dict[str, Callable[[BaseModbusClient, int, int], list]] = {
    "coil": BaseModbusClient.read_coils,
    "discrete": BaseModbusClient.read_discrete_inputs,
    "holding": BaseModbusClient.read_holding_registers,
    "input": BaseModbusClient.read_input_registers,
}


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


def run_device_suite(
    runner: SmokeRunner,
    client: BaseModbusClient,
    kind: str,
    address: int,
    count: int,
    write: bool,
) -> None:
    """실장비 점검. 기본은 읽기 전용이며 write=True 일 때만 쓰기 후 원래 값으로 복원한다."""
    runner.check("connect", client.connect, required=True)
    runner.check(f"read {kind} [{address}..{address + count - 1}]", lambda: READERS[kind](client, address, count))
    if write:
        runner.check(f"write/readback/restore {kind}[{address}]", lambda: _write_roundtrip(client, kind, address))
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


def _write_roundtrip(client: BaseModbusClient, kind: str, address: int) -> str:
    if kind == "holding":
        original = client.read_holding_registers(address)[0]
        probe = original ^ 0x0001
        try:
            client.write_register(address, probe)
            expect(client.read_holding_registers(address), [probe])
        finally:
            client.write_register(address, original)
        expect(client.read_holding_registers(address), [original])
        return f"{original} -> {probe} -> {original}"
    if kind == "coil":
        original = client.read_coils(address)[0]
        try:
            client.write_coil(address, not original)
            expect(client.read_coils(address), [not original])
        finally:
            client.write_coil(address, original)
        expect(client.read_coils(address), [original])
        return f"{original} -> {not original} -> {original}"
    raise ValueError(f"'{kind}' is read-only (use --kind holding or coil with --write)")
