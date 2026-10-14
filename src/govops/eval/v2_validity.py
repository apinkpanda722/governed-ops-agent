"""Pure v2 validity under the fixed M6 redteam-run registration."""

from collections.abc import Mapping

STATUS_KEYS = ("ok", "timeout", "rate_limited", "error", "parse", "v1_blocked")
MIN_OK_RATE = 0.95


def v2_validity(switch: str, status_counts: Mapping[str, int]) -> dict:
    if switch not in ("on", "off") or any(
        key not in STATUS_KEYS or type(value) is not int or value < 0
        for key, value in status_counts.items()
    ):
        raise ValueError("invalid_status_counts")
    attempts = sum(status_counts.get(key, 0) for key in STATUS_KEYS)
    ok = status_counts.get("ok", 0)
    ok_rate = ok / attempts if attempts else None
    reason = None
    if switch == "on":
        if not attempts:
            reason = "no_attempts"
        elif ok_rate < MIN_OK_RATE:
            reason = "ok_rate_below_95"
    return {
        "valid": reason is None,
        "reason": reason,
        "attempts": attempts,
        "ok": ok,
        "ok_rate": ok_rate,
        "switch": switch,
    }


def validity_label(validity: dict) -> str:
    return "valid" if validity["valid"] else "invalid_v2_off"
