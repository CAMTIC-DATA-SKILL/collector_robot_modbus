"""게이트웨이 ↔ collector 프로토콜 DTO. collector-plc 와이어와 같다.

ZeroMQ multipart [topic, ProtocolHeaderDTO JSON] 이고 msg_body 에 msg_type 별 본문을 담는다.
"""
from enum import Enum
from uuid import UUID

from pydantic import BaseModel

from .memory_map_model import PointType, RegisterKind
from .sample_model import Value


class MsgTypeEnum(str, Enum):
    CMD_R = "cmd_r"
    CMD_W = "cmd_w"
    ACK = "ack"
    HEALTH = "health"
    DATA = "data"


class MsgCmdREnum(str, Enum):
    SET_SCAN = "SET_SCAN"
    SET_DEVICE = "SET_DEVICE"
    READ_ONCE = "READ_ONCE"
    STOP = "STOP"


class MsgCmdWEnum(str, Enum):
    CONTROL_COMMAND = "CONTROL_COMMAND"
    INSERT = "INSERT"


class MsgAckStatusEnum(str, Enum):
    OK = "ok"
    # 요청 자체의 결함 (E-4xxx)
    REJECTED = "rejected"
    ERROR = "error"


class AckCode(str, Enum):
    """ACK code. 값은 collector-plc ClientErrorCode 와 같다."""

    OK = "S-0000"
    CMD_BODY_INVALID = "E-4101"
    CMD_UNSUPPORTED_ACTION = "E-4102"


class ProtocolHeaderDTO(BaseModel):
    msg_id: UUID
    # 게이트웨이가 채운 값. ACK 응답 시 요청 헤더 값을 그대로 에코한다
    gateway_address: str
    # DEVICE_KEY. 토픽 collector.robot.{collector_address}.{msg_type} 세그먼트와 같다
    collector_address: str
    msg_type: MsgTypeEnum
    msg_body: dict
    timestamp_ms: int


class MsgAckDTO(BaseModel):
    ref_msg_id: UUID
    device_key: str
    action: MsgCmdREnum | MsgCmdWEnum
    status: MsgAckStatusEnum
    code: AckCode = AckCode.OK
    reason: str | None = None
    applied_ms: int | None = None


class MsgHealthDTO(BaseModel):
    device_key: str
    ipc_ok: bool
    device_ok: bool
    # 장비 이상 시 ModbusErrorCode 값 (예: "E-1001")
    reason: str | None = None
    observed_ms: int


class MsgSampleDTO(BaseModel):
    """Sample 과 같은 필드. 실패하면 value 는 None, error 에 ModbusErrorCode 값."""

    name: str
    kind: RegisterKind
    addr: str
    type: PointType
    value: Value | None = None
    error: str | None = None


class MsgDataDTO(BaseModel):
    device_key: str
    sample_ms: int
    samples: list[MsgSampleDTO]
