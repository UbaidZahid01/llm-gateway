"""Guardrail policy: what the gateway does with each kind of sensitive content.

The policy is config-driven. Each PII type maps independently to an action
(``allow`` | ``redact`` | ``mask`` | ``block``), plus a denylist of terms that
block a request outright. A single global policy is stored in
``db_json/guardrail_policy.json`` and seeded with a sensible default on first
use; it can be edited through the admin API / the frontend policy page.
"""

import copy
import threading
from typing import Any, Dict, List

from . import detectors
from .config import settings

# Actions a policy can take on a finding.
ALLOW = "allow"
REDACT = "redact"
MASK = "mask"
BLOCK = "block"
ACTIONS = [ALLOW, REDACT, MASK, BLOCK]

DEFAULT_POLICY: Dict[str, Any] = {
    "pii": {
        detectors.EMAIL: {"enabled": True, "action": REDACT},
        detectors.PHONE: {"enabled": True, "action": REDACT},
        detectors.CREDIT_CARD: {"enabled": True, "action": BLOCK},
        detectors.SSN: {"enabled": True, "action": BLOCK},
        detectors.CNIC: {"enabled": True, "action": BLOCK},
        detectors.IP: {"enabled": True, "action": MASK},
        detectors.SECRET: {"enabled": True, "action": BLOCK},
    },
    # Each: {"term": str, "is_regex": bool}. A match blocks the request.
    "denylist": [],
    "apply_to_request": True,
    "apply_to_response": True,
}


class PolicyStore:
    """Thread-safe accessor for the single global guardrail policy."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        if not self.path.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._write(DEFAULT_POLICY)

    def _write(self, policy: Dict[str, Any]) -> None:
        import json

        self.path.write_text(json.dumps(policy, indent=2), encoding="utf-8")

    def get(self) -> Dict[str, Any]:
        import json

        with self._lock:
            raw = self.path.read_text(encoding="utf-8").strip()
            stored = json.loads(raw) if raw else {}
        return _merge_with_default(stored)

    def update(self, policy: Dict[str, Any]) -> Dict[str, Any]:
        merged = _merge_with_default(policy)
        with self._lock:
            self._write(merged)
        return merged


def _merge_with_default(stored: Dict[str, Any]) -> Dict[str, Any]:
    """Return a full policy, filling any missing keys from the default so a
    partial/old stored policy (or a newly added PII type) still works."""
    policy = copy.deepcopy(DEFAULT_POLICY)
    if not isinstance(stored, dict):
        return policy

    pii = stored.get("pii")
    if isinstance(pii, dict):
        for ptype, rule in pii.items():
            if ptype in policy["pii"] and isinstance(rule, dict):
                action = rule.get("action")
                if action in ACTIONS:
                    policy["pii"][ptype]["action"] = action
                if isinstance(rule.get("enabled"), bool):
                    policy["pii"][ptype]["enabled"] = rule["enabled"]

    denylist = stored.get("denylist")
    if isinstance(denylist, list):
        clean: List[Dict[str, Any]] = []
        for entry in denylist:
            if isinstance(entry, dict) and entry.get("term"):
                clean.append(
                    {"term": str(entry["term"]), "is_regex": bool(entry.get("is_regex", False))}
                )
        policy["denylist"] = clean

    for flag in ("apply_to_request", "apply_to_response"):
        if isinstance(stored.get(flag), bool):
            policy[flag] = stored[flag]

    return policy


policy_store = PolicyStore(settings.DB_JSON_DIR / "guardrail_policy.json")
