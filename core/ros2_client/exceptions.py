class RosClientError(Exception):
    """ROS 클라이언트 공통 예외."""


class RosConnectionError(RosClientError):
    """노드가 시작되지 않은 상태에서의 호출."""


class RosTimeoutError(RosClientError):
    """서비스 대기/호출 시간 초과."""


class RosMessageTypeError(RosClientError):
    """메시지/서비스 타입을 해석할 수 없거나 기존 타입과 충돌."""
