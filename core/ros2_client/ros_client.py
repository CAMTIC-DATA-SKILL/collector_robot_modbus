import logging
import threading
from collections.abc import Callable
from typing import Any

import rclpy
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup, ReentrantCallbackGroup
from rclpy.client import Client
from rclpy.context import Context
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.publisher import Publisher
from rclpy.qos import QoSProfile
from rclpy.subscription import Subscription
from rosidl_runtime_py.convert import message_to_ordereddict
from rosidl_runtime_py.set_message import set_message_fields
from rosidl_runtime_py.utilities import get_message, get_service

from .config import RosClientConfig
from .exceptions import RosClientError, RosConnectionError, RosMessageTypeError, RosTimeoutError

Message = dict[str, Any]
MessageCallback = Callable[[Any], None]
QoS = QoSProfile | int

logger = logging.getLogger(__name__)


class RosClient:
    """rclpy 기반 ROS 2 클라이언트.

    - 전용 Context 와 백그라운드 MultiThreadedExecutor 스레드에서 동작하므로
      호출하는 쪽의 메인 루프를 막지 않는다.
    - 구독마다 별도의 MutuallyExclusive 콜백 그룹을 사용해
      토픽 간에는 병렬, 같은 토픽 안에서는 수신 순서대로 콜백이 실행된다.
    - 타입 문자열은 `std_msgs/msg/String`, `std_srvs/srv/Trigger` 형식을 사용한다.
    """

    def __init__(self, config: RosClientConfig) -> None:
        self.config = config
        self._lock = threading.RLock()
        self._context: Context | None = None
        self._node: Node | None = None
        self._executor: MultiThreadedExecutor | None = None
        self._spin_thread: threading.Thread | None = None
        self._service_group = ReentrantCallbackGroup()
        self._subscriptions: dict[str, Subscription] = {}
        self._publishers: dict[str, Publisher] = {}
        self._service_clients: dict[str, Client] = {}

    @property
    def is_connected(self) -> bool:
        return self._context is not None and self._context.ok()

    @property
    def node(self) -> Node:
        """고급 기능(타이머, 파라미터, 액션 등)을 위한 rclpy Node 직접 접근."""
        self._ensure_connected()
        return self._node

    def connect(self) -> None:
        with self._lock:
            if self.is_connected:
                return

            context = Context()
            rclpy.init(context=context, domain_id=self.config.domain_id)
            try:
                node = rclpy.create_node(
                    self.config.node_name,
                    namespace=self.config.namespace or None,
                    context=context,
                )
                executor = MultiThreadedExecutor(num_threads=self.config.num_threads, context=context)
                executor.add_node(node)
            except Exception:
                context.try_shutdown()
                raise

            self._context, self._node, self._executor = context, node, executor
            self._spin_thread = threading.Thread(
                target=executor.spin, name=f"{self.config.node_name}-spin", daemon=True
            )
            self._spin_thread.start()
            logger.info("ROS 2 node '%s' started", node.get_fully_qualified_name())

    def disconnect(self) -> None:
        with self._lock:
            if self._context is None:
                return

            self._executor.shutdown(timeout_sec=self.config.shutdown_timeout)
            self._spin_thread.join(timeout=self.config.shutdown_timeout)
            self._node.destroy_node()
            self._context.try_shutdown()

            self._subscriptions.clear()
            self._publishers.clear()
            self._service_clients.clear()
            self._context = self._node = self._executor = self._spin_thread = None
            logger.info("ROS 2 node stopped")

    def subscribe(
        self,
        topic: str,
        msg_type: str,
        callback: MessageCallback,
        qos: QoS | None = None,
        as_dict: bool = True,
    ) -> None:
        """토픽을 구독한다.

        as_dict=False 이면 dict 변환 없이 rclpy 메시지 객체를 그대로 넘긴다(고주기 토픽용).
        센서 토픽은 qos=rclpy.qos.qos_profile_sensor_data 사용을 권장한다.
        """
        self._ensure_connected()
        msg_cls = self._resolve_type(get_message, msg_type)

        def _on_message(msg: Any) -> None:
            try:
                callback(message_to_ordereddict(msg) if as_dict else msg)
            except Exception:
                logger.exception("Callback for '%s' raised", topic)

        with self._lock:
            if topic in self._subscriptions:
                raise RosClientError(f"Already subscribed to '{topic}'")
            self._subscriptions[topic] = self._node.create_subscription(
                msg_cls,
                topic,
                _on_message,
                self._qos(qos),
                callback_group=MutuallyExclusiveCallbackGroup(),
            )
        logger.info("Subscribed to %s [%s]", topic, msg_type)

    def unsubscribe(self, topic: str) -> None:
        with self._lock:
            subscription = self._subscriptions.pop(topic, None)
            if subscription is not None and self._node is not None:
                self._node.destroy_subscription(subscription)
                logger.info("Unsubscribed from %s", topic)

    def publish(self, topic: str, msg_type: str, message: Message | Any, qos: QoS | None = None) -> None:
        """message 는 dict 또는 msg_type 의 rclpy 메시지 객체."""
        self._ensure_connected()
        msg_cls = self._resolve_type(get_message, msg_type)

        with self._lock:
            publisher = self._publishers.get(topic)
            if publisher is None:
                publisher = self._node.create_publisher(msg_cls, topic, self._qos(qos))
                self._publishers[topic] = publisher
            elif publisher.msg_type is not msg_cls:
                raise RosMessageTypeError(f"'{topic}' is already advertised as {publisher.msg_type}")

        if isinstance(message, msg_cls):
            publisher.publish(message)
            return
        msg = msg_cls()
        set_message_fields(msg, message)
        publisher.publish(msg)

    def call_service(
        self,
        service: str,
        srv_type: str,
        request: Message | None = None,
        timeout: float | None = None,
    ) -> Message:
        """서비스를 동기 호출한다. 구독 콜백 안에서 호출해도 교착되지 않는다(num_threads > 1)."""
        self._ensure_connected()
        srv_cls = self._resolve_type(get_service, srv_type)
        timeout = self.config.service_timeout if timeout is None else timeout

        with self._lock:
            client = self._service_clients.get(service)
            if client is None:
                client = self._node.create_client(srv_cls, service, callback_group=self._service_group)
                self._service_clients[service] = client
            elif client.srv_type is not srv_cls:
                raise RosMessageTypeError(f"'{service}' is already used as {client.srv_type}")

        if not client.wait_for_service(timeout_sec=timeout):
            raise RosTimeoutError(f"Service '{service}' is not available")

        req = srv_cls.Request()
        set_message_fields(req, request or {})

        done = threading.Event()
        future = client.call_async(req)
        future.add_done_callback(lambda _: done.set())
        if not done.wait(timeout):
            client.remove_pending_request(future)
            raise RosTimeoutError(f"Service '{service}' call timed out")

        return message_to_ordereddict(future.result())

    def __enter__(self) -> "RosClient":
        self.connect()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.disconnect()

    def _ensure_connected(self) -> None:
        if not self.is_connected:
            raise RosConnectionError("ROS client is not connected")

    def _qos(self, qos: QoS | None) -> QoS:
        return self.config.qos_depth if qos is None else qos

    @staticmethod
    def _resolve_type(resolver: Callable[[str], Any], type_name: str) -> Any:
        try:
            return resolver(type_name)
        except (AttributeError, ModuleNotFoundError, ValueError) as e:
            raise RosMessageTypeError(f"Unknown ROS type '{type_name}'") from e
