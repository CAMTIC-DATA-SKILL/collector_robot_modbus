from dataclasses import dataclass


@dataclass(slots=True)
class ZmqConfig:
    """게이트웨이 IPC 용 ZeroMQ 엔드포인트. 환경 변수 값은 루트 config.py 에서 채운다."""

    # collector → gateway (data / health / ack). 같은 edge 의 collector 끼리 포트가 겹치면 안 된다
    pub_endpoint: str = "tcp://127.0.0.1:5557"
    # gateway → collector (cmd_r / cmd_w). 게이트웨이 PUB 주소
    sub_endpoint: str = "tcp://127.0.0.1:5555"
    # True 면 bind, False 면 connect
    pub_bind: bool = True
    sub_bind: bool = False
    recv_timeout_ms: int = 100
    linger_ms: int = 0
