"""모든 환경 변수는 여기서만 읽는다. 저장소 루트의 .env 를 먼저 불러온다 (.env.example 참고).

    from config import settings
    client = ModbusTcpClient(settings.modbus_tcp)
"""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import load_dotenv

from core.modbus_client.config import ModbusRtuConfig, ModbusTcpConfig
from core.zeromq_client.config import ZmqConfig

if TYPE_CHECKING:
    from core.ros2_client.config import RosClientConfig

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _env_str(key: str, default: str) -> str:
    value = os.getenv(key)
    return value.strip() if value is not None and value.strip() else default


def _env_int(key: str, default: int) -> int:
    return int(_env_str(key, str(default)))


def _env_float(key: str, default: float) -> float:
    return float(_env_str(key, str(default)))


def _env_bool(key: str, default: bool) -> bool:
    return _env_str(key, str(default)).lower() in ("1", "true", "yes", "on")


def _env_path(key: str, default: str) -> Path:
    path = Path(_env_str(key, default))
    return path if path.is_absolute() else BASE_DIR / path


@dataclass(slots=True)
class Settings:
    log_level: str
    # 프로토콜 collector_address / device_key, ZeroMQ 토픽 collector.robot.{device_key}.* 세그먼트
    device_key: str
    # data / health 헤더의 gateway_address. ack 는 요청 헤더 값을 에코한다
    gateway_address: str
    # tcp | rtu
    modbus_mode: str
    memory_map_file: Path
    poll_interval: float
    reconnect_interval: float
    modbus_tcp: ModbusTcpConfig
    modbus_rtu: ModbusRtuConfig
    zmq: ZmqConfig
    # 표시용. rcl 은 이 값을 환경 변수에서 직접 읽는다
    ros_domain_id: str


def load_settings() -> Settings:
    modbus_mode = _env_str("MODBUS_MODE", "tcp").lower()
    if modbus_mode not in ("tcp", "rtu"):
        raise ValueError(f"MODBUS_MODE 는 tcp 또는 rtu: {modbus_mode!r}")
    device_key = _env_str("DEVICE_KEY", "robot-1")
    if "." in device_key:
        raise ValueError(f"DEVICE_KEY 에 '.' 는 쓸 수 없다 (토픽 계층 구분자): {device_key!r}")
    tcp, rtu, zmq = ModbusTcpConfig(), ModbusRtuConfig(), ZmqConfig()
    return Settings(
        log_level=_env_str("LOG_LEVEL", "INFO").upper(),
        device_key=device_key,
        gateway_address=_env_str("GATEWAY_ADDRESS", "gateway"),
        modbus_mode=modbus_mode,
        memory_map_file=_env_path("MEMORY_MAP_FILE", "memory_map.json"),
        poll_interval=_env_float("POLL_INTERVAL_SEC", 1.0),
        reconnect_interval=_env_float("RECONNECT_INTERVAL_SEC", 5.0),
        modbus_tcp=ModbusTcpConfig(
            device_id=_env_int("MODBUS_TCP_DEVICE_ID", tcp.device_id),
            timeout=_env_float("MODBUS_TCP_TIMEOUT", tcp.timeout),
            retries=_env_int("MODBUS_TCP_RETRIES", tcp.retries),
            host=_env_str("MODBUS_TCP_HOST", tcp.host),
            port=_env_int("MODBUS_TCP_PORT", tcp.port),
        ),
        modbus_rtu=ModbusRtuConfig(
            device_id=_env_int("MODBUS_RTU_DEVICE_ID", rtu.device_id),
            timeout=_env_float("MODBUS_RTU_TIMEOUT", rtu.timeout),
            retries=_env_int("MODBUS_RTU_RETRIES", rtu.retries),
            port=_env_str("MODBUS_RTU_PORT", rtu.port),
            baudrate=_env_int("MODBUS_RTU_BAUDRATE", rtu.baudrate),
            bytesize=_env_int("MODBUS_RTU_BYTESIZE", rtu.bytesize),
            parity=_env_str("MODBUS_RTU_PARITY", rtu.parity).upper(),
            stopbits=_env_int("MODBUS_RTU_STOPBITS", rtu.stopbits),
            handle_local_echo=_env_bool("MODBUS_RTU_LOCAL_ECHO", rtu.handle_local_echo),
        ),
        zmq=ZmqConfig(
            pub_endpoint=_env_str("ZMQ_PUB_ENDPOINT", zmq.pub_endpoint),
            sub_endpoint=_env_str("ZMQ_SUB_ENDPOINT", zmq.sub_endpoint),
            pub_bind=_env_bool("ZMQ_PUB_BIND", zmq.pub_bind),
            sub_bind=_env_bool("ZMQ_SUB_BIND", zmq.sub_bind),
            recv_timeout_ms=_env_int("ZMQ_RECV_TIMEOUT_MS", zmq.recv_timeout_ms),
            linger_ms=_env_int("ZMQ_LINGER_MS", zmq.linger_ms),
        ),
        ros_domain_id=_env_str("ROS_DOMAIN_ID", "0"),
    )


def ros_client_config() -> "RosClientConfig":
    # core.ros2_client 는 import 시 rclpy 를 불러오므로 ROS 환경에서 필요할 때만 만든다
    from core.ros2_client.config import RosClientConfig

    defaults = RosClientConfig()
    num_threads = _env_str("ROS_CLIENT_NUM_THREADS", "")
    return RosClientConfig(
        node_name=_env_str("ROS_CLIENT_NODE_NAME", defaults.node_name),
        namespace=_env_str("ROS_CLIENT_NAMESPACE", defaults.namespace),
        qos_depth=_env_int("ROS_CLIENT_QOS_DEPTH", defaults.qos_depth),
        num_threads=int(num_threads) if num_threads else defaults.num_threads,
        service_timeout=_env_float("ROS_CLIENT_SERVICE_TIMEOUT", defaults.service_timeout),
        shutdown_timeout=_env_float("ROS_CLIENT_SHUTDOWN_TIMEOUT", defaults.shutdown_timeout),
    )


settings = load_settings()
