"""RabbitMQ asynchronous message bus implementation using aio-pika."""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from typing import Any

import aio_pika
import structlog
from aio_pika.abc import AbstractChannel, AbstractConnection, AbstractExchange, AbstractQueue

from src.config.settings import settings
from src.infrastructure.messaging.topology import EVENTS_EXCHANGE

logger = structlog.get_logger(__name__)


class MessageBus:
    """Asynchronous RabbitMQ message publisher and subscriber."""

    def __init__(self, url: str | None = None) -> None:
        self._url = url or settings.rabbitmq.url
        self._connection: AbstractConnection | None = None
        self._channel: AbstractChannel | None = None
        self._exchange: AbstractExchange | None = None

    async def connect(self) -> None:
        """Establish connection and declare topic exchange."""
        if self._connection is None:
            try:
                self._connection = await aio_pika.connect_robust(self._url)
                self._channel = await self._connection.channel()
                await self._channel.set_qos(prefetch_count=settings.rabbitmq.prefetch_count)
                self._exchange = await self._channel.declare_exchange(
                    EVENTS_EXCHANGE,
                    aio_pika.ExchangeType.TOPIC,
                    durable=True,
                )
                logger.info("rabbitmq_connected", exchange=EVENTS_EXCHANGE)
            except Exception as exc:
                logger.warning("rabbitmq_connect_failed", error=str(exc))
                self._connection = None
                self._channel = None
                self._exchange = None

    async def disconnect(self) -> None:
        """Close RabbitMQ connection."""
        if self._channel:
            await self._channel.close()
            self._channel = None
        if self._connection:
            await self._connection.close()
            self._connection = None
        logger.info("rabbitmq_disconnected")

    async def is_connected(self) -> bool:
        """Return True if connection is alive."""
        return self._connection is not None and not self._connection.is_closed

    async def publish(
        self,
        routing_key: str,
        payload: dict[str, Any],
        correlation_id: str | None = None,
        workspace_id: str | None = None,
    ) -> None:
        """Publish JSON message to topic exchange."""
        if not self._exchange:
            logger.warning("rabbitmq_publish_skipped_not_connected", routing_key=routing_key)
            return

        body = json.dumps(payload, default=str).encode("utf-8")
        headers: dict[str, Any] = {}
        if workspace_id:
            headers["workspace_id"] = workspace_id

        message = aio_pika.Message(
            body=body,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            content_type="application/json",
            correlation_id=correlation_id,
            headers=headers,
        )

        await self._exchange.publish(message, routing_key=routing_key)
        logger.debug("message_published", routing_key=routing_key, correlation_id=correlation_id)

    async def subscribe(
        self,
        queue_name: str,
        routing_keys: list[str],
        callback: Callable[[dict[str, Any], dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> AbstractQueue | None:
        """Declare queue, bind to routing keys, and register async message callback."""
        if not self._channel or not self._exchange:
            logger.warning("rabbitmq_subscribe_failed_not_connected", queue=queue_name)
            return None

        queue = await self._channel.declare_queue(queue_name, durable=True)
        for rk in routing_keys:
            await queue.bind(self._exchange, routing_key=rk)

        async def _consumer(message: aio_pika.abc.AbstractIncomingMessage) -> None:
            async with message.process():
                try:
                    payload = json.loads(message.body.decode("utf-8"))
                    headers = dict(message.headers or {})
                    await callback(payload, headers)
                except Exception as exc:
                    logger.error("message_consumer_error", queue=queue_name, error=str(exc))
                    raise

        await queue.consume(_consumer)
        logger.info("queue_consumer_registered", queue=queue_name, routing_keys=routing_keys)
        return queue


message_bus = MessageBus()
