"""JSON 메모리 맵으로 읽기 요청을 자동으로 묶고 값을 디코딩한다. 스키마는 model.memory_map_model 참고.

    {
      "word_endian": "le",
      "max_gap": 8,
      "format": "dec",
      "holding": [
        {"addr": "0.8~F", "type": "u8",     "name": "status_code"},
        {"addr": "1",     "type": "i16",    "name": "temperature", "scale": 0.1},
        {"addr": "5",     "type": "i16[6]", "name": "joint_torque"},
        {"addr": "11",    "type": "u32",    "name": "run_counter", "format": "hex"}
      ],
      "input": [{"addr": "100", "type": "f32", "name": "battery_voltage"}]
    }

- word_endian: 2레지스터 이상 값의 순서. le = 앞 레지스터가 하위 워드 (collector-plc 와 동일).
- max_gap: 빈 번지가 이 개수 이하면 같은 요청으로 묶는다. 장비가 범위를 거부하면 자동으로 쪼개 다시 읽는다.
- format: dec | hex. 포인트별로 덮어쓸 수 있다. hex 는 scale 을 적용하지 않은 원시 비트를 "0xFF38" 처럼 낸다.
"""
import json
import logging
import struct
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from model import (
    MAX_READ_REGISTERS,
    MemoryMapPoint,
    MemoryMapSettings,
    PointType,
    ReadRequest,
    RegisterKind,
    Sample,
    Value,
    ValueFormat,
    WordEndian,
)

from .base_client import BaseModbusClient
from .exceptions import ModbusClientError, ModbusErrorCode

logger = logging.getLogger(__name__)

_READERS = {
    RegisterKind.HOLDING: BaseModbusClient.read_holding_registers,
    RegisterKind.INPUT: BaseModbusClient.read_input_registers,
}


class MemoryMap:
    def __init__(self, settings: MemoryMapSettings) -> None:
        self.settings = settings
        self.plan = tuple(
            r for kind in RegisterKind for r in _plan(kind, settings.points_of(kind), settings.max_gap)
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "MemoryMap":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MemoryMap":
        return cls(MemoryMapSettings.model_validate(raw))

    def read(self, client: BaseModbusClient) -> list[Sample]:
        """계획된 요청으로 전체 포인트를 읽는다. 연결 계열 에러만 예외로 올리고 나머지는 Sample.error 에 담는다."""
        samples: dict[str, Sample] = {}
        plan: list[ReadRequest] = []
        for request in self.plan:
            plan += self._execute(client, request, samples)
        # 범위 거부로 쪼갠 계획은 다음 읽기부터 그대로 쓴다
        self.plan = tuple(plan)
        return [samples[p.name] for kind in RegisterKind for p in self.settings.points_of(kind)]

    @staticmethod
    def values(samples: list[Sample]) -> dict[str, Value]:
        """성공한 포인트만 {name: value} 로 모은다."""
        return {s.name: s.value for s in samples if s.error is None}

    def describe_plan(self) -> str:
        return "\n".join(
            f"{r.kind.value} {r.start}~{r.end - 1} ({r.count} regs): {', '.join(p.name for p in r.points)}"
            for r in self.plan
        )

    def decode(self, point: MemoryMapPoint, registers: list[int]) -> Value:
        """포인트가 차지하는 레지스터만 받아 값으로 바꾼다."""
        if point.bits:
            lo, hi = point.bits
            return self._convert(point, (registers[0] >> lo) & ((1 << (hi - lo + 1)) - 1))
        words_per_item = point.point_type.width // 16
        items = []
        for i in range(point.length):
            words = registers[i * words_per_item : (i + 1) * words_per_item]
            if self.settings.word_endian is WordEndian.LE:
                words = words[::-1]
            items.append(self._convert(point, int.from_bytes(b"".join(w.to_bytes(2, "big") for w in words), "big")))
        return items[0] if point.length == 1 else items

    def _execute(self, client: BaseModbusClient, request: ReadRequest, samples: dict[str, Sample]) -> list[ReadRequest]:
        try:
            registers = _read_registers(client, request)
        except ModbusClientError as e:
            if e.code.requires_reconnect:
                raise
            parts = _plan(request.kind, request.points, -1) if e.code is ModbusErrorCode.ADDR_OUT_OF_RANGE else []
            if len(parts) > 1:
                logger.warning(
                    "범위 거부로 요청 분할: %s %d~%d -> %d개",
                    request.kind.value, request.start, request.end - 1, len(parts),
                )
                return [r for part in parts for r in self._execute(client, part, samples)]
            for p in request.points:
                samples[p.name] = _sample(request.kind, p, error=e.code.value)
            return [request]
        for p in request.points:
            offset = p.register - request.start
            samples[p.name] = _sample(request.kind, p, value=self.decode(p, registers[offset : offset + p.register_count]))
        return [request]

    def _convert(self, point: MemoryMapPoint, raw: int) -> int | float | str:
        point_type = point.point_type
        width = point.bits[1] - point.bits[0] + 1 if point.bits else point_type.width
        if (point.format or self.settings.format) is ValueFormat.HEX:
            return f"0x{raw:0{(width + 3) // 4}X}"
        if point_type.struct_format:
            value = struct.unpack(">" + point_type.struct_format, raw.to_bytes(width // 8, "big"))[0]
        elif point_type is PointType.I8 and raw & 0x80:
            value = raw - 0x100
        else:
            value = raw
        return value if point.scale is None else value * point.scale


def _sample(kind: RegisterKind, point: MemoryMapPoint, value: Value | None = None, error: str | None = None) -> Sample:
    return Sample(name=point.name, kind=kind, addr=point.addr, type=point.point_type, value=value, error=error)


def _plan(kind: RegisterKind, points: Iterable[MemoryMapPoint], max_gap: int) -> list[ReadRequest]:
    """주소순 정렬 후, 간격이 max_gap 이하이고 125 레지스터를 넘지 않으면 한 요청으로 묶는다.

    max_gap=-1 이면 레지스터를 공유하는 포인트끼리만 묶는다 (범위 거부 시 분할용).
    """
    requests: list[ReadRequest] = []
    group: list[MemoryMapPoint] = []
    start = end = 0
    for p in sorted(points, key=lambda p: p.register):
        if group and p.register - end <= max_gap and max(end, p.end) - start <= MAX_READ_REGISTERS:
            group.append(p)
            end = max(end, p.end)
            continue
        if group:
            requests.append(ReadRequest(kind=kind, start=start, count=end - start, points=tuple(group)))
        group, start, end = [p], p.register, p.end
    if group:
        requests.append(ReadRequest(kind=kind, start=start, count=end - start, points=tuple(group)))
    return requests


def _read_registers(client: BaseModbusClient, request: ReadRequest) -> list[int]:
    read = _READERS[request.kind]
    registers: list[int] = []
    for start in range(request.start, request.end, MAX_READ_REGISTERS):
        registers += read(client, start, min(MAX_READ_REGISTERS, request.end - start))
    return registers
