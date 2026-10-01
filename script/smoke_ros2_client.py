"""ROS 2 클라이언트 스모크 테스트.

외부 노드 없이 자기 자신에게 publish/subscribe, 서비스 서버/호출을 해 보며
RosClient 의 기본 기능과 예외 매핑을 점검한다. --topic/--type 을 주면
외부 토픽에서 메시지 1건 수신까지 확인한다.

사전 준비) source /opt/ros/humble/setup.bash && source .venv/bin/activate
예) python script/smoke_ros2_client.py
    python script/smoke_ros2_client.py --topic /joint_states --type sensor_msgs/msg/JointState --sensor-qos
"""
import argparse
import logging
import os
import sys
import threading
import time
from typing import Any

from _smoke import SmokeAbort, SmokeRunner, expect
from rclpy.qos import qos_profile_sensor_data
from std_srvs.srv import Trigger

from config import ros_client_config, settings
from core.ros2_client import (
    RosClient,
    RosClientError,
    RosConnectionError,
    RosMessageTypeError,
    RosTimeoutError,
)


def _wait_loopback(client: RosClient, topic: str, timeout: float) -> str:
    """구독 → 반복 publish. DDS discovery 가 끝나기 전 첫 메시지는 유실될 수 있다."""
    received: list[dict[str, Any]] = []
    done = threading.Event()

    def on_message(msg: dict[str, Any]) -> None:
        received.append(msg)
        done.set()

    client.subscribe(topic, "std_msgs/msg/String", on_message)
    deadline = time.monotonic() + timeout
    while not done.is_set():
        if time.monotonic() > deadline:
            raise TimeoutError(f"no loopback message on {topic} within {timeout}s")
        client.publish(topic, "std_msgs/msg/String", {"data": "smoke"})
        done.wait(0.1)
    return expect(received[0]["data"], "smoke")


def _service_from_callback(client: RosClient, topic: str, service: str, timeout: float) -> str:
    """구독 콜백 안에서 call_service 가 교착 없이 끝나는지 확인한다."""
    results: list[Any] = []
    done = threading.Event()

    def on_message(_: dict[str, Any]) -> None:
        if done.is_set():
            return
        try:
            results.append(client.call_service(service, "std_srvs/srv/Trigger", timeout=timeout))
        except Exception as e:
            results.append(e)
        done.set()

    client.subscribe(topic, "std_msgs/msg/String", on_message)
    deadline = time.monotonic() + timeout * 2
    while not done.is_set():
        if time.monotonic() > deadline:
            raise TimeoutError("callback did not finish (deadlock?)")
        client.publish(topic, "std_msgs/msg/String", {"data": "call"})
        done.wait(0.1)
    client.unsubscribe(topic)
    if isinstance(results[0], Exception):
        raise results[0]
    return expect(results[0]["message"], "pong")


def _wait_external(client: RosClient, topic: str, msg_type: str, sensor_qos: bool, timeout: float) -> str:
    received: list[dict[str, Any]] = []
    done = threading.Event()

    def on_message(msg: dict[str, Any]) -> None:
        received.append(msg)
        done.set()

    client.subscribe(topic, msg_type, on_message, qos=qos_profile_sensor_data if sensor_qos else None)
    try:
        if not done.wait(timeout):
            raise TimeoutError(f"no message on {topic} within {timeout}s")
    finally:
        client.unsubscribe(topic)
    preview = str(dict(received[0]))
    return preview if len(preview) <= 200 else preview[:200] + "..."


def main() -> int:
    config = ros_client_config()

    parser = argparse.ArgumentParser(description="ROS 2 client smoke test")
    parser.add_argument("--node-name", default=f"collector_smoke_{os.getpid()}")
    parser.add_argument("--timeout", type=float, default=5.0, help="seconds to wait for each message/service")
    parser.add_argument("--topic", help="external topic to receive one message from")
    parser.add_argument("--type", dest="msg_type", help="message type of --topic, e.g. std_msgs/msg/String")
    parser.add_argument("--sensor-qos", action="store_true", help="use qos_profile_sensor_data for --topic")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    if bool(args.topic) != bool(args.msg_type):
        parser.error("--topic and --type must be given together")

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config.node_name = args.node_name
    prefix = f"/{args.node_name}"
    chatter, callback_topic, service = f"{prefix}/chatter", f"{prefix}/call", f"{prefix}/trigger"

    runner = SmokeRunner(f"ROS 2 client node={config.node_name} domain={settings.ros_domain_id}")
    client = RosClient(config)
    try:
        runner.check("connect", client.connect, required=True)
        runner.check("is_connected", lambda: expect(client.is_connected, True))

        runner.check("publish/subscribe loopback", lambda: _wait_loopback(client, chatter, args.timeout))
        runner.expect_raises(
            "duplicate subscribe -> RosClientError",
            RosClientError,
            lambda: client.subscribe(chatter, "std_msgs/msg/String", lambda _: None),
        )
        runner.expect_raises(
            "publish with other type -> RosMessageTypeError",
            RosMessageTypeError,
            lambda: client.publish(chatter, "std_msgs/msg/Int32", {"data": 1}),
        )
        runner.expect_raises(
            "unknown type -> RosMessageTypeError",
            RosMessageTypeError,
            lambda: client.publish(f"{prefix}/unknown", "no_such_pkg/msg/Nothing", {}),
        )
        runner.check("unsubscribe", lambda: client.unsubscribe(chatter))

        def on_trigger(_request: Trigger.Request, response: Trigger.Response) -> Trigger.Response:
            response.success = True
            response.message = "pong"
            return response

        client.node.create_service(Trigger, service, on_trigger)
        runner.check(
            "call_service (std_srvs/Trigger)",
            lambda: expect(dict(client.call_service(service, "std_srvs/srv/Trigger", timeout=args.timeout)),
                           {"success": True, "message": "pong"}),
        )
        runner.check(
            "call_service inside subscription callback",
            lambda: _service_from_callback(client, callback_topic, service, args.timeout),
        )
        runner.expect_raises(
            "missing service -> RosTimeoutError",
            RosTimeoutError,
            lambda: client.call_service(f"{prefix}/missing", "std_srvs/srv/Trigger", timeout=0.5),
        )

        if args.topic:
            runner.check(
                f"receive external {args.topic}",
                lambda: _wait_external(client, args.topic, args.msg_type, args.sensor_qos, args.timeout),
            )

        def disconnect() -> None:
            client.disconnect()
            expect(client.is_connected, False)

        runner.check("disconnect", disconnect)
        runner.expect_raises(
            "publish after disconnect -> RosConnectionError",
            RosConnectionError,
            lambda: client.publish(chatter, "std_msgs/msg/String", {"data": "x"}),
        )
    except SmokeAbort:
        pass
    finally:
        client.disconnect()
    return runner.finish()


if __name__ == "__main__":
    sys.exit(main())
