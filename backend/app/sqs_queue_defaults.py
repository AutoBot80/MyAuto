"""
Canonical defaults for bulk ingest / worker SQS queue behavior.

Shared by ``bulk_queue_service`` and related code via ``app.config``. Env vars may
still override per deployment.
"""

BULK_QUEUE_PROVIDER: str = "sqs"

BULK_SQS_REGION: str = "ap-south-1"

BULK_SQS_WAIT_TIME_SEC: int = 20

BULK_SQS_VISIBILITY_TIMEOUT_SEC: int = 900

BULK_WORKER_ENABLED: bool = True

BULK_INGEST_ENABLED: bool = True
