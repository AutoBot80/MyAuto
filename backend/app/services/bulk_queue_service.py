"""Queue abstraction for bulk jobs.

Uses SQS when configured, with an in-process local queue fallback for development.
"""

from __future__ import annotations

import json
import logging
import queue
from dataclasses import dataclass
from typing import Any

import boto3

from app.config import (
    BULK_QUEUE_PROVIDER,
    BULK_SQS_QUEUE_URL,
    BULK_SQS_REGION,
    BULK_SQS_VISIBILITY_TIMEOUT_SEC,
    BULK_SQS_WAIT_TIME_SEC,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class BulkQueueMessage:
    job_id: str
    dealer_id: int
    receipt_handle: str | None = None
    transport_id: str | None = None
    source: str = "local"


class BulkQueueService:
    _local_queue: queue.Queue[dict[str, Any]] = queue.Queue()

    def __init__(self) -> None:
        configured_provider = BULK_QUEUE_PROVIDER
        self.provider = "sqs" if BULK_QUEUE_PROVIDER == "sqs" and BULK_SQS_QUEUE_URL else "local"
        self._client = None
        if configured_provider == "sqs" and not BULK_SQS_QUEUE_URL:
            logger.warning(
                "bulk_queue: BULK_QUEUE_PROVIDER=sqs but BULK_SQS_QUEUE_URL is empty; falling back to local queue"
            )
        if self.provider == "sqs":
            self._client = boto3.client("sqs", region_name=BULK_SQS_REGION)
            logger.info("bulk_queue: using SQS queue in region %s", BULK_SQS_REGION)
        else:
            logger.info("bulk_queue: using local in-process queue")

    def send_job(self, job_id: str, dealer_id: int) -> str | None:
        payload = {"job_id": job_id, "dealer_id": dealer_id}
        if self.provider == "sqs":
            assert self._client is not None
            response = self._client.send_message(
                QueueUrl=BULK_SQS_QUEUE_URL,
                MessageBody=json.dumps(payload),
            )
            return response.get("MessageId")

        transport_id = f"local-{job_id}"
        self._local_queue.put({"payload": payload, "transport_id": transport_id})
        return transport_id

    def receive_messages(self, max_messages: int = 1, wait_time_sec: int | None = None) -> list[BulkQueueMessage]:
        if self.provider == "sqs":
            assert self._client is not None
            response = self._client.receive_message(
                QueueUrl=BULK_SQS_QUEUE_URL,
                MaxNumberOfMessages=max(1, min(max_messages, 10)),
                WaitTimeSeconds=wait_time_sec if wait_time_sec is not None else BULK_SQS_WAIT_TIME_SEC,
                VisibilityTimeout=BULK_SQS_VISIBILITY_TIMEOUT_SEC,
            )
            messages = response.get("Messages", [])
            results: list[BulkQueueMessage] = []
            for msg in messages:
                try:
                    body = json.loads(msg.get("Body") or "{}")
                    results.append(
                        BulkQueueMessage(
                            job_id=str(body["job_id"]),
                            dealer_id=int(body["dealer_id"]),
                            receipt_handle=msg.get("ReceiptHandle"),
                            transport_id=msg.get("MessageId"),
                            source="sqs",
                        )
                    )
                except Exception:
                    logger.exception("bulk_queue: failed to parse SQS message %s", msg)
            return results

        timeout = wait_time_sec if wait_time_sec is not None else 1
        results: list[BulkQueueMessage] = []
        try:
            item = self._local_queue.get(timeout=timeout)
            payload = item["payload"]
            results.append(
                BulkQueueMessage(
                    job_id=str(payload["job_id"]),
                    dealer_id=int(payload["dealer_id"]),
                    transport_id=item.get("transport_id"),
                    source="local",
                )
            )
        except queue.Empty:
            return []

        while len(results) < max_messages:
            try:
                item = self._local_queue.get_nowait()
            except queue.Empty:
                break
            payload = item["payload"]
            results.append(
                BulkQueueMessage(
                    job_id=str(payload["job_id"]),
                    dealer_id=int(payload["dealer_id"]),
                    transport_id=item.get("transport_id"),
                    source="local",
                )
            )
        return results

    def ack_message(self, message: BulkQueueMessage) -> None:
        if self.provider == "sqs" and message.receipt_handle:
            assert self._client is not None
            self._client.delete_message(
                QueueUrl=BULK_SQS_QUEUE_URL,
                ReceiptHandle=message.receipt_handle,
            )
