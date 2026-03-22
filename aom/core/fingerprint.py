from __future__ import annotations

import hashlib
import json
from typing import Any, Dict


def canonical_expression(expression: str) -> str:
    return " ".join(expression.strip().split())


def canonical_settings(settings: Dict[str, Any]) -> str:
    return json.dumps(settings, sort_keys=True, separators=(",", ":"))


def factor_fingerprint(expression: str, settings: Dict[str, Any], alpha_type: str = "REGULAR") -> str:
    payload = f"{canonical_expression(expression)}|{canonical_settings(settings)}|{alpha_type}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
