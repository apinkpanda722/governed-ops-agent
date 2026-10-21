"""Read-only reconciliation using signed synthetic chains and local traces."""

import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from govops.audit import toolcall_check
from govops.audit.argmac import load_args_mac
from govops.audit.log import AuditLog
from govops.audit.toolcall import ToolCallAuditor
from govops.obs.jsonl import JsonlSink

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)
TURN = "ab" * 16
OTHER = "cd" * 16


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    key_hex = secrets.token_hex(32)
    monkeypatch.setenv("AUDIT_ARGS_HMAC_KEY", key_hex)
    audit_dir = tmp_path / "audit"
    trace_dir = tmp_path / "traces"
    trace_dir.mkdir()
    clock = [NOW]
    # A controlled clock tests the cutoff boundary, not UUID or PII handling.
    audit = AuditLog(audit_dir, clock=lambda: clock[0])
    auditor = ToolCallAuditor(audit, load_args_mac())

    def record(task_id=TURN, status="ok"):
        auditor.record(
            task_id=task_id,
            actor_sub="demo-platform-g3",
            tool_name="search_docs",
            tool_call_id="synthetic-call",
            arguments='{"query":"합성 검색어"}',
            status=status,
        )

    def trace(task_id=TURN, count=1, extra=()):
        rows = [{"kind": "tool_call", "name": "search_docs"}] * count
        (trace_dir / f"{task_id}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in [*rows, *extra]),
            encoding="utf-8",
        )

    return audit_dir, trace_dir, record, trace, clock, key_hex


def invoke(evidence, capsys, *, since=NOW):
    audit_dir, trace_dir, *_ = evidence
    result = toolcall_check.check(audit_dir, trace_dir, since=since)
    exit_code = toolcall_check.main(
        [
            "--audit-dir",
            str(audit_dir),
            "--trace-dir",
            str(trace_dir),
            "--since",
            since.isoformat(),
        ]
    )
    captured = capsys.readouterr()
    assert captured.err == ""
    assert exit_code == (0 if result.ok else 1)
    for private in (str(audit_dir.parent), TURN, OTHER, evidence[-1]):
        assert private not in captured.out
    return result, captured.out


def test_matching_counts_and_exact_pass_line_are_read_only(evidence, capsys):
    audit_dir, trace_dir, record, trace, *_ = evidence
    for status in (
        "ok",
        "tool_error",
        "tool_not_available",
        "invalid_argument",
        "tool_unavailable",
    ):
        record(status=status)
    record(OTHER)
    trace(count=5)
    trace(OTHER)
    files = [*audit_dir.iterdir(), *trace_dir.iterdir()]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files}
    result, output = invoke(evidence, capsys)
    assert result.ok and result.code is None
    assert (result.turns, result.tool_calls, result.spans) == (2, 6, 6)
    assert output == "PASS audit_toolcall_check turns=2 tool_calls=6 spans=6\n"
    assert set(files) == {*audit_dir.iterdir(), *trace_dir.iterdir()}
    assert before == {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in files
    }


def test_one_turn_missing_a_span_cannot_be_hidden_by_another(evidence, capsys):
    _, _, record, trace, *_ = evidence
    record()
    record(OTHER)
    trace(count=0)
    trace(OTHER, count=2)
    result, output = invoke(evidence, capsys)
    assert result.code == "count_mismatch"
    assert output == "FAIL audit_toolcall_check count_mismatch\n"


def test_missing_trace(evidence, capsys):
    evidence[2]()
    result, output = invoke(evidence, capsys)
    assert result.code == "trace_missing"
    assert output == "FAIL audit_toolcall_check trace_missing\n"


def test_no_recent_turns(evidence, capsys):
    evidence[2]()
    result, output = invoke(evidence, capsys, since=NOW + timedelta(seconds=1))
    assert result.code == "no_turns"
    assert output == "FAIL audit_toolcall_check no_turns\n"


def test_broken_chain_line(evidence, capsys):
    audit_dir, _, record, trace, *_ = evidence
    record()
    trace()
    with next(audit_dir.glob("chain-*.jsonl")).open("ab") as stream:
        stream.write(b"not-json\n")
    result, output = invoke(evidence, capsys)
    assert result.code == "audit_unavailable"
    assert output == "FAIL audit_toolcall_check audit_unavailable\n"


def test_missing_audit_directory_is_unavailable(evidence, capsys):
    audit_dir, trace_dir, *_ = evidence
    result = toolcall_check.check(audit_dir / "absent", trace_dir, since=NOW)
    assert result.code == "audit_unavailable"
    assert not result.ok


def test_ignored_spans_and_nonexecuted_calls(evidence, capsys):
    _, _, record, trace, *_ = evidence
    record()
    record(status="proposal_invalid")
    record(status="not_executed")
    trace(
        extra=(
            {"kind": "tool_call", "name": "tool_attempt"},
            {"kind": "graph_node", "name": "search_docs"},
        )
    )
    _, output = invoke(evidence, capsys)
    assert output == "PASS audit_toolcall_check turns=1 tool_calls=1 spans=1\n"


def test_nonexecuted_calls_still_select_a_target_turn(evidence, capsys):
    evidence[2](status="not_executed")
    evidence[3](count=0)
    _, output = invoke(evidence, capsys)
    assert output == "PASS audit_toolcall_check turns=1 tool_calls=0 spans=0\n"


def test_since_selects_turns_then_counts_the_whole_turn(evidence, capsys):
    _, _, record, trace, clock, _ = evidence
    clock[0] = NOW - timedelta(seconds=1)
    record()
    record(OTHER)
    clock[0] = NOW
    record()
    trace(count=2)
    _, output = invoke(evidence, capsys)
    assert output == "PASS audit_toolcall_check turns=1 tool_calls=2 spans=2\n"


def test_only_completed_chain_appends_are_read(evidence, capsys):
    audit_dir, _, record, trace, *_ = evidence
    record()
    trace()
    with next(audit_dir.glob("chain-*.jsonl")).open("ab") as stream:
        stream.write(b'{"event":')
    _, output = invoke(evidence, capsys)
    assert output == "PASS audit_toolcall_check turns=1 tool_calls=1 spans=1\n"


def test_trace_missing_precedes_count_mismatch_and_audit_unavailable(evidence, capsys):
    audit_dir, _, record, trace, *_ = evidence
    record()
    record(OTHER)
    trace(count=0)
    with next(audit_dir.glob("chain-*.jsonl")).open("ab") as stream:
        stream.write(b"not-json\n")
    _, output = invoke(evidence, capsys)
    assert output == "FAIL audit_toolcall_check trace_missing\n"
    trace(OTHER)
    _, output = invoke(evidence, capsys)
    assert output == "FAIL audit_toolcall_check count_mismatch\n"


def test_main_defaults_use_environment_and_recent_hour(evidence, monkeypatch, capsys):
    audit_dir, trace_dir, record, trace, clock, _ = evidence
    clock[0] = datetime.now(UTC) - timedelta(minutes=30)
    record()
    clock[0] = datetime.now(UTC) - timedelta(minutes=90)
    record(OTHER)
    trace()
    monkeypatch.setenv("AUDIT_DIR", str(audit_dir))
    monkeypatch.setenv("GOVOPS_TRACE_DIR", str(trace_dir))
    assert toolcall_check.main([]) == 0
    assert capsys.readouterr().out == (
        "PASS audit_toolcall_check turns=1 tool_calls=1 spans=1\n"
    )


def test_blank_environment_uses_registered_defaults(monkeypatch, capsys):
    monkeypatch.setenv("AUDIT_DIR", " ")
    monkeypatch.setenv("GOVOPS_TRACE_DIR", " ")
    observed = []

    def fake_check(audit_dir, trace_dir, *, since):
        observed.append((audit_dir, trace_dir, since))
        return toolcall_check.CheckResult(code="no_turns")

    monkeypatch.setattr(toolcall_check, "check", fake_check)
    start = datetime.now(UTC) - timedelta(minutes=60)
    assert toolcall_check.main([]) == 1
    end = datetime.now(UTC) - timedelta(minutes=60)
    audit_dir, trace_dir, since = observed[0]
    assert audit_dir == Path("data/interim/audit")
    assert trace_dir == JsonlSink().root
    assert start <= since <= end
    assert capsys.readouterr().out == "FAIL audit_toolcall_check no_turns\n"


@pytest.mark.parametrize(
    "since", ["not-a-date", "2026-10-21T12:00:00", "2026-10-21T12:00:00+09:00"]
)
def test_invalid_since_does_not_echo_input(since, capsys):
    assert toolcall_check.main(["--since", since]) == 1
    captured = capsys.readouterr()
    assert captured.out == "FAIL audit_toolcall_check audit_unavailable\n"
    assert captured.err == ""


def test_output_codes_and_templates_match_registration():
    text = (ROOT / "docs/results/hypotheses-m8-tool-audit.md").read_text()
    section = text.split("## 8.", 1)[1].split("## 9.", 1)[0]
    code_line = next(
        line for line in section.splitlines() if line.startswith("실패 코드는")
    )
    codes = set(re.findall(r"`([a-z_]+)`", code_line))
    assert codes
    assert codes == toolcall_check.FAILURE_CODES
    assert "PASS audit_toolcall_check turns=<n> tool_calls=<n> spans=<n>" in section
    assert toolcall_check.CheckResult(turns=2, tool_calls=3, spans=3).line == (
        "PASS audit_toolcall_check turns=2 tool_calls=3 spans=3"
    )
    for code in codes:
        assert toolcall_check.CheckResult(code=code).line == (
            f"FAIL audit_toolcall_check {code}"
        )
    section12 = text.split("## 12.", 1)[1]
    examples = re.findall(r"^make (audit-toolcall-check)$", section12, re.MULTILINE)
    assert examples == ["audit-toolcall-check"]
    makefile = (ROOT / "Makefile").read_text()
    assert f"\n{examples[0]}:\n" in makefile
