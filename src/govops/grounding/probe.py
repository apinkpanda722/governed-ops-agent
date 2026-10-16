"""Read-only, count-only checkpoint evidence availability probe for M8."""

import argparse
import asyncio
import json
import os
import re
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from govops.eval.observation import load_observations
from govops.grounding import db

_HEX_ID = re.compile(r"[0-9a-f]{32}")
_CHAR_BOUNDARIES = (20000, 40000, 80000, 120000)
_STAGES = ("db_connect", "link", "thread_read", "tool_messages", "langfuse")


@dataclass(frozen=True)
class RowLink:
    result_dir: str
    task_id: str
    run_index: int
    trace_id: str | None
    thread_ids: tuple[str, ...]


@dataclass(frozen=True)
class ProbeStats:
    threads: int
    found: int
    missing: int
    errors: int
    error_types: tuple[str, ...]
    row_tool_messages: tuple[int, ...]
    row_nonempty: tuple[int, ...]
    tool_messages: int
    nonempty: int
    truncated: int
    char_buckets: tuple[int, ...]
    shape_dict: int
    shape_object: int
    shape_unknown: int
    assistant_tool_calls: int = 0


@dataclass(frozen=True)
class LangfuseStats:
    traces: int
    generations_with_input: int


class LangfuseShapeError(ValueError):
    """The public API response cannot establish the requested counts."""


def _attempt_threads(attempt: Path, trace_id: str) -> set[str]:
    """Project only the trace link and audit task identifiers, never bodies."""
    try:
        record = json.loads((attempt / "attempt.json").read_text(encoding="utf-8"))
        if not isinstance(record, dict) or record.get("trace_id") != trace_id:
            return set()
    except (OSError, ValueError, UnicodeError):
        return set()
    threads = {trace_id} if _HEX_ID.fullmatch(trace_id) else set()
    for chain in sorted((attempt / "audit").glob("chain-*.jsonl")):
        try:
            lines = chain.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for line in lines:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            task_id = event.get("task_id") if isinstance(event, dict) else None
            if isinstance(task_id, str) and _HEX_ID.fullmatch(task_id):
                threads.add(task_id)
    return threads


def link_results(results_dir: Path) -> list[RowLink]:
    """Link each observation to matching attempts; unresolved rows stay present."""
    attempts: dict[str, list[Path]] = {}
    for file in sorted((results_dir / "attempts").glob("*/attempt.json")):
        task, separator, number = file.parent.name.rpartition("-")
        if separator and number.isdigit():
            attempts.setdefault(task, []).append(file.parent)
    links = []
    for observation in load_observations(results_dir / "observations.jsonl"):
        threads: set[str] = set()
        if observation.trace_id:
            for attempt in attempts.get(observation.task_id, []):
                threads.update(_attempt_threads(attempt, observation.trace_id))
        links.append(
            RowLink(
                results_dir.name,
                observation.task_id,
                observation.run_index,
                observation.trace_id,
                tuple(sorted(threads)),
            )
        )
    return links


def _message_parts(message: object) -> tuple[str | None, str, str]:
    if isinstance(message, dict):
        role, content = message.get("role"), message.get("content")
        shape = "dict"
    else:
        role = getattr(message, "type", None)
        content = getattr(message, "content", None)
        shape = "object"
    if not isinstance(role, str) or not isinstance(content, str):
        return None, "", "unknown"
    return role, content, shape


def _is_tool_call_only(message: object) -> bool:
    """An assistant turn that only requested tools (no text) is a normal message."""
    if isinstance(message, dict):
        role, content = message.get("role"), message.get("content")
        calls = message.get("tool_calls")
    else:
        role = getattr(message, "type", None)
        content = getattr(message, "content", None)
        calls = getattr(message, "tool_calls", None)
    if role not in {"assistant", "ai"}:
        return False
    if not (content is None or (isinstance(content, str) and not content.strip())):
        return False
    return isinstance(calls, (list, tuple)) and len(calls) > 0


async def probe_threads(saver, links: Sequence[RowLink]) -> ProbeStats:
    """Read latest tuples directly, outside any lock-holding saver iterator."""
    checkpoints: dict[str, object] = {}
    found = missing = errors = 0
    error_types: set[str] = set()
    for thread_id in sorted({thread for link in links for thread in link.thread_ids}):
        try:
            item = await asyncio.wait_for(
                saver.aget_tuple({"configurable": {"thread_id": thread_id}}), 30
            )
            checkpoints[thread_id] = item.checkpoint if item is not None else None
            if item is None:
                missing += 1
            else:
                found += 1
        except Exception as exc:  # noqa: BLE001 — only class names leave this boundary
            checkpoints[thread_id] = None
            errors += 1
            error_types.add(type(exc).__name__)

    row_counts, row_nonempty = [], []
    nonempty = truncated = 0
    shapes = {"dict": 0, "object": 0, "unknown": 0}
    assistant_tool_calls = 0
    buckets = [0] * 5
    for link in links:
        count = chars = present = 0
        for thread_id in link.thread_ids:
            checkpoint = checkpoints[thread_id]
            if checkpoint is None:
                continue
            # A malformed checkpoint cannot silently look like an empty message set.
            if not isinstance(checkpoint, dict):
                shapes["unknown"] += 1
                continue
            values = checkpoint.get("channel_values", {})
            messages = values.get("messages", []) if isinstance(values, dict) else None
            if not isinstance(messages, (list, tuple)):
                shapes["unknown"] += 1
                continue
            for message in messages:
                if _is_tool_call_only(message):
                    shapes["dict" if isinstance(message, dict) else "object"] += 1
                    assistant_tool_calls += 1
                    continue
                role, content, shape = _message_parts(message)
                shapes[shape] += 1
                if role == "tool":
                    count += 1
                    present += bool(content.strip())
                    chars += len(content)
                    truncated += len(content) >= 20000 or content.endswith("…(잘림)")
        nonempty += present
        row_counts.append(count)
        row_nonempty.append(present)
        buckets[bisect_right(_CHAR_BOUNDARIES, chars)] += 1
    return ProbeStats(
        threads=len(checkpoints),
        found=found,
        missing=missing,
        errors=errors,
        error_types=tuple(sorted(error_types)),
        row_tool_messages=tuple(row_counts),
        row_nonempty=tuple(row_nonempty),
        tool_messages=sum(row_counts),
        nonempty=nonempty,
        truncated=truncated,
        char_buckets=tuple(buckets),
        shape_dict=shapes["dict"],
        shape_object=shapes["object"],
        shape_unknown=shapes["unknown"],
        assistant_tool_calls=assistant_tool_calls,
    )


async def _pages(client, endpoint: str, **params):
    """Require a paginated v3 public list envelope on every page."""
    page = 1
    while True:
        response = await client.get(
            endpoint, params={**params, "page": page, "limit": 100}
        )
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError:
            raise LangfuseShapeError("langfuse_shape") from None
        if not isinstance(payload, dict):
            raise LangfuseShapeError("langfuse_shape")
        data, meta = payload.get("data"), payload.get("meta")
        if not isinstance(data, list) or not isinstance(meta, dict):
            raise LangfuseShapeError("langfuse_shape")
        total = meta.get("totalPages")
        if (
            type(meta.get("page")) is not int
            or meta["page"] != page
            or type(total) is not int
            or total < 0
            or (total < page and not (page == 1 and total == 0 and not data))
            or not all(isinstance(item, dict) for item in data)
        ):
            raise LangfuseShapeError("langfuse_shape")
        for item in data:
            yield item
        if page >= total:
            return
        if not data:
            raise LangfuseShapeError("langfuse_shape")
        page += 1


async def langfuse_counts(client, links: Sequence[RowLink]) -> LangfuseStats:
    """Count listed linked traces and generations containing input; discard bodies."""
    wanted = {link.trace_id for link in links if link.trace_id}
    traces: set[str] = set()
    async for trace in _pages(client, "/api/public/traces"):
        if not isinstance(trace.get("id"), str):
            raise LangfuseShapeError("langfuse_shape")
        if trace["id"] in wanted:
            traces.add(trace["id"])
    inputs = 0
    # Both list endpoints must be checked, even if no requested trace was listed.
    for trace_id in sorted(wanted) or [None]:
        params = {"traceId": trace_id} if trace_id else {}
        async for observation in _pages(client, "/api/public/observations", **params):
            if not isinstance(observation.get("type"), str):
                raise LangfuseShapeError("langfuse_shape")
            if observation["type"] == "GENERATION":
                if "input" not in observation:
                    raise LangfuseShapeError("langfuse_shape")
                value = observation["input"]
                nonempty = value is not None
                if isinstance(value, str):
                    nonempty = bool(value.strip())
                elif isinstance(value, (list, dict)):
                    nonempty = bool(value)
                inputs += nonempty and trace_id in traces
    return LangfuseStats(len(traces), inputs)


def _tool_result(stats: ProbeStats) -> tuple[str, str]:
    rows_with_tools = sum(count > 0 for count in stats.row_tool_messages)
    rows_nonempty = sum(count > 0 for count in stats.row_nonempty)
    ok = bool(stats.row_tool_messages) and rows_nonempty == len(stats.row_tool_messages)
    ok = ok and stats.shape_unknown == 0
    bucket_names = (
        "chars_lt_20000",
        "chars_lt_40000",
        "chars_lt_80000",
        "chars_lt_120000",
        "chars_ge_120000",
    )
    buckets = " ".join(
        f"{name}={count}"
        for name, count in zip(bucket_names, stats.char_buckets, strict=True)
    )
    return (
        "PASS" if ok else "FAIL",
        (
            f"rows={len(stats.row_tool_messages)} rows_with_tools={rows_with_tools} "
            f"rows_nonempty={rows_nonempty} messages={stats.tool_messages} "
            f"nonempty={stats.nonempty} truncated={stats.truncated} "
            f"shape_dict={stats.shape_dict} shape_object={stats.shape_object} "
            f"shape_unknown={stats.shape_unknown} "
            f"assistant_tool_calls={stats.assistant_tool_calls} {buckets}"
        ),
    )


async def _run(args) -> dict[str, tuple[str, str]]:
    stages = {
        "db_connect": ("SKIP", "not_attempted"),
        "link": ("SKIP", "not_attempted"),
        "thread_read": ("SKIP", "dependency_failed"),
        "tool_messages": ("SKIP", "dependency_failed"),
        "langfuse": ("SKIP", "not_requested"),
    }
    links: list[RowLink] = []
    link_errors = 0
    for result_dir in args.results:
        try:
            links.extend(link_results(result_dir))
        except Exception:  # noqa: BLE001 — paths and observation errors stay private
            link_errors += 1
    linked = sum(bool(link.thread_ids) for link in links)
    stages["link"] = (
        "PASS" if links and linked == len(links) and not link_errors else "FAIL",
        ("no_rows " if not links else "")
        + f"rows={len(links)} linked={linked} missing={len(links) - linked} errors={link_errors}",
    )
    try:
        url = os.environ[args.url_env]
    except KeyError:
        url = ""
    if not url.strip():
        stages["db_connect"] = ("FAIL", "url_env_unset")
    else:
        opened = False
        try:
            if args.dbname is not None:
                url = db.url_with_dbname(url, args.dbname)
            async with db.open_readonly_saver(url) as saver:
                opened = True
                stages["db_connect"] = (
                    "PASS",
                    "connected=1"
                    + (f" dbname={args.dbname}" if args.dbname is not None else ""),
                )
                if links:
                    stats = await probe_threads(saver, links)
                    stages["thread_read"] = (
                        "PASS"
                        if stats.threads and not (stats.missing or stats.errors)
                        else "FAIL",
                        f"threads={stats.threads} found={stats.found} missing={stats.missing} "
                        f"errors={stats.errors} " + " ".join(stats.error_types),
                    )
                    stages["tool_messages"] = _tool_result(stats)
                else:
                    stages["thread_read"] = ("SKIP", "no_rows")
                    stages["tool_messages"] = ("SKIP", "no_rows")
        except Exception as exc:  # noqa: BLE001 — never print a backend error message
            # Opening/closing errors belong to db_connect; unexpected tuple errors
            # must not erase an already established connection result.
            stage = (
                "thread_read"
                if opened and not isinstance(exc, RuntimeError)
                else "db_connect"
            )
            stages[stage] = ("FAIL", type(exc).__name__)

    if args.langfuse:
        names = ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
        missing = sum(not os.environ.get(name, "").strip() for name in names)
        if missing:
            stages["langfuse"] = ("FAIL", f"env_unset missing={missing}")
        else:
            try:
                async with httpx.AsyncClient(
                    base_url=os.environ["LANGFUSE_HOST"].strip(),
                    auth=httpx.BasicAuth(
                        os.environ["LANGFUSE_PUBLIC_KEY"],
                        os.environ["LANGFUSE_SECRET_KEY"],
                    ),
                    timeout=30,
                    follow_redirects=False,
                ) as client:
                    stats_lf = await langfuse_counts(client, links)
                detail = (
                    f"traces={stats_lf.traces} "
                    f"generations_with_input={stats_lf.generations_with_input}"
                )
                # Zero usable traces cannot establish anything: report SKIP with the reason.
                stages["langfuse"] = (
                    ("SKIP", f"no_usable_traces {detail}")
                    if stats_lf.traces == 0 or stats_lf.generations_with_input == 0
                    else ("PASS", detail)
                )
            except LangfuseShapeError:
                stages["langfuse"] = ("FAIL", "langfuse_shape")
            except Exception as exc:  # noqa: BLE001 — HTTP errors contain URLs and headers
                stages["langfuse"] = ("FAIL", type(exc).__name__)
    return stages


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid_arguments")


def main(argv: list[str] | None = None) -> int:
    """Emit exactly five stage lines and a count summary, never source values."""
    parser = _Parser(description=__doc__)
    parser.add_argument("--results", type=Path, nargs="+", required=True)
    parser.add_argument("--url-env", default="GOVOPS_DATABASE_URL")
    parser.add_argument("--dbname")
    parser.add_argument("--langfuse", action="store_true")
    try:
        args = parser.parse_args(argv)
        stages = asyncio.run(_run(args))
    except Exception:  # noqa: BLE001 — invalid argument values may contain secrets
        stages = {stage: ("SKIP", "invalid_arguments") for stage in _STAGES}
        stages["link"] = ("FAIL", "invalid_arguments")
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for stage in _STAGES:
        status, detail = stages[stage]
        print(f"{status} {stage} {detail}".rstrip())
        counts[status] += 1
    print(f"SUMMARY pass={counts['PASS']} fail={counts['FAIL']} skip={counts['SKIP']}")
    return int(counts["FAIL"] > 0)


if __name__ == "__main__":
    raise SystemExit(main())
