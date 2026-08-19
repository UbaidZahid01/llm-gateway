import json
import threading
from pathlib import Path
from typing import Any, Dict, List

from .config import settings


class JSONStore:
    """Thread-safe read/append/write helper backed by a single JSON array file."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("[]", encoding="utf-8")

    def read_all(self) -> List[Dict[str, Any]]:
        with self._lock:
            raw = self.path.read_text(encoding="utf-8").strip()
            return json.loads(raw) if raw else []

    def write_all(self, data: List[Dict[str, Any]]) -> None:
        with self._lock:
            self.path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def append(self, record: Dict[str, Any]) -> None:
        with self._lock:
            raw = self.path.read_text(encoding="utf-8").strip()
            data = json.loads(raw) if raw else []
            data.append(record)
            self.path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


audit_store = JSONStore(settings.DB_JSON_DIR / "audit_logs.json")
