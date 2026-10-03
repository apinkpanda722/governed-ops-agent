"""
execute.py 리팩터링 안전망 테스트.
리팩터링 전후 동작이 동일한지 검증한다.
"""

import json
import os
import subprocess
import sys
import textwrap
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import execute as ex


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_project(tmp_path):
    """phases/, CLAUDE.md, docs/ 를 갖춘 임시 프로젝트 구조."""
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()

    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# Rules\n- rule one\n- rule two")

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "arch.md").write_text("# Architecture\nSome content")
    (docs_dir / "guide.md").write_text("# Guide\nAnother doc")

    return tmp_path


@pytest.fixture
def phase_dir(tmp_project):
    """step 3개를 가진 phase 디렉토리."""
    d = tmp_project / "phases" / "0-mvp"
    d.mkdir()

    index = {
        "project": "TestProject",
        "phase": "mvp",
        "steps": [
            {"step": 0, "name": "setup", "status": "completed", "summary": "프로젝트 초기화 완료"},
            {"step": 1, "name": "core", "status": "completed", "summary": "핵심 로직 구현"},
            {"step": 2, "name": "ui", "status": "pending"},
        ],
    }
    (d / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False))
    (d / "step2.md").write_text("# Step 2: UI\n\nUI를 구현하세요.")

    return d


@pytest.fixture
def top_index(tmp_project):
    """phases/index.json (top-level)."""
    top = {
        "phases": [
            {"dir": "0-mvp", "status": "pending"},
            {"dir": "1-polish", "status": "pending"},
        ]
    }
    p = tmp_project / "phases" / "index.json"
    p.write_text(json.dumps(top, indent=2))
    return p


@pytest.fixture
def executor(tmp_project, phase_dir):
    """테스트용 StepExecutor 인스턴스. git 호출은 별도 mock 필요."""
    with patch.object(ex, "ROOT", tmp_project):
        inst = ex.StepExecutor("0-mvp")
    # 내부 경로를 tmp_project 기준으로 재설정
    inst._root = str(tmp_project)
    inst._phases_dir = tmp_project / "phases"
    inst._phase_dir = phase_dir
    inst._phase_dir_name = "0-mvp"
    inst._index_file = phase_dir / "index.json"
    inst._top_index_file = tmp_project / "phases" / "index.json"
    return inst


# ---------------------------------------------------------------------------
# _stamp (= 이전 now_iso)
# ---------------------------------------------------------------------------

class TestStamp:
    def test_returns_kst_timestamp(self, executor):
        result = executor._stamp()
        assert "+0900" in result

    def test_format_is_iso(self, executor):
        result = executor._stamp()
        dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%S%z")
        assert dt.tzinfo is not None

    def test_is_current_time(self, executor):
        before = datetime.now(ex.StepExecutor.TZ).replace(microsecond=0)
        result = executor._stamp()
        after = datetime.now(ex.StepExecutor.TZ).replace(microsecond=0) + timedelta(seconds=1)
        parsed = datetime.strptime(result, "%Y-%m-%dT%H:%M:%S%z")
        assert before <= parsed <= after


# ---------------------------------------------------------------------------
# _read_json / _write_json
# ---------------------------------------------------------------------------

class TestJsonHelpers:
    def test_roundtrip(self, tmp_path):
        data = {"key": "값", "nested": [1, 2, 3]}
        p = tmp_path / "test.json"
        ex.StepExecutor._write_json(p, data)
        loaded = ex.StepExecutor._read_json(p)
        assert loaded == data

    def test_save_ensures_ascii_false(self, tmp_path):
        p = tmp_path / "test.json"
        ex.StepExecutor._write_json(p, {"한글": "테스트"})
        raw = p.read_text()
        assert "한글" in raw
        assert "\\u" not in raw

    def test_save_indented(self, tmp_path):
        p = tmp_path / "test.json"
        ex.StepExecutor._write_json(p, {"a": 1})
        raw = p.read_text()
        assert "\n" in raw

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ex.StepExecutor._read_json(tmp_path / "nope.json")



# ---------------------------------------------------------------------------
# _build_step_context
# ---------------------------------------------------------------------------

class TestBuildStepContext:
    def test_includes_completed_with_summary(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        result = ex.StepExecutor._build_step_context(index)
        assert "Step 0 (setup): 프로젝트 초기화 완료" in result
        assert "Step 1 (core): 핵심 로직 구현" in result

    def test_excludes_pending(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        result = ex.StepExecutor._build_step_context(index)
        assert "ui" not in result

    def test_excludes_completed_without_summary(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        del index["steps"][0]["summary"]
        result = ex.StepExecutor._build_step_context(index)
        assert "setup" not in result
        assert "core" in result

    def test_empty_when_no_completed(self):
        index = {"steps": [{"step": 0, "name": "a", "status": "pending"}]}
        result = ex.StepExecutor._build_step_context(index)
        assert result == ""

    def test_has_header(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        result = ex.StepExecutor._build_step_context(index)
        assert result.startswith("## 이전 Step 산출물")


# ---------------------------------------------------------------------------
# _update_top_index
# ---------------------------------------------------------------------------

class TestUpdateTopIndex:
    def test_completed(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("completed")
        data = json.loads(top_index.read_text())
        mvp = next(p for p in data["phases"] if p["dir"] == "0-mvp")
        assert mvp["status"] == "completed"
        assert "completed_at" in mvp

    def test_error(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("error")
        data = json.loads(top_index.read_text())
        mvp = next(p for p in data["phases"] if p["dir"] == "0-mvp")
        assert mvp["status"] == "error"
        assert "failed_at" in mvp

    def test_blocked(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("blocked")
        data = json.loads(top_index.read_text())
        mvp = next(p for p in data["phases"] if p["dir"] == "0-mvp")
        assert mvp["status"] == "blocked"
        assert "blocked_at" in mvp

    def test_other_phases_unchanged(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("completed")
        data = json.loads(top_index.read_text())
        polish = next(p for p in data["phases"] if p["dir"] == "1-polish")
        assert polish["status"] == "pending"

    def test_nonexistent_dir_is_noop(self, executor, top_index):
        executor._top_index_file = top_index
        executor._phase_dir_name = "no-such-dir"
        original = json.loads(top_index.read_text())
        executor._update_top_index("completed")
        after = json.loads(top_index.read_text())
        for p_before, p_after in zip(original["phases"], after["phases"]):
            assert p_before["status"] == p_after["status"]

    def test_no_top_index_file(self, executor, tmp_path):
        executor._top_index_file = tmp_path / "nonexistent.json"
        executor._update_top_index("completed")  # should not raise


# ---------------------------------------------------------------------------
# progress_indicator (= 이전 Spinner)
# ---------------------------------------------------------------------------

class TestProgressIndicator:
    def test_context_manager(self):
        import time
        with ex.progress_indicator("test") as pi:
            time.sleep(0.15)
        assert pi.elapsed >= 0.1

    def test_elapsed_increases(self):
        import time
        with ex.progress_indicator("test") as pi:
            time.sleep(0.2)
        assert pi.elapsed > 0


# ---------------------------------------------------------------------------
# main() CLI 파싱 (mocked)
# ---------------------------------------------------------------------------

class TestMainCli:
    def test_no_args_exits(self):
        with patch("sys.argv", ["execute.py"]):
            with pytest.raises(SystemExit) as exc_info:
                ex.main()
            assert exc_info.value.code == 2  # argparse exits with 2

    def test_invalid_phase_dir_exits(self):
        with patch("sys.argv", ["execute.py", "nonexistent"]):
            with patch.object(ex, "ROOT", Path("/tmp/fake_nonexistent")):
                with pytest.raises(SystemExit) as exc_info:
                    ex.main()
                assert exc_info.value.code == 1

    def test_missing_index_exits(self, tmp_project):
        (tmp_project / "phases" / "empty").mkdir()
        with patch("sys.argv", ["execute.py", "empty"]):
            with patch.object(ex, "ROOT", tmp_project):
                with pytest.raises(SystemExit) as exc_info:
                    ex.main()
                assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _check_blockers (= 이전 main() error/blocked 체크)
# ---------------------------------------------------------------------------

class TestCheckBlockers:
    def _make_executor_with_steps(self, tmp_project, steps):
        d = tmp_project / "phases" / "test-phase"
        d.mkdir(exist_ok=True)
        index = {"project": "T", "phase": "test", "steps": steps}
        (d / "index.json").write_text(json.dumps(index))

        with patch.object(ex, "ROOT", tmp_project):
            inst = ex.StepExecutor.__new__(ex.StepExecutor)
        inst._root = str(tmp_project)
        inst._phases_dir = tmp_project / "phases"
        inst._phase_dir = d
        inst._phase_dir_name = "test-phase"
        inst._index_file = d / "index.json"
        inst._top_index_file = tmp_project / "phases" / "index.json"
        inst._phase_name = "test"
        inst._total = len(steps)
        return inst

    def test_error_step_exits_1(self, tmp_project):
        steps = [
            {"step": 0, "name": "ok", "status": "completed"},
            {"step": 1, "name": "bad", "status": "error", "error_message": "fail"},
        ]
        inst = self._make_executor_with_steps(tmp_project, steps)
        with pytest.raises(SystemExit) as exc_info:
            inst._check_blockers()
        assert exc_info.value.code == 1

    def test_blocked_step_exits_2(self, tmp_project):
        steps = [
            {"step": 0, "name": "ok", "status": "completed"},
            {"step": 1, "name": "stuck", "status": "blocked", "blocked_reason": "API key"},
        ]
        inst = self._make_executor_with_steps(tmp_project, steps)
        with pytest.raises(SystemExit) as exc_info:
            inst._check_blockers()
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# _build_preamble
# ---------------------------------------------------------------------------

class TestBuildPreamble:
    def test_includes_project_name(self, executor):
        assert "TestProject" in executor._build_preamble("")

    def test_includes_step_context(self, executor):
        result = executor._build_preamble("## 이전 Step 산출물\n\n- Step 0: done")
        assert "이전 Step 산출물" in result

    def test_does_not_inject_agents_or_docs(self, executor):
        # AGENTS.md 는 Codex가 자동으로 읽고 docs 는 step 파일이 지정한다.
        result = executor._build_preamble("")
        assert "rule one" not in result
        assert "Some content" not in result

    def test_forbids_agent_commit_and_index_edit(self, executor):
        result = executor._build_preamble("")
        assert "git commit 하지 마라" in result
        assert "phases/" in result

    def test_describes_result_statuses(self, executor):
        result = executor._build_preamble("")
        for status in ("completed", "error", "blocked"):
            assert status in result

    def test_no_retry_section_by_default(self, executor):
        assert "이전 시도 실패" not in executor._build_preamble("")

    def test_retry_section_with_prev_error(self, executor):
        result = executor._build_preamble("", prev_error="타입 에러 발생")
        assert "이전 시도 실패" in result
        assert "타입 에러 발생" in result


# ---------------------------------------------------------------------------
# _checkout_branch / _check_clean_worktree (mocked)
# ---------------------------------------------------------------------------

def _mock_git_sequence(executor, responses):
    calls = []
    def fake_git(*args):
        calls.append(args)
        idx = len(calls) - 1
        if idx < len(responses):
            return responses[idx]
        return MagicMock(returncode=0, stdout="", stderr="")
    executor._run_git = fake_git
    return calls


class TestCheckoutBranch:
    def test_branch_name_follows_feature_prefix(self, executor):
        assert executor._branch_name() == "feature/mvp"

    def test_already_on_branch(self, executor):
        calls = _mock_git_sequence(executor, [MagicMock(returncode=0, stdout="feature/mvp\n", stderr="")])
        executor._checkout_branch()
        assert len(calls) == 1

    def test_branch_exists_checkout(self, executor):
        calls = _mock_git_sequence(executor, [
            MagicMock(returncode=0, stdout="main\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ])
        executor._checkout_branch()
        assert calls[-1] == ("checkout", "feature/mvp")

    def test_branch_not_exists_create(self, executor):
        calls = _mock_git_sequence(executor, [
            MagicMock(returncode=0, stdout="main\n", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="not found"),
            MagicMock(returncode=0, stdout="", stderr=""),
        ])
        executor._checkout_branch()
        assert calls[-1] == ("checkout", "-b", "feature/mvp")

    def test_checkout_fails_exits(self, executor):
        _mock_git_sequence(executor, [
            MagicMock(returncode=0, stdout="main\n", stderr=""),
            MagicMock(returncode=1, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="dirty tree"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            executor._checkout_branch()
        assert exc_info.value.code == 1

    def test_no_git_exits(self, executor):
        _mock_git_sequence(executor, [MagicMock(returncode=1, stdout="", stderr="not a git repo")])
        with pytest.raises(SystemExit) as exc_info:
            executor._checkout_branch()
        assert exc_info.value.code == 1


class TestCheckCleanWorktree:
    def test_clean_passes(self, executor):
        _mock_git_sequence(executor, [MagicMock(returncode=0, stdout="", stderr="")])
        executor._check_clean_worktree()

    def test_phases_changes_allowed(self, executor):
        out = " M phases/0-mvp/index.json\n?? phases/1-next/\n"
        _mock_git_sequence(executor, [MagicMock(returncode=0, stdout=out, stderr="")])
        executor._check_clean_worktree()

    def test_other_changes_exit(self, executor):
        out = " M phases/0-mvp/index.json\n M src/app.py\n"
        _mock_git_sequence(executor, [MagicMock(returncode=0, stdout=out, stderr="")])
        with pytest.raises(SystemExit) as exc_info:
            executor._check_clean_worktree()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _commit_code / _commit_meta (mocked)
# ---------------------------------------------------------------------------

class TestCommit:
    def _fake_git(self, executor, staged=True):
        calls = []
        def fake_git(*args):
            calls.append(args)
            if args[:2] == ("diff", "--cached"):
                return MagicMock(returncode=1 if staged else 0)
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git
        return calls

    def test_code_commit_excludes_phases(self, executor):
        calls = self._fake_git(executor)
        executor._commit_code(2, "ui")
        assert calls[0] == ("add", "-A", "--", ".", ":(exclude)phases")
        commit = next(c for c in calls if c[0] == "commit")
        assert commit[2] == "feat(mvp): step 2 — ui"

    def test_meta_commit_only_phases(self, executor):
        calls = self._fake_git(executor)
        executor._commit_meta(2, "error")
        assert calls[0] == ("add", "-A", "--", "phases")
        commit = next(c for c in calls if c[0] == "commit")
        assert commit[2] == "chore(mvp): step 2 error"

    def test_nothing_staged_skips_commit(self, executor):
        calls = self._fake_git(executor, staged=False)
        executor._commit_code(2, "ui")
        assert not any(c[0] == "commit" for c in calls)


# ---------------------------------------------------------------------------
# _parse_usage / _invoke_codex / _verify (mocked)
# ---------------------------------------------------------------------------

CODEX_EVENTS = "\n".join([
    json.dumps({"type": "thread.started", "thread_id": "t"}),
    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 10}}),
    "not json",
    json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5, "cached_input_tokens": 0, "output_tokens": 1}}),
])


class TestParseUsage:
    def test_sums_turn_completed(self):
        assert ex.StepExecutor._parse_usage(CODEX_EVENTS) == {
            "input_tokens": 105, "cached_input_tokens": 40, "output_tokens": 11,
        }

    def test_empty(self):
        assert ex.StepExecutor._parse_usage("") == {
            "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
        }


class TestInvokeCodex:
    def _run_writing_result(self, executor, payload, returncode=0):
        def fake_run(cmd, **kwargs):
            out_file = Path(cmd[cmd.index("--output-last-message") + 1])
            if payload is not None:
                out_file.write_text(json.dumps(payload))
            return MagicMock(returncode=returncode, stdout=CODEX_EVENTS, stderr="")
        return fake_run

    def test_invokes_codex_with_sandbox_and_schema(self, executor):
        payload = {"status": "completed", "summary": "done", "reason": None}
        with patch("subprocess.run", side_effect=self._run_writing_result(executor, payload)) as mock_run:
            executor._invoke_codex({"step": 2, "name": "ui"}, "PREAMBLE\n")

        cmd = mock_run.call_args[0][0]
        kwargs = mock_run.call_args[1]
        assert cmd[:3] == ["codex", "exec", "--json"]
        assert cmd[cmd.index("--sandbox") + 1] == "workspace-write"
        assert "--dangerously-bypass-approvals-and-sandbox" not in cmd
        assert cmd[cmd.index("--output-schema") + 1] == str(ex.RESULT_SCHEMA)
        assert cmd[-1] == "-"
        assert "PREAMBLE" in kwargs["input"]
        assert "UI를 구현하세요" in kwargs["input"]
        assert kwargs["timeout"] == ex.StepExecutor.STEP_TIMEOUT

    def test_returns_result_with_usage(self, executor):
        payload = {"status": "completed", "summary": "done", "reason": None}
        with patch("subprocess.run", side_effect=self._run_writing_result(executor, payload)):
            result = executor._invoke_codex({"step": 2, "name": "ui"}, "p")
        assert result["status"] == "completed"
        assert result["summary"] == "done"
        assert result["usage"]["input_tokens"] == 105
        assert not (executor._phase_dir / "step2-result.json").exists()

    def test_saves_output_json(self, executor):
        payload = {"status": "completed", "summary": "done", "reason": None}
        with patch("subprocess.run", side_effect=self._run_writing_result(executor, payload)):
            executor._invoke_codex({"step": 2, "name": "ui"}, "p")
        data = json.loads((executor._phase_dir / "step2-output.json").read_text())
        assert data["step"] == 2
        assert data["exitCode"] == 0

    def test_missing_result_is_error(self, executor):
        with patch("subprocess.run", side_effect=self._run_writing_result(executor, None, returncode=1)):
            result = executor._invoke_codex({"step": 2, "name": "ui"}, "p")
        assert result["status"] == "error"
        assert "결과 JSON" in result["reason"]

    def test_stale_result_is_removed_before_run(self, executor):
        (executor._phase_dir / "step2-result.json").write_text(
            json.dumps({"status": "completed", "summary": "old", "reason": None}))
        with patch("subprocess.run", side_effect=self._run_writing_result(executor, None, returncode=1)):
            result = executor._invoke_codex({"step": 2, "name": "ui"}, "p")
        assert result["status"] == "error"

    def test_timeout_is_error_not_crash(self, executor):
        err = subprocess.TimeoutExpired(cmd="codex", timeout=1800, output=b"", stderr=b"")
        with patch("subprocess.run", side_effect=err):
            result = executor._invoke_codex({"step": 2, "name": "ui"}, "p")
        assert result["status"] == "error"
        assert "타임아웃" in result["reason"]

    def test_nonexistent_step_file_exits(self, executor):
        with pytest.raises(SystemExit) as exc_info:
            executor._invoke_codex({"step": 99, "name": "nonexistent"}, "p")
        assert exc_info.value.code == 1


class TestVerify:
    def test_pass_returns_none(self, executor):
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="ok", stderr="")) as mock_run:
            assert executor._verify() is None
        assert mock_run.call_args[0][0] == ["make", "verify"]

    def test_fail_returns_output(self, executor):
        with patch("subprocess.run", return_value=MagicMock(returncode=2, stdout="FAILED test_x", stderr="")):
            assert "FAILED test_x" in executor._verify()

    def test_timeout_returns_message(self, executor):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="make", timeout=600)):
            assert "타임아웃" in executor._verify()


# ---------------------------------------------------------------------------
# _execute_single_step / _execute_all_steps (mocked)
# ---------------------------------------------------------------------------

def _step(executor, num):
    return next(s for s in json.loads(executor._index_file.read_text())["steps"] if s["step"] == num)


class TestExecuteSingleStep:
    def _wire(self, executor, results, verify_results=None):
        results = list(results)
        verify_results = list(verify_results or [])
        executor._invoke_codex = MagicMock(side_effect=results)
        executor._verify = MagicMock(side_effect=verify_results or [None] * len(results))
        executor._commit_code = MagicMock()
        executor._commit_meta = MagicMock()
        executor._update_top_index = MagicMock()

    def _res(self, status, summary="", reason=None):
        return {"status": status, "summary": summary, "reason": reason,
                "usage": {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 1}}

    def test_completed_and_verified_commits(self, executor):
        self._wire(executor, [self._res("completed", "UI 완료")])
        assert executor._execute_single_step({"step": 2, "name": "ui"}) is True
        s = _step(executor, 2)
        assert s["status"] == "completed"
        assert s["summary"] == "UI 완료"
        assert s["usage"] == {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 1, "attempts": 1}

    def test_elapsed_is_measured(self, executor, capsys):
        self._wire(executor, [self._res("completed", "UI 완료")])
        with patch.object(ex, "progress_indicator") as pi:
            pi.return_value.__enter__.return_value = types.SimpleNamespace(elapsed=0.0)
            def set_elapsed(*a):
                pi.return_value.__enter__.return_value.elapsed = 42.0
            pi.return_value.__exit__.side_effect = set_elapsed
            executor._execute_single_step({"step": 2, "name": "ui"})
        assert "[42s]" in capsys.readouterr().out
        executor._commit_code.assert_called_once_with(2, "ui")
        executor._commit_meta.assert_called_once_with(2, "completed")

    def test_self_report_rejected_when_verify_fails(self, executor):
        self._wire(executor,
                   [self._res("completed", "a"), self._res("completed", "b")],
                   verify_results=["`make verify` 실패: FAILED test_x", None])
        executor._execute_single_step({"step": 2, "name": "ui"})
        prompt_on_retry = executor._invoke_codex.call_args_list[1][0][1]
        assert "FAILED test_x" in prompt_on_retry
        s = _step(executor, 2)
        assert s["status"] == "completed"
        assert s["usage"]["attempts"] == 2
        assert s["usage"]["input_tokens"] == 20

    def test_final_failure_does_not_commit_code(self, executor):
        self._wire(executor, [self._res("error", reason="boom")] * ex.StepExecutor.MAX_RETRIES)
        with pytest.raises(SystemExit) as exc_info:
            executor._execute_single_step({"step": 2, "name": "ui"})
        assert exc_info.value.code == 1
        s = _step(executor, 2)
        assert s["status"] == "error"
        assert "boom" in s["error_message"]
        executor._commit_code.assert_not_called()
        executor._commit_meta.assert_called_once_with(2, "error")
        executor._update_top_index.assert_called_once_with("error")

    def test_blocked_exits_2_without_code_commit(self, executor):
        self._wire(executor, [self._res("blocked", reason="API 키 필요")])
        with pytest.raises(SystemExit) as exc_info:
            executor._execute_single_step({"step": 2, "name": "ui"})
        assert exc_info.value.code == 2
        s = _step(executor, 2)
        assert s["status"] == "blocked"
        assert s["blocked_reason"] == "API 키 필요"
        executor._commit_code.assert_not_called()
        executor._verify.assert_not_called()


class TestExecuteAllSteps:
    def test_max_steps_limits_execution(self, executor):
        executor._execute_single_step = MagicMock()
        executor._execute_all_steps(max_steps=0)
        executor._execute_single_step.assert_not_called()

    def test_records_started_at(self, executor):
        def complete(step):
            executor._update_step(step["step"], status="completed")
        executor._execute_single_step = MagicMock(side_effect=complete)
        executor._execute_all_steps()
        assert "started_at" in _step(executor, 2)
        assert executor._all_steps_completed()
