"""Offline, read-only M8 tool-call measurement and input integrity verification."""

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from govops.eval.observation import load_observations
from govops.eval.spec import TaskSpec
from govops.metrics.toolcall import (
    WRITE_TOOLS,
    cross_check_tool_coverage,
    evaluate_run,
    summarize,
)
from govops.metrics.traces import LinkResult, link_reads, read_tool_spans

_REGISTRATION = "docs/results/hypotheses-m8-toolcall.md"
_LABEL = "M8 추가 측정 — 공식 M6 수치 아님"
_METRIC_ERRORS = {"m8_toolcall_duplicate_run", "m8_toolcall_denominator_zero"}


class _Failure(Exception):
    """Only explicitly selected, preregistered diagnostics cross this boundary."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise _Failure("m8_toolcall_input_invalid")


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
            raise _Failure("m8_toolcall_registration_not_committed")
        return commits[0]
    except (OSError, subprocess.SubprocessError):
        raise _Failure("m8_toolcall_registration_not_committed") from None


def _protect_inputs(out: Path, directories: list[Path], specs: Path) -> None:
    target = out.resolve()
    if any(
        target.is_relative_to(p.resolve()) or p.resolve().is_relative_to(target)
        for p in [*directories, specs]
    ):
        raise _Failure("m8_toolcall_out_overlaps_input")
    if out.exists() or out.is_symlink():
        raise _Failure("m8_toolcall_out_exists")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise _Failure("m8_toolcall_input_missing") from None
    except (OSError, ValueError, UnicodeError):
        raise _Failure("m8_toolcall_input_invalid") from None


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
        raise _Failure("m8_toolcall_input_invalid") from None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _input_hashes(directory: Path) -> dict:
    hashes = {
        name: _sha256(directory / name)
        for name in ("observations.jsonl", "grades.json", "manifest.json")
    }
    entries = []
    for path in sorted((directory / "traces").rglob("*")):
        if path.is_file():
            try:
                digest = _sha256(path)
            except OSError:
                # Unreadable traces are unavailable, never silently removed.
                digest = "unreadable"
            entries.append((path.relative_to(directory / "traces").as_posix(), digest))
    hashes["traces"] = hashlib.sha256(
        "".join(f"{name}\0{digest}\n" for name, digest in entries).encode("utf-8")
    ).hexdigest()
    return hashes


def _validate_metadata(grades, manifest) -> None:
    if not isinstance(grades, dict) or not isinstance(manifest, dict):
        raise _Failure("m8_toolcall_input_invalid")
    trace_ids = manifest.get("trace_ids")
    if not isinstance(trace_ids, dict) or any(
        not isinstance(task, str)
        or not isinstance(trace, str)
        or re.fullmatch(r"[0-9a-fA-F]{32}", trace) is None
        for task, trace in trace_ids.items()
    ):
        raise _Failure("m8_toolcall_input_invalid")
    if "repeat_index" in manifest and (
        type(manifest["repeat_index"]) is not int or manifest["repeat_index"] < 1
    ):
        raise _Failure("m8_toolcall_input_invalid")
    for task, grade in grades.items():
        if not isinstance(task, str) or not isinstance(grade, dict):
            raise _Failure("m8_toolcall_input_invalid")
        tcr = grade.get("tcr")
        if (
            not isinstance(tcr, dict)
            or type(tcr.get("tool_coverage")) is not bool
            or type(tcr.get("run_index")) is not int
        ):
            raise _Failure("m8_toolcall_input_invalid")


def _load_run(directory: Path):
    try:
        observations = load_observations(directory / "observations.jsonl")
    except FileNotFoundError:
        raise _Failure("m8_toolcall_input_missing") from None
    except (OSError, ValueError, UnicodeError):
        raise _Failure("m8_toolcall_input_invalid") from None
    grades = _read_json(directory / "grades.json")
    manifest = _read_json(directory / "manifest.json")
    _validate_metadata(grades, manifest)
    return observations, grades, manifest


def _connect(directory, obs, manifest) -> dict:
    trace_id = obs.trace_id or manifest["trace_ids"].get(obs.task_id)
    if trace_id is not None and (
        re.fullmatch(r"[0-9a-fA-F]{32}", trace_id) is None
        or manifest["trace_ids"].get(obs.task_id, trace_id) != trace_id
    ):
        raise _Failure("m8_toolcall_input_invalid")
    reads = [
        (call.name, call.ok) for call in obs.tool_calls if call.name not in WRITE_TOOLS
    ]
    missing = False
    try:
        if trace_id is None:
            raise FileNotFoundError
        spans = read_tool_spans(directory / "traces" / f"{trace_id}.jsonl")
        result = link_reads(reads, spans)
    except (OSError, ValueError):
        missing = True
        result = LinkResult(0, len(reads), {})
    return {
        **asdict(result),
        "unlinked_write": len(obs.tool_calls) - len(reads),
        "unlinked_trace_missing": int(missing),
    }


def _connections(rows) -> dict:
    errors = Counter()
    for row in rows:
        errors.update(row["connection"]["error_codes"])
    return {
        **{
            key: sum(row["connection"][key] for row in rows)
            for key in (
                "linked_reads",
                "unlinked_read",
                "unlinked_write",
                "unlinked_trace_missing",
            )
        },
        "error_codes": dict(sorted(errors.items())),
    }


def _summary(runs) -> dict:
    try:
        return asdict(summarize(runs))
    except ValueError as exc:
        if str(exc) in _METRIC_ERRORS:
            raise _Failure(str(exc)) from None
        raise


def _markdown(report) -> str:
    lines = [
        f"# {_LABEL}",
        "",
        'scope: "m8"',
        "",
        "읽기 ok는 실행된 읽기, 쓰기 ok는 제출 초안의 그래프·정책 응답을 뜻한다.",
        "쓰기 ok는 실제 발송 성공을 뜻하지 않는다. 합계는 참고용이다.",
        "",
        "| 대상 | 필수 호출률 | 금지 호출 건수 | 금지 호출 실행/전체 | 읽기 ok | 쓰기 ok | 합계 ok(참고) | 빈 그룹 제외 |",
        "|---|---|---|---|---|---|---|---|",
    ]

    def fraction(rate):
        return f"{rate['numerator']}/{rate['denominator']}"

    def add(label, summary):
        forbidden = summary["forbidden_call_count"]
        rates = summary["call_ok_rate"]
        # Escape identifiers so arbitrary directory names cannot break table columns.
        label = (
            label.replace("&", "&amp;")
            .replace("|", "&#124;")
            .replace("\n", " ")
            .replace("\r", " ")
        )
        lines.append(
            f"| {label} | {fraction(summary['required_tool_call_rate'])} | {forbidden['count']} | "
            f"{forbidden['forbidden_call_runs']}/{forbidden['total_runs']} | {fraction(rates['reads'])} | "
            f"{fraction(rates['writes'])} | {fraction(rates['total'])} | "
            f"{summary['required_tool_call_rate']['excluded_empty_groups']} |"
        )

    if report["groups"]["official"] is not None:
        add("official 합계", report["groups"]["official"])
    for name, entry in report["directories"].items():
        add(f"{entry['group']} / {name}", entry["summary"])
    connections = report["connections"]
    lines += [
        "",
        "연결 진단(호출 항목, trace_missing만 실행 단위): "
        + ", ".join(
            f"{key}={connections[key]}"
            for key in (
                "linked_reads",
                "unlinked_read",
                "unlinked_write",
                "unlinked_trace_missing",
            )
        ),
        "",
    ]
    return "\n".join(lines)


def _write_output(out: Path, report: dict, rows: list[dict]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    # Publish only complete files, and never reuse an existing output directory.
    with tempfile.TemporaryDirectory(
        prefix=".m8-toolcall-", dir=out.parent
    ) as temporary:
        staging = Path(temporary) / "report"
        staging.mkdir()
        for name, content in {
            "report.json": json.dumps(
                report, ensure_ascii=False, sort_keys=True, indent=2
            )
            + "\n",
            "rows.jsonl": "".join(
                json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                for row in rows
            ),
            "report.md": _markdown(report),
        }.items():
            path = staging / name
            temp_path = staging / f".{name}.tmp"
            with temp_path.open("x", encoding="utf-8") as handle:
                handle.write(content)
            temp_path.rename(path)
        if out.exists() or out.is_symlink():
            raise _Failure("m8_toolcall_out_exists")
        staging.rename(out)


def _toolcall(args, repo_root: Path) -> None:
    commit = _registration_commit(repo_root)
    directories = [p.resolve() for p in [*args.official, *args.reference]]
    _protect_inputs(args.out, directories, args.specs)
    try:
        specs_hash = _sha256(args.specs)
    except FileNotFoundError:
        raise _Failure("m8_toolcall_input_missing") from None
    except OSError:
        raise _Failure("m8_toolcall_input_invalid") from None
    specs = _load_specs(args.specs)
    runs, rows, metadata, inputs, grades_by_dir = [], [], {}, {}, {}
    # Load and evaluate first, then cross-check, connect, and summarize the same rows.
    loaded = []
    seen = set()
    for group, paths in (("official", args.official), ("reference", args.reference)):
        for path in paths:
            directory = path.resolve()
            try:
                inputs[str(directory)] = _input_hashes(directory)
            except FileNotFoundError:
                raise _Failure("m8_toolcall_input_missing") from None
            except OSError:
                raise _Failure("m8_toolcall_input_invalid") from None
            observations, grades, manifest = _load_run(directory)
            if directory.name in metadata:
                raise _Failure("m8_toolcall_duplicate_run")
            metadata[directory.name] = {
                "group": group,
                **{
                    key: manifest[key]
                    for key in (
                        "repeat_index",
                        "run_label",
                        "config",
                        "run_role",
                        "validity",
                    )
                    if key in manifest
                },
            }
            grades_by_dir[directory.name] = grades
            for obs in observations:
                if obs.task_id not in specs:
                    raise _Failure("m8_toolcall_unknown_task")
                run = evaluate_run(specs[obs.task_id], obs, result_dir=directory.name)
                if run.key in seen:
                    raise _Failure("m8_toolcall_duplicate_run")
                seen.add(run.key)
                runs.append(run)
                loaded.append((directory, obs, manifest))
    if not runs:
        raise _Failure("m8_toolcall_denominator_zero")
    if cross_check_tool_coverage(runs, grades_by_dir):
        raise _Failure("m8_toolcall_grade_mismatch")
    for run, (directory, obs, manifest) in zip(runs, loaded, strict=True):
        data = asdict(run)
        rows.append(
            {
                **data.pop("key"),
                **data,
                "connection": _connect(directory, obs, manifest),
            }
        )
    by_directory = {
        name: {
            **meta,
            "summary": _summary([run for run in runs if run.key.result_dir == name]),
            "connections": _connections(
                [row for row in rows if row["result_dir"] == name]
            ),
        }
        for name, meta in metadata.items()
    }
    official = [
        run for run in runs if metadata[run.key.result_dir]["group"] == "official"
    ]
    report = {
        "scope": "m8",
        "label": _LABEL,
        "registration_commit": commit,
        "groups": {
            "official": _summary(official) if args.official else None,
            # N1a and N1b must never be pooled.
            "reference": {
                name: entry["summary"]
                for name, entry in by_directory.items()
                if entry["group"] == "reference"
            },
        },
        "directories": by_directory,
        "connections": _connections(rows),
        "inputs": inputs,
        "specs": {"path": str(args.specs.resolve()), "sha256": specs_hash},
    }
    # Detect input changes during evaluation before publishing any output.
    _check_hashes(report)
    _write_output(args.out.resolve(), report, rows)


def _check_hashes(report) -> None:
    try:
        if (
            not isinstance(report, dict)
            or not isinstance(report.get("inputs"), dict)
            or not report["inputs"]
        ):
            raise _Failure("m8_toolcall_input_invalid")
        for directory, expected in report["inputs"].items():
            if _input_hashes(Path(directory)) != expected:
                raise _Failure("m8_toolcall_hash_mismatch")
        if _sha256(Path(report["specs"]["path"])) != report["specs"]["sha256"]:
            raise _Failure("m8_toolcall_hash_mismatch")
    except OSError:
        raise _Failure("m8_toolcall_hash_mismatch") from None
    except (KeyError, TypeError, ValueError):
        raise _Failure("m8_toolcall_input_invalid") from None


def main(argv: list[str] | None = None, *, repo_root: Path | None = None) -> int:
    parser = _Parser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    measure = commands.add_parser("toolcall")
    measure.add_argument("--official", type=Path, nargs="+", default=[])
    measure.add_argument("--reference", type=Path, nargs="+", default=[])
    measure.add_argument("--specs", type=Path, default=Path("data/eval/tasks.json"))
    measure.add_argument(
        "--out", type=Path, default=Path("data/interim/m8-metrics/toolcall")
    )
    verify = commands.add_parser("verify")
    verify.add_argument("--report", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        if args.command == "verify":
            _check_hashes(_read_json(args.report))
        else:
            if not args.official and not args.reference:
                raise _Failure("m8_toolcall_input_invalid")
            _toolcall(args, repo_root or Path(__file__).resolve().parents[3])
    except _Failure as exc:
        print(f"FAIL {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary reports class only.
        print(f"FAIL unexpected {type(exc).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
