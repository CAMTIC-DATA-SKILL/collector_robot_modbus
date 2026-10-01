from dataclasses import dataclass


@dataclass(slots=True)
class RosClientConfig:
    """환경 변수 값은 루트 config.py 에서 채운다."""

    node_name: str = "collector_ros_client"
    namespace: str = ""
    # None 이면 rcl 이 ROS_DOMAIN_ID 환경변수를 사용한다
    domain_id: int | None = None
    qos_depth: int = 10
    # None 이면 MultiThreadedExecutor 기본값(CPU 코어 수)
    num_threads: int | None = None
    service_timeout: float = 5.0
    shutdown_timeout: float = 5.0
