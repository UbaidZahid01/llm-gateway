"""PII / secret detectors for the guardrail engine.

Each detector finds spans of a given type in a piece of text. The engine
resolves overlaps by priority (lower number = higher priority) so, e.g., a
credit-card number is never also reported as a phone number.

This is a regex baseline with a pluggable shape — a heavier engine (e.g.
Microsoft Presidio) can be dropped in behind the same ``detect()`` contract.
"""

import re
from dataclasses import dataclass
from typing import Callable, List, Optional

# Canonical PII type identifiers. Kept in sync with policy.py DEFAULT_POLICY.
EMAIL = "email"
PHONE = "phone"
CREDIT_CARD = "credit_card"
SSN = "ssn"
CNIC = "cnic"
IP = "ip"
SECRET = "secret"

ALL_TYPES = [EMAIL, PHONE, CREDIT_CARD, SSN, CNIC, IP, SECRET]


@dataclass
class Match:
    type: str
    start: int
    end: int
    value: str


def _luhn_ok(digits: str) -> bool:
    if len(digits) < 13:
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass
class _Detector:
    type: str
    priority: int
    pattern: "re.Pattern[str]"
    validator: Optional[Callable[[str], bool]] = None


# Priority order matters: more specific / higher-confidence types win overlaps.
_DETECTORS: List[_Detector] = [
    _Detector(
        SECRET,
        1,
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{36}|xox[baprs]-[A-Za-z0-9-]{10,})\b"
        ),
    ),
    _Detector(CREDIT_CARD, 2, re.compile(r"\b(?:\d[ -]?){13,19}\b"),
              validator=lambda s: _luhn_ok(re.sub(r"\D", "", s))),
    _Detector(CNIC, 3, re.compile(r"\b\d{5}-\d{7}-\d\b")),
    _Detector(SSN, 4, re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    _Detector(IP, 5, re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
    _Detector(EMAIL, 6, re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    _Detector(PHONE, 7, re.compile(r"(?<!\d)\+?\d(?:[\d().\s-]{7,}\d)(?!\d)")),
]


def detect(text: str, enabled_types: List[str]) -> List[Match]:
    """Return non-overlapping matches for the enabled types, ordered by position.

    Overlaps are resolved by detector priority; ties by earlier start.
    """
    if not text:
        return []

    candidates: List[tuple] = []  # (priority, Match)
    for det in _DETECTORS:
        if det.type not in enabled_types:
            continue
        for m in det.pattern.finditer(text):
            value = m.group(0)
            if det.validator and not det.validator(value):
                continue
            candidates.append((det.priority, Match(det.type, m.start(), m.end(), value)))

    # Resolve overlaps: keep higher-priority (lower number) matches.
    candidates.sort(key=lambda c: (c[0], c[1].start))
    chosen: List[Match] = []
    taken: List[tuple] = []  # (start, end)
    for _prio, match in candidates:
        if any(match.start < e and match.end > s for s, e in taken):
            continue
        chosen.append(match)
        taken.append((match.start, match.end))

    chosen.sort(key=lambda m: m.start)
    return chosen
