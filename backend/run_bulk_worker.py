"""Standalone bulk worker entrypoint."""

from __future__ import annotations

import logging
import time

from app.config import DEALER_ID
from app.db import get_connection
from app.repositories.bulk_loads import BulkLoadsRepository
from app.services.bulk_job_service import process_job
from app.services.bulk_queue_service import BulkQueueService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main() -> None:
    queue_service = BulkQueueService()
    worker_id = "bulk-worker-cli"
    logger.info("bulk worker started")
    while True:
        handled = False
        try:
            messages = queue_service.receive_messages(max_messages=1, wait_time_sec=1)
            if messages:
                handled = True
            for message in messages:
                try:
                    process_job(message.job_id, message.dealer_id, worker_id)
                finally:
                    queue_service.ack_message(message)

            if not handled:
                conn = get_connection()
                try:
                    BulkLoadsRepository.ensure_table(conn)
                    fallback_jobs = BulkLoadsRepository.list_runnable_jobs(conn, dealer_id=DEALER_ID, limit=1)
                    conn.commit()
                finally:
                    conn.close()
                for row in fallback_jobs:
                    handled = True
                    process_job(row["job_id"], int(row.get("dealer_id") or DEALER_ID), worker_id)
        except Exception:
            logger.exception("bulk worker loop failed")
        if not handled:
            time.sleep(1)


if __name__ == "__main__":
    main()
