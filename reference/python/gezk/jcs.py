"""RFC 8785 (JSON Canonicalization Scheme) — the byte-exact signature input.
Keys sort by UTF-16 code units and numbers serialize the ECMAScript way."""

from __future__ import annotations

import json
import math
import re

_REPR = re.compile(r"^(-?)(\d+)(?:\.(\d+))?(?:e([+-]\d+))?$")


def es_number(value: float) -> str:
    if isinstance(value, bool):
        raise TypeError("booleans are not numbers")
    if not math.isfinite(value):
        raise ValueError("non-finite number has no JSON identity")
    if value == 0:
        return "0"
    if float(value).is_integer() and abs(value) < 1e21:
        return str(int(value))
    match = _REPR.match(repr(float(value)))
    if not match:
        raise ValueError(f"unexpected float repr {value!r}")
    sign, int_part, frac, exp = match.groups()
    digits = int_part + (frac or "")
    point = len(int_part) + int(exp or 0)
    stripped = digits.lstrip("0")
    point -= len(digits) - len(stripped)
    digits = stripped.rstrip("0") or "0"
    k, n = len(digits), point
    if k <= n <= 21:
        return f"{sign}{digits}{'0' * (n - k)}"
    if 0 < n <= 21:
        return f"{sign}{digits[:n]}.{digits[n:]}"
    if -6 < n <= 0:
        return f"{sign}0.{'0' * (-n)}{digits}"
    exponent = n - 1
    mantissa = digits[0] + (f".{digits[1:]}" if k > 1 else "")
    return f"{sign}{mantissa}e{'+' if exponent >= 0 else '-'}{abs(exponent)}"


def _utf16_key(key: str) -> bytes:
    return key.encode("utf-16-be")


def canonicalize(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return es_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonicalize(item) for item in value) + "]"
    if isinstance(value, dict):
        items = sorted(((k, v) for k, v in value.items() if v is not None or True), key=lambda kv: _utf16_key(kv[0]))
        return "{" + ",".join(f"{json.dumps(k, ensure_ascii=False)}:{canonicalize(v)}" for k, v in items) + "}"
    raise TypeError(f"value of type {type(value).__name__} has no JSON identity")
