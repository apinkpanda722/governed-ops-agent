"""Strict, bounded content-free response header protocol."""

import re
from dataclasses import dataclass, replace

MAX_HEADER_LENGTH = 4800
_ID = re.compile(r"(?:call|toolu)_[A-Za-z0-9_-]{1,64}")
_HEX = re.compile(r"[0-9a-f]{12}")
_TEAM = re.compile(r"[a-z][a-z0-9-]{0,63}")
_KEYS = (
    "v",
    "rules",
    "code",
    "team",
    "pre",
    "c",
    "q",
    "m",
    "ids",
    "bad",
    "trunc",
    "ms",
    "post",
)
_PRE = {"allow", "block", "unavailable", "team_unknown", "probe"}
_CODES = {
    "-",
    "injection_direct",
    "pii_input",
    "guardrail_unavailable",
    "team_unknown",
    "probe",
}
_POST = {"ok", "flag", "error", "none"}


@dataclass(frozen=True)
class GuardrailInfo:
    rules: str
    code: str
    team: str = "none"
    pre: str = "allow"
    c: str = "-"
    q: int = 0
    m: int = 0
    ids: tuple[str, ...] = ()
    bad: int = 0
    trunc: int = 0
    ms: int = 0
    post: str = "none"


def format_header(info: GuardrailInfo) -> str:
    ids = tuple(i for i in info.ids if isinstance(i, str) and _ID.fullmatch(i))
    info = replace(
        info,
        ids=ids[:64],
        bad=info.bad + len(info.ids) - len(ids),
        trunc=int(bool(info.trunc or len(ids) > 64)),
    )
    values = (
        "1",
        info.rules,
        info.code,
        info.team,
        info.pre,
        info.c,
        str(info.q),
        str(info.m),
        ",".join(info.ids) or "-",
        str(info.bad),
        str(info.trunc),
        str(info.ms),
        info.post,
    )
    value = ";".join(f"{k}={v}" for k, v in zip(_KEYS, values, strict=True))
    if parse_header(value) is None:
        raise ValueError("invalid guardrail header")
    return value


def parse_header(value: str | None) -> GuardrailInfo | None:
    if not isinstance(value, str) or len(value) > MAX_HEADER_LENGTH:
        return None
    parts = value.split(";")
    if len(parts) != len(_KEYS):
        return None
    fields = {}
    for key, part in zip(_KEYS, parts, strict=True):
        if not part.startswith(key + "="):
            return None
        fields[key] = part[len(key) + 1 :]
    if (
        fields["v"] != "1"
        or not _HEX.fullmatch(fields["rules"])
        or not _HEX.fullmatch(fields["code"])
        or not _TEAM.fullmatch(fields["team"])
        or fields["pre"] not in _PRE
        or fields["c"] not in _CODES
        or fields["post"] not in _POST
        or fields["trunc"] not in {"0", "1"}
    ):
        return None
    for key in ("q", "m", "bad", "ms"):
        if not re.fullmatch(r"0|[1-9][0-9]{0,9}", fields[key]):
            return None
    ids = () if fields["ids"] == "-" else tuple(fields["ids"].split(","))
    if len(ids) > 64 or any(not _ID.fullmatch(i) for i in ids):
        return None
    q, m, bad, ms = (int(fields[k]) for k in ("q", "m", "bad", "ms"))
    if len(ids) + bad > q:
        return None
    return GuardrailInfo(
        fields["rules"],
        fields["code"],
        fields["team"],
        fields["pre"],
        fields["c"],
        q,
        m,
        ids,
        bad,
        int(fields["trunc"]),
        ms,
        fields["post"],
    )
