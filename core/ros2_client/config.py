import os
from dataclasses import dataclass


@dataclass(slots=True)
class RosClientConfig:
    node_name: str = "collector_ros_client"
    namespace: str = ""
    # None 이면 rcl 이 ROS_DOMAIN_ID 환경변수를 사용한다
    domain_id: int | None = None
    qos_depth: int = 10
    # None 이면 MultiThreadedExecutor 기본값(CPU 코어 수)
    num_threads: int | None = None
    service_timeout: float = 5.0
    shutdown_timeout: float = 5.0

    @classmethod
    def from_env(cls) -> "RosClientConfig":
        defaults = cls()
        num_threads = os.getenv("ROS_CLIENT_NUM_THREADS")
        return cls(
            node_name=os.getenv("ROS_CLIENT_NODE_NAME", defaults.node_name),
            namespace=os.getenv("ROS_CLIENT_NAMESPACE", defaults.namespace),
            qos_depth=int(os.getenv("ROS_CLIENT_QOS_DEPTH", defaults.qos_depth)),
            num_threads=int(num_threads) if num_threads else defaults.num_threads,
            service_timeout=float(os.getenv("ROS_CLIENT_SERVICE_TIMEOUT", defaults.service_timeout)),
            shutdown_timeout=float(os.getenv("ROS_CLIENT_SHUTDOWN_TIMEOUT", defaults.shutdown_timeout)),
        )
