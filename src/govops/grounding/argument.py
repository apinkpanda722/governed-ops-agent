"""Read-only M8 tool-argument ID grounding over checkpointer assistant messages.

Registration: docs/results/hypotheses-m8-argument.md. Argument values never leave
the process: outputs carry counts, tool names and ratios only.
"""

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from govops.eval.observation import load_observations
from govops.eval.spec import TaskSpec
from govops.grounding import db
from govops.grounding.deliverable import extract_citations
from govops.grounding.probe import link_results

_REGISTRATION = "docs/results/hypotheses-m8-argument.md"
_LABEL = "M8 추가 측정 — 공식 M6 수치 아님"
WRITE_TOOLS = frozenset(
    {"slack_send_message", "jira_add_comment", "jira_create_followup_ticket"}
)
OFFICIAL = "official"
CODES = (
    "m8_args_registration_not_committed",
    "m8_args_input_missing",
    "m8_args_input_invalid",
    "m8_args_unknown_task",
    "m8_args_duplicate_run",
    "m8_args_out_overlaps_input",
    "m8_args_out_exists",
    "m8_args_url_env_unset",
    "m8_args_db_connect_failed",
    "m8_args_thread_read_failed",
    "m8_args_checkpoint_shape",
    "m8_args_denominator_zero",
)
COUNTERS = (
    "calls_without_id",
    "arguments_parse_failed",
    "ok_unpaired",
    "unlinked_rows",
    "checkpoint_missing",
    "rows_without_calls",
    "multi_thread_rows",
)


class _Failure(Exception):
    """Only preregistered diagnostics cross the CLI boundary."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _Failure("m8_args_input_invalid")


@dataclass(frozen=True)
class CallRow:
    group: str
    result_dir: str
    task_id: str
    run_index: int
    call_index: int
    tool: str
    kind: str
    ok: bool | None
    arguments_ok: bool
    ids: int
    grounded: int


@dataclass(frozen=True)
class RunRecord:
    group: str
    result_dir: str
    task_id: str
    run_index: int
    observed_calls: int
    assistant_calls: int
    unlinked: bool
    checkpoint_missing: bool
    without_calls: bool
    multi_thread: bool


def _field(value, name, default=None):
    return (
        value.get(name, default)
        if isinstance(value, dict)
        else getattr(value, name, default)
    )


def _strings(value) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def argument_ids(arguments) -> tuple[list[str], bool]:
    """IDs from every string value of one call's arguments; flag parse success."""
    try:
        parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
    except ValueError:
        return [], False
    if not isinstance(parsed, (dict, list)):
        return [], False
    found: list[str] = []
    for text in _strings(parsed):
        found.extend(extract_citations(text))
    return list(dict.fromkeys(found)), True


def _has_calls(messages) -> bool:
    return any(
        _role(message) in {"assistant", "ai"} and _calls(message)
        for message in messages
    )


def _role(message):
    return (
        _field(message, "role")
        if isinstance(message, dict)
        else _field(message, "type")
    )


def _calls(message) -> list:
    calls = _field(message, "tool_calls", [])
    return list(calls) if isinstance(calls, (list, tuple)) else []


def thread_messages(checkpoint) -> list:
    if not isinstance(checkpoint, dict):
        raise _Failure("m8_args_checkpoint_shape")
    values = checkpoint.get("channel_values", {})
    messages = values.get("messages", []) if isinstance(values, dict) else None
    if not isinstance(messages, (list, tuple)):
        raise _Failure("m8_args_checkpoint_shape")
    return list(messages)


def extract_call_facts(messages, request: str) -> list[dict]:
    """Evidence before the assistant message holding the call (registration §5)."""
    evidence = set(extract_citations(request))
    facts = []
    for message in messages:
        role = _role(message)
        if role in {"assistant", "ai"}:
            for call in _calls(message):
                function = _field(call, "function")
                name = _field(function, "name") if function is not None else None
                if name is None:
                    name = _field(call, "name")
                arguments = (
                    _field(function, "arguments")
                    if function is not None
                    else _field(call, "args")
                )
                ids, parsed = argument_ids(arguments)
                facts.append(
                    {
                        "tool": name if isinstance(name, str) else "",
                        "parsed": parsed,
                        "ids": len(ids),
                        "grounded": sum(i in evidence for i in ids),
                    }
                )
        elif role == "tool":
            content = _field(message, "content")
            if isinstance(content, str):
                evidence.update(extract_citations(content))
    return facts


def build_rows(
    *, group, result_dir, spec, observation, checkpoints: list
) -> tuple[list[CallRow], RunRecord]:
    """Rows for one run from its linked threads' checkpoints (None = missing)."""
    chosen = None
    with_calls = 0
    present = 0
    for checkpoint in checkpoints:
        if checkpoint is None:
            continue
        present += 1
        messages = thread_messages(checkpoint)
        if _has_calls(messages):
            with_calls += 1
            if chosen is None:
                chosen = messages
    facts = extract_call_facts(chosen, spec.request) if chosen else []
    observed_writes = [c for c in observation.tool_calls if c.name in WRITE_TOOLS]
    rows, write_position = [], 0
    for index, fact in enumerate(facts):
        kind = "write" if fact["tool"] in WRITE_TOOLS else "read"
        ok = None
        if kind == "write":
            match = (
                observed_writes[write_position]
                if write_position < len(observed_writes)
                else None
            )
            write_position += 1
            if match is not None and match.name == fact["tool"]:
                ok = bool(match.ok)
        rows.append(
            CallRow(
                group,
                result_dir,
                observation.task_id,
                observation.run_index,
                index,
                fact["tool"],
                kind,
                ok,
                fact["parsed"],
                fact["ids"],
                fact["grounded"],
            )
        )
    run = RunRecord(
        group,
        result_dir,
        observation.task_id,
        observation.run_index,
        len(observation.tool_calls),
        len(facts),
        unlinked=not checkpoints,
        checkpoint_missing=bool(checkpoints) and present < len(checkpoints),
        without_calls=not facts,
        multi_thread=with_calls > 1,
    )
    return rows, run


def _pair(numerator: int, denominator: int) -> dict:
    return {"n": numerator, "d": denominator}


def _counts(rows: list[dict]) -> dict:
    with_id = [r for r in rows if r["ids"] > 0]
    return {
        "calls": len(rows),
        "calls_with_id": len(with_id),
        "ids": sum(r["ids"] for r in rows),
        "grounded": sum(r["grounded"] for r in rows),
        "ungrounded_calls": sum(r["grounded"] < r["ids"] for r in with_id),
        "calls_without_id": sum(r["ids"] == 0 and r["arguments_ok"] for r in rows),
        "arguments_parse_failed": sum(not r["arguments_ok"] for r in rows),
    }


def _block(rows: list[dict]) -> dict:
    counts = _counts(rows)
    return {
        **counts,
        "argument_id_grounded_rate": _pair(counts["grounded"], counts["ids"]),
        "ungrounded_call_rate": _pair(
            counts["ungrounded_calls"], counts["calls_with_id"]
        ),
    }


def summarize_group(rows: list[dict], runs: list[dict], *, strict: bool = True) -> dict:
    """Group summary computed only from call rows and run records."""
    writes = [r for r in rows if r["kind"] == "write"]
    ok_split = {
        "true": [r for r in writes if r["ok"] is True],
        "false": [r for r in writes if r["ok"] is False],
        "unpaired": [r for r in writes if r["ok"] is None],
    }
    summary = {
        "read": _block([r for r in rows if r["kind"] == "read"]),
        "write": _block(writes),
        "write_by_ok": {key: _block(value) for key, value in ok_split.items()},
        "total_reference": _block(rows),
        "counters": {
            "calls_without_id": sum(r["ids"] == 0 and r["arguments_ok"] for r in rows),
            "arguments_parse_failed": sum(not r["arguments_ok"] for r in rows),
            "ok_unpaired": len(ok_split["unpaired"]),
            "unlinked_rows": sum(r["unlinked"] for r in runs),
            "checkpoint_missing": sum(r["checkpoint_missing"] for r in runs),
            "rows_without_calls": sum(r["without_calls"] for r in runs),
            "multi_thread_rows": sum(r["multi_thread"] for r in runs),
        },
        "rows": len(runs),
        "observed_calls": sum(r["observed_calls"] for r in runs),
        "assistant_calls": sum(r["assistant_calls"] for r in runs),
    }
    summary["call_count_difference"] = (
        summary["assistant_calls"] - summary["observed_calls"]
    )
    summary["rows_with_call_count_difference"] = sum(
        r["observed_calls"] != r["assistant_calls"] for r in runs
    )
    if strict and summary["total_reference"]["ids"] == 0:
        raise _Failure("m8_args_denominator_zero")
    return summary


def summarize(rows: list[dict], runs: list[dict]) -> dict:
    """Official group once, each reference directory separately; never merged."""
    groups: dict[str, tuple[list, list]] = {}
    for row in rows:
        key = OFFICIAL if row["group"] == OFFICIAL else row["result_dir"]
        groups.setdefault(key, ([], []))[0].append(row)
    for run in runs:
        key = OFFICIAL if run["group"] == OFFICIAL else run["result_dir"]
        groups.setdefault(key, ([], []))[1].append(run)
    result = {key: summarize_group(*value) for key, value in groups.items()}
    result["directories"] = {
        directory: summarize_group(
            [r for r in rows if r["result_dir"] == directory],
            [r for r in runs if r["result_dir"] == directory],
            strict=False,
        )
        for directory in sorted({r["result_dir"] for r in runs})
    }
    return result


def _cell(pair: dict) -> str:
    return f"{pair['n']}/{pair['d']}"


def render_markdown(report: dict) -> str:
    lines = [
        f"# {_LABEL}",
        "",
        'scope: "m8"',
        "",
        "인자 ID 근거율은 호출 시점 이전 근거(요청 문장 + 앞선 tool 메시지)에 인자 ID가 있는 비율이다.",
        "정답 인자 대조가 아니며 합계는 참고용이다. 쓰기 ok는 제출 초안의 응답에 error가 없다는 뜻이다.",
        "",
        (
            "| 대상 | 읽기 근거율 | 쓰기 근거율 | 합계 근거율(참고) | 읽기 근거 없는 호출 | "
            "쓰기 근거 없는 호출 | 합계 근거 없는 호출(참고) |"
        ),
        "|---|---|---|---|---|---|---|",
    ]
    for name, group in report["groups"].items():
        lines.append(
            f"| {name} | {_cell(group['read']['argument_id_grounded_rate'])} | "
            f"{_cell(group['write']['argument_id_grounded_rate'])} | "
            f"{_cell(group['total_reference']['argument_id_grounded_rate'])} | "
            f"{_cell(group['read']['ungrounded_call_rate'])} | "
            f"{_cell(group['write']['ungrounded_call_rate'])} | "
            f"{_cell(group['total_reference']['ungrounded_call_rate'])} |"
        )
    lines += [
        "",
        "| 대상 | 쓰기 ok=true 근거율 | 쓰기 ok=false 근거율 | 쓰기 ok 미상 근거율 |",
        "|---|---|---|---|",
    ]
    for name, group in report["groups"].items():
        split = group["write_by_ok"]
        lines.append(
            f"| {name} | {_cell(split['true']['argument_id_grounded_rate'])} | "
            f"{_cell(split['false']['argument_id_grounded_rate'])} | "
            f"{_cell(split['unpaired']['argument_id_grounded_rate'])} |"
        )
    lines += [
        "",
        (
            "| 대상 | 행 | 관측 호출 | assistant 호출 | 차이 | ID 없는 호출 | 파싱 실패 | "
            "ok 미상 | 연결 없음 | 체크포인트 없음 | 호출 없는 행 | 다중 thread |"
        ),
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, group in report["groups"].items():
        c = group["counters"]
        lines.append(
            f"| {name} | {group['rows']} | {group['observed_calls']} | "
            f"{group['assistant_calls']} | {group['call_count_difference']} | "
            f"{c['calls_without_id']} | {c['arguments_parse_failed']} | "
            f"{c['ok_unpaired']} | {c['unlinked_rows']} | {c['checkpoint_missing']} | "
            f"{c['rows_without_calls']} | {c['multi_thread_rows']} |"
        )
    return "\n".join(lines) + "\n"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _registration_commit(repo_root: Path) -> str:
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()

    try:
        git("cat-file", "-e", f"HEAD:{_REGISTRATION}")
        commits = git("log", "--format=%H", "HEAD", "--", _REGISTRATION).splitlines()
        dirty = git(
            "status", "--porcelain", "--untracked-files=all", "--", _REGISTRATION
        )
        if len(commits) != 1 or dirty:
            raise _Failure("m8_args_registration_not_committed")
        return commits[0]
    except (OSError, subprocess.SubprocessError):
        raise _Failure("m8_args_registration_not_committed") from None


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise _Failure("m8_args_input_missing") from None
    except (OSError, ValueError, UnicodeError):
        raise _Failure("m8_args_input_invalid") from None


def _load_specs(path: Path) -> dict[str, TaskSpec]:
    rows = _read_json(path)
    try:
        if not isinstance(rows, list):
            raise TypeError
        specs = [TaskSpec.model_validate(row) for row in rows]
        result = {spec.task_id: spec for spec in specs}
        if len(result) != len(specs):
            raise ValueError
        return result
    except (ValueError, TypeError):
        raise _Failure("m8_args_input_invalid") from None


def _input_digest(directory: Path) -> dict:
    entries = []
    for pattern in ("attempts/*/attempt.json", "attempts/*/audit/chain-*.jsonl"):
        for path in sorted(directory.glob(pattern)):
            entries.append((path.relative_to(directory).as_posix(), _sha256(path)))
    return {
        "observations.jsonl": _sha256(directory / "observations.jsonl"),
        "attempts": hashlib.sha256(
            "".join(f"{name}\0{digest}\n" for name, digest in entries).encode()
        ).hexdigest(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__)
    parser.add_argument("--official", type=Path, nargs="+", default=[])
    parser.add_argument("--reference", type=Path, nargs="+", default=[])
    parser.add_argument("--specs", type=Path, default=Path("data/eval/tasks.json"))
    parser.add_argument("--out", type=Path, default=Path("data/interim/m8-args"))
    parser.add_argument("--dbname")
    parser.add_argument("--url-env", default="GOVOPS_DATABASE_URL")
    return parser


def _protect_inputs(out: Path, directories: list[Path], specs: Path) -> None:
    target = out.resolve()
    if any(
        target.is_relative_to(p.resolve()) or p.resolve().is_relative_to(target)
        for p in [*directories, specs]
    ):
        raise _Failure("m8_args_out_overlaps_input")
    if out.exists() or out.is_symlink():
        raise _Failure("m8_args_out_exists")


async def _read_checkpoints(saver, thread_ids: list[str]) -> dict:
    checkpoints = {}
    for thread_id in thread_ids:
        try:
            item = await asyncio.wait_for(
                saver.aget_tuple({"configurable": {"thread_id": thread_id}}), 30
            )
        except Exception:  # noqa: BLE001 — never expose backend messages
            raise _Failure("m8_args_thread_read_failed") from None
        checkpoints[thread_id] = item.checkpoint if item is not None else None
    return checkpoints


def _write_outputs(out: Path, report: dict, rows: list[dict]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".m8-args-", dir=out.parent) as temporary:
        staging = Path(temporary) / "report"
        staging.mkdir()
        files = {
            "report.json": json.dumps(
                report, ensure_ascii=False, sort_keys=True, indent=2
            )
            + "\n",
            "rows.jsonl": "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in rows
            ),
            "report.md": render_markdown(report),
        }
        for name, content in files.items():
            with (staging / name).open("x", encoding="utf-8") as handle:
                handle.write(content)
        if out.exists() or out.is_symlink():
            raise _Failure("m8_args_out_exists")
        staging.rename(out)


async def _measure(args, repo_root: Path, saver_factory) -> dict:
    commit = _registration_commit(repo_root)
    directories = [p.resolve() for p in [*args.official, *args.reference]]
    if not directories:
        raise _Failure("m8_args_input_invalid")
    if len({d.name for d in directories}) != len(directories):
        raise _Failure("m8_args_duplicate_run")
    _protect_inputs(args.out, directories, args.specs)
    try:
        specs_hash = _sha256(args.specs)
    except OSError:
        raise _Failure("m8_args_input_missing") from None
    specs = _load_specs(args.specs)

    url = os.environ.get(args.url_env, "")
    if not url.strip():
        raise _Failure("m8_args_url_env_unset")
    if args.dbname is not None:
        try:
            url = db.url_with_dbname(url, args.dbname)
        except ValueError:
            raise _Failure("m8_args_input_invalid") from None

    planned = []
    for group, paths in ((OFFICIAL, args.official), ("reference", args.reference)):
        for path in paths:
            directory = path.resolve()
            try:
                observations = load_observations(directory / "observations.jsonl")
                links = link_results(directory)
                inputs = _input_digest(directory)
            except FileNotFoundError:
                raise _Failure("m8_args_input_missing") from None
            except (OSError, ValueError, UnicodeError):
                raise _Failure("m8_args_input_invalid") from None
            keys = [(o.task_id, o.run_index) for o in observations]
            if len(set(keys)) != len(keys):
                raise _Failure("m8_args_duplicate_run")
            if any(o.task_id not in specs for o in observations):
                raise _Failure("m8_args_unknown_task")
            link_by_key = {(link.task_id, link.run_index): link for link in links}
            planned.append((group, directory, observations, link_by_key, inputs))

    thread_ids = sorted(
        {
            thread
            for *_, link_by_key, _inputs in planned
            for link in link_by_key.values()
            for thread in link.thread_ids
        }
    )
    try:
        async with saver_factory(url) as saver:
            checkpoints = await _read_checkpoints(saver, thread_ids)
    except _Failure:
        raise
    except Exception:  # noqa: BLE001 — class names only
        raise _Failure("m8_args_db_connect_failed") from None

    rows: list[dict] = []
    runs: list[dict] = []
    for group, directory, observations, link_by_key, _inputs in planned:
        for observation in observations:
            link = link_by_key.get((observation.task_id, observation.run_index))
            threads = list(link.thread_ids) if link else []
            call_rows, run = build_rows(
                group=group,
                result_dir=directory.name,
                spec=specs[observation.task_id],
                observation=observation,
                checkpoints=[checkpoints[t] for t in threads],
            )
            rows.extend(asdict(row) for row in call_rows)
            runs.append(asdict(run))
    groups = summarize(rows, runs)
    directories_summary = groups.pop("directories")
    return {
        "scope": "m8",
        "label": _LABEL,
        "registration_commit": commit,
        "specs": {"sha256": specs_hash},
        "inputs": {d.name: i for _g, d, _o, _l, i in planned},
        "threads_sha256": hashlib.sha256("\n".join(thread_ids).encode()).hexdigest(),
        "thread_count": len(thread_ids),
        "groups": groups,
        "directories": directories_summary,
        "runs": runs,
        "rows": rows,
    }


def main(
    argv: list[str] | None = None, *, repo_root: Path | None = None, saver_factory=None
) -> int:
    try:
        args = build_parser().parse_args(argv)
        report = asyncio.run(
            _measure(
                args,
                repo_root or Path(__file__).resolve().parents[3],
                saver_factory or db.open_readonly_saver,
            )
        )
        rows = report.pop("rows")
        _write_outputs(args.out, report, rows)
    except _Failure as exc:
        print(f"FAIL {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports class only.
        print(f"FAIL unexpected {type(exc).__name__}")
        return 1
    print(f"OK {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
