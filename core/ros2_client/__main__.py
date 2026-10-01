"""토픽 구독 스모크 테스트.

예) python -m core.ros2_client --topic /chatter --type std_msgs/msg/String
"""
import argparse
import logging
import signal
import threading

from rclpy.qos import qos_profile_sensor_data

from config import ros_client_config

from .ros_client import RosClient


def main() -> None:
    config = ros_client_config()

    parser = argparse.ArgumentParser(description="ROS client topic echo")
    parser.add_argument("--node-name", default=config.node_name)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--type", dest="msg_type", required=True)
    parser.add_argument("--sensor-qos", action="store_true", help="use qos_profile_sensor_data (best effort)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config.node_name = args.node_name

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())

    with RosClient(config) as client:
        client.subscribe(
            args.topic,
            args.msg_type,
            lambda msg: print(msg, flush=True),
            qos=qos_profile_sensor_data if args.sensor_qos else None,
        )
        stop.wait()


if __name__ == "__main__":
    main()
