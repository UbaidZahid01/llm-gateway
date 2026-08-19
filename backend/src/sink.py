"""Audit log forwarder.

Posts each audit record to an external sink (SIEM / webhook) as fire-and-forget
work on a background thread, so a slow or down sink never blocks the LLM call.
A bounded retry with backoff handles transient failures; records are dropped
(with a log line) only after retries are exhausted or the queue is full.

Enabled by setting ``AUDIT_SINK_URL`` (and optionally ``AUDIT_SINK_ENABLED``).
"""

import json
import logging
import queue
import threading
import time
from typing import Any, Dict, Optional

import requests

from .config import settings

logger = logging.getLogger("guardrails.sink")

_MAX_QUEUE = 1000
_MAX_RETRIES = 3
_TIMEOUT = 5  # seconds per POST


class AuditSink:
    def __init__(self, url: Optional[str], enabled: bool):
        self.url = url
        self.enabled = bool(enabled and url)
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=_MAX_QUEUE)
        self._worker: Optional[threading.Thread] = None
        self._started = False
        self._lock = threading.Lock()

    def _ensure_worker(self) -> None:
        if self._started:
            return
        with self._lock:
            if self._started:
                return
            self._worker = threading.Thread(target=self._run, name="audit-sink", daemon=True)
            self._worker.start()
            self._started = True

    def send(self, record: Dict[str, Any]) -> None:
        """Enqueue a record for delivery. Never raises; drops on a full queue."""
        if not self.enabled:
            return
        self._ensure_worker()
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            logger.warning("audit sink queue full; dropping record %s", record.get("log_id"))

    def _run(self) -> None:
        while True:
            record = self._queue.get()
            try:
                self._deliver(record)
            except Exception as exc:  # noqa: BLE001 - never let the worker die
                logger.warning("audit sink delivery failed permanently: %s", exc)
            finally:
                self._queue.task_done()

    def _deliver(self, record: Dict[str, Any]) -> None:
        payload = json.dumps(record, default=str)
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = requests.post(
                    self.url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=_TIMEOUT,
                )
                if resp.status_code < 400:
                    return
                logger.warning(
                    "audit sink HTTP %s (attempt %s/%s) for %s",
                    resp.status_code, attempt, _MAX_RETRIES, record.get("log_id"),
                )
            except requests.RequestException as exc:
                logger.warning(
                    "audit sink error (attempt %s/%s) for %s: %s",
                    attempt, _MAX_RETRIES, record.get("log_id"), exc,
                )
            if attempt < _MAX_RETRIES:
                time.sleep(min(2 ** attempt, 10))
        logger.warning("audit sink giving up on record %s", record.get("log_id"))


audit_sink = AuditSink(settings.AUDIT_SINK_URL, settings.AUDIT_SINK_ENABLED)
