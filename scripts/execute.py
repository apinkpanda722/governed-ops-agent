#!/usr/bin/env python3
"""
Harness Step Executor — phase 내 step을 Codex로 순차 실행하고 자가 교정한다.

Usage:
    python3 scripts/execute.py <phase-dir> [--steps N] [--push]
"""

import argparse
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
RESULT_SCHEMA = Path(__file__).resolve().parent / "step-result.schema.json"


@contextlib.contextmanager
def progress_indicator(label: str):
    """터미널 진행 표시기. with 문으로 사용하며 .elapsed 로 경과 시간을 읽는다."""
    frames = "◐◓◑◒"
    stop = threading.Event()
    t0 = time.monotonic()

    def _animate():
        idx = 0
        while not stop.wait(0.12):
            sec = int(time.monotonic() - t0)
            sys.stderr.write(f"\r{frames[idx % len(frames)]} {label} [{sec}s]")
            sys.stderr.flush()
            idx += 1
        sys.stderr.write("\r" + " " * (len(label) + 20) + "\r")
        sys.stderr.flush()

    th = threading.Thread(target=_animate, daemon=True)
    th.start()
    info = types.SimpleNamespace(elapsed=0.0)
    try:
        yield info
    finally:
        stop.set()
        th.join()
        info.elapsed = time.monotonic() - t0


class StepExecutor:
    """Phase 디렉토리 안의 step들을 순차 실행하는 하네스.

    커밋과 index.json 갱신은 이 스크립트만 한다. 에이전트(Codex)는 코드만 수정하고
    최종 응답으로 step-result.schema.json 형식의 결과를 돌려준다.
    """

    MAX_RETRIES = 3
    STEP_TIMEOUT = 1800
    VERIFY_CMD = ["make", "verify"]
    VERIFY_TIMEOUT = 600
    FEAT_MSG = "feat({phase}): step {num} — {name}"
    CHORE_MSG = "chore({phase}): step {num} {status}"
    TZ = timezone(timedelta(hours=9))

    def __init__(self, phase_dir_name: str, *, auto_push: bool = False):
        self._root = str(ROOT)
        self._phases_dir = ROOT / "phases"
        self._phase_dir = self._phases_dir / phase_dir_name
        self._phase_dir_name = phase_dir_name
        self._top_index_file = self._phases_dir / "index.json"
        self._auto_push = auto_push

        if not self._phase_dir.is_dir():
            print(f"ERROR: {self._phase_dir} not found")
            sys.exit(1)

        self._index_file = self._phase_dir / "index.json"
        if not self._index_file.exists():
            print(f"ERROR: {self._index_file} not found")
            sys.exit(1)

        idx = self._read_json(self._index_file)
        self._project = idx.get("project", "project")
        self._phase_name = idx.get("phase", phase_dir_name)
        self._total = len(idx["steps"])

    def run(self, max_steps: Optional[int] = None):
        self._print_header()
        self._check_blockers()
        self._check_clean_worktree()
        self._checkout_branch()
        self._ensure_created_at()
        self._execute_all_steps(max_steps)
        if self._all_steps_completed():
            self._finalize()

    # --- timestamps ---

    def _stamp(self) -> str:
        return datetime.now(self.TZ).strftime("%Y-%m-%dT%H:%M:%S%z")

    # --- JSON I/O ---

    @staticmethod
    def _read_json(p: Path) -> dict:
        return json.loads(p.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(p: Path, data: dict):
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _update_step(self, step_num: int, **fields):
        index = self._read_json(self._index_file)
        for s in index["steps"]:
            if s["step"] == step_num:
                s.update(fields)
        self._write_json(self._index_file, index)

    # --- git ---

    def _run_git(self, *args) -> subprocess.CompletedProcess:
        cmd = ["git"] + list(args)
        return subprocess.run(cmd, cwd=self._root, capture_output=True, text=True)

    def _branch_name(self) -> str:
        return f"feature/{self._phase_name}"

    def _checkout_branch(self):
        branch = self._branch_name()

        r = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        if r.returncode != 0:
            print(f"  ERROR: git을 사용할 수 없거나 git repo가 아닙니다.")
            print(f"  {r.stderr.strip()}")
            sys.exit(1)

        if r.stdout.strip() == branch:
            return

        r = self._run_git("rev-parse", "--verify", branch)
        r = self._run_git("checkout", branch) if r.returncode == 0 else self._run_git("checkout", "-b", branch)

        if r.returncode != 0:
            print(f"  ERROR: 브랜치 '{branch}' checkout 실패.")
            print(f"  {r.stderr.strip()}")
            print(f"  Hint: 변경사항을 stash하거나 commit한 후 다시 시도하세요.")
            sys.exit(1)

        print(f"  Branch: {branch}")

    def _check_clean_worktree(self):
        """step 커밋이 무관한 변경사항까지 쓸어담지 않도록, 실행 전 작업 트리가
        phases/ 외에는 깨끗한지 확인한다. 더러우면 즉시 중단한다."""
        r = self._run_git("status", "--porcelain")
        if r.returncode != 0:
            print(f"  ERROR: git status 실행 실패.")
            print(f"  {r.stderr.strip()}")
            sys.exit(1)

        offending = []
        for line in r.stdout.splitlines():
            if not line.strip():
                continue
            path = line[3:].strip()
            if " -> " in path:  # rename entries: "old -> new"
                path = path.split(" -> ", 1)[1]
            if path.startswith("phases/"):
                continue
            offending.append(line)

        if offending:
            print(f"\n  ERROR: 작업 트리가 깨끗하지 않습니다. 아래 변경사항을 먼저 커밋하거나 stash하세요:")
            for line in offending:
                print(f"    {line}")
            sys.exit(1)

    def _commit_code(self, step_num: int, step_name: str):
        """phases/ 를 제외한 코드 변경을 feat 커밋으로 남긴다."""
        self._run_git("add", "-A", "--", ".", ":(exclude)phases")
        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = self.FEAT_MSG.format(phase=self._phase_name, num=step_num, name=step_name)
            r = self._run_git("commit", "-m", msg)
            if r.returncode == 0:
                print(f"  Commit: {msg}")
            else:
                print(f"  WARN: 코드 커밋 실패: {r.stderr.strip()}")

    def _commit_meta(self, step_num: int, status: str):
        """phases/ 메타데이터(index, step 파일)만 chore 커밋으로 남긴다."""
        self._run_git("add", "-A", "--", "phases")
        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = self.CHORE_MSG.format(phase=self._phase_name, num=step_num, status=status)
            r = self._run_git("commit", "-m", msg)
            if r.returncode != 0:
                print(f"  WARN: housekeeping 커밋 실패: {r.stderr.strip()}")

    # --- top-level index ---

    def _update_top_index(self, status: str):
        if not self._top_index_file.exists():
            return
        top = self._read_json(self._top_index_file)
        ts = self._stamp()
        for phase in top.get("phases", []):
            if phase.get("dir") == self._phase_dir_name:
                phase["status"] = status
                ts_key = {"completed": "completed_at", "error": "failed_at", "blocked": "blocked_at"}.get(status)
                if ts_key:
                    phase[ts_key] = ts
                break
        self._write_json(self._top_index_file, top)

    # --- context ---

    @staticmethod
    def _build_step_context(index: dict) -> str:
        lines = [
            f"- Step {s['step']} ({s['name']}): {s['summary']}"
            for s in index["steps"]
            if s["status"] == "completed" and s.get("summary")
        ]
        if not lines:
            return ""
        return "## 이전 Step 산출물\n\n" + "\n".join(lines) + "\n\n"

    def _build_preamble(self, step_context: str, prev_error: Optional[str] = None) -> str:
        # 프로젝트 규칙(AGENTS.md)은 Codex가 자동으로 읽고, docs는 step 파일의
        # "읽어야 할 파일"로 지정하므로 여기서 다시 주입하지 않는다.
        retry_section = ""
        if prev_error:
            retry_section = (
                f"\n## ⚠ 이전 시도 실패 — 아래 에러를 반드시 참고하여 수정하라\n\n"
                f"{prev_error}\n\n---\n\n"
            )
        return (
            f"당신은 {self._project} 프로젝트의 개발자입니다. 아래 step을 수행하세요.\n\n"
            f"{step_context}{retry_section}"
            f"## 작업 규칙\n\n"
            f"1. 이전 step에서 작성된 코드를 확인하고 일관성을 유지하라.\n"
            f"2. 이 step에 명시된 작업만 수행하라. 추가 기능이나 파일을 만들지 마라.\n"
            f"3. 기존 테스트를 깨뜨리지 마라.\n"
            f"4. AC(Acceptance Criteria) 검증을 직접 실행하라. 끝나면 실행기가 `make verify`로 다시 검증한다.\n"
            f"5. git commit 하지 마라. phases/ 아래 파일을 수정하지 마라. 커밋과 상태 기록은 실행기가 한다.\n"
            f"6. 최종 응답은 지정된 JSON 스키마로만 답하라:\n"
            f"   - AC 통과 → status \"completed\", summary에 산출물 한 줄 요약(다음 step에 전달됨), reason null\n"
            f"   - 해결할 수 없는 실패 → status \"error\", reason에 구체적 에러 내용\n"
            f"   - 사용자 개입이 필요한 경우 (API 키, 인증, 수동 설정 등) → status \"blocked\", reason에 구체적 사유\n\n---\n\n"
        )

    # --- Codex 호출 ---

    @staticmethod
    def _parse_usage(jsonl: str) -> dict:
        """codex exec --json 이벤트 스트림에서 turn.completed 의 토큰 사용량을 합산한다."""
        usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
        for line in jsonl.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "turn.completed":
                for k in usage:
                    usage[k] += event.get("usage", {}).get(k, 0)
        return usage

    def _invoke_codex(self, step: dict, preamble: str) -> dict:
        """step 하나를 Codex로 실행하고 {status, summary, reason, usage} 를 반환한다."""
        step_num, step_name = step["step"], step["name"]
        step_file = self._phase_dir / f"step{step_num}.md"

        if not step_file.exists():
            print(f"  ERROR: {step_file} not found")
            sys.exit(1)

        prompt = preamble + step_file.read_text()
        result_file = self._phase_dir / f"step{step_num}-result.json"
        result_file.unlink(missing_ok=True)

        cmd = [
            "codex", "exec", "--json",
            # 워크스페이스 밖 쓰기를 OS 샌드박스로 막는다. 의존성 설치를 위해 네트워크만 연다.
            "--sandbox", "workspace-write",
            "-c", "sandbox_workspace_write.network_access=true",
            # 저장소에 커밋된 .codex/config.toml hook을 신뢰 등록 없이 실행한다.
            "--dangerously-bypass-hook-trust",
            "--output-schema", str(RESULT_SCHEMA),
            "--output-last-message", str(result_file),
            "-",  # 프롬프트는 stdin으로 전달 (인자 길이 제한 회피)
        ]
        try:
            proc = subprocess.run(
                cmd, input=prompt, cwd=self._root,
                capture_output=True, text=True, timeout=self.STEP_TIMEOUT,
            )
            stdout, stderr, exit_code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as e:
            stdout = e.stdout or ""
            stderr = e.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode(errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode(errors="replace")
            exit_code = None

        out_path = self._phase_dir / f"step{step_num}-output.json"
        self._write_json(out_path, {
            "step": step_num, "name": step_name,
            "exitCode": exit_code, "stdout": stdout, "stderr": stderr,
        })

        usage = self._parse_usage(stdout)
        if exit_code is None:
            return {"status": "error", "reason": f"타임아웃 ({self.STEP_TIMEOUT}s 초과)", "usage": usage}
        if exit_code != 0:
            print(f"\n  WARN: Codex가 비정상 종료됨 (code {exit_code})")
        try:
            result = self._read_json(result_file)
            result_file.unlink()  # 내용은 index.json 에 옮겨 적으므로 파일은 남기지 않는다
        except (FileNotFoundError, json.JSONDecodeError):
            tail = (stderr or stdout)[-1000:]
            return {"status": "error", "reason": f"결과 JSON을 받지 못함 (exit {exit_code}): {tail}", "usage": usage}
        result["usage"] = usage
        return result

    def _verify(self) -> Optional[str]:
        """make verify 를 실행해 통과하면 None, 실패하면 출력 끝부분을 반환한다."""
        try:
            r = subprocess.run(
                self.VERIFY_CMD, cwd=self._root,
                capture_output=True, text=True, timeout=self.VERIFY_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return f"`make verify` 타임아웃 ({self.VERIFY_TIMEOUT}s 초과)"
        if r.returncode == 0:
            return None
        return f"`make verify` 실패 (exit {r.returncode}):\n{(r.stdout + r.stderr)[-3000:]}"

    # --- 헤더 & 검증 ---

    def _print_header(self):
        print(f"\n{'='*60}")
        print(f"  Harness Step Executor (Codex)")
        print(f"  Phase: {self._phase_name} | Steps: {self._total}")
        if self._auto_push:
            print(f"  Auto-push: enabled")
        print(f"{'='*60}")

    def _check_blockers(self):
        index = self._read_json(self._index_file)
        for s in reversed(index["steps"]):
            if s["status"] == "error":
                print(f"\n  ✗ Step {s['step']} ({s['name']}) failed.")
                print(f"  Error: {s.get('error_message', 'unknown')}")
                print(f"  Fix and reset status to 'pending' to retry.")
                sys.exit(1)
            if s["status"] == "blocked":
                print(f"\n  ⏸ Step {s['step']} ({s['name']}) blocked.")
                print(f"  Reason: {s.get('blocked_reason', 'unknown')}")
                print(f"  Resolve and reset status to 'pending' to retry.")
                sys.exit(2)
            if s["status"] != "pending":
                break

    def _ensure_created_at(self):
        index = self._read_json(self._index_file)
        if "created_at" not in index:
            index["created_at"] = self._stamp()
            self._write_json(self._index_file, index)

    # --- 실행 루프 ---

    def _execute_single_step(self, step: dict) -> bool:
        """단일 step 실행 (재시도 포함). 완료되면 True. 실패/차단이면 프로세스를 종료한다."""
        step_num, step_name = step["step"], step["name"]
        done = sum(1 for s in self._read_json(self._index_file)["steps"] if s["status"] == "completed")
        prev_error = None
        total_usage = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}

        for attempt in range(1, self.MAX_RETRIES + 1):
            index = self._read_json(self._index_file)
            preamble = self._build_preamble(self._build_step_context(index), prev_error)

            tag = f"Step {step_num}/{self._total - 1} ({done} done): {step_name}"
            if attempt > 1:
                tag += f" [retry {attempt}/{self.MAX_RETRIES}]"

            with progress_indicator(tag) as pi:
                result = self._invoke_codex(step, preamble)
                status = result.get("status")
                reason = result.get("reason") or ""
                # 에이전트의 자기 보고를 그대로 믿지 않고 실행기가 직접 다시 검증한다.
                if status == "completed":
                    verify_error = self._verify()
                    if verify_error:
                        status, reason = "error", verify_error
            elapsed = int(pi.elapsed)  # finally 에서 채워지므로 with 블록 밖에서 읽는다

            for k in total_usage:
                total_usage[k] += result.get("usage", {}).get(k, 0)
            usage = {**total_usage, "attempts": attempt}
            ts = self._stamp()

            if status == "completed":
                self._update_step(step_num, status="completed", summary=result.get("summary", ""),
                                  completed_at=ts, usage=usage)
                self._commit_code(step_num, step_name)
                self._commit_meta(step_num, "completed")
                print(f"  ✓ Step {step_num}: {step_name} [{elapsed}s]")
                return True

            if status == "blocked":
                self._update_step(step_num, status="blocked", blocked_reason=reason,
                                  blocked_at=ts, usage=usage)
                self._update_top_index("blocked")
                self._commit_meta(step_num, "blocked")
                print(f"  ⏸ Step {step_num}: {step_name} blocked [{elapsed}s]")
                print(f"    Reason: {reason}")
                print(f"    코드 변경은 커밋하지 않고 작업 트리에 남겨두었습니다. 검토 후 커밋하거나 stash하고 재실행하세요.")
                sys.exit(2)

            err_msg = reason or "Step did not report a result"
            if attempt < self.MAX_RETRIES:
                prev_error = err_msg
                print(f"  ↻ Step {step_num}: retry {attempt}/{self.MAX_RETRIES} — {err_msg[:200]}")
            else:
                self._update_step(step_num, status="error",
                                  error_message=f"[{self.MAX_RETRIES}회 시도 후 실패] {err_msg}",
                                  failed_at=ts, usage=usage)
                self._update_top_index("error")
                # 실패한 코드는 커밋하지 않는다. 메타데이터만 남기고 작업 트리는 검토용으로 둔다.
                self._commit_meta(step_num, "error")
                print(f"  ✗ Step {step_num}: {step_name} failed after {self.MAX_RETRIES} attempts [{elapsed}s]")
                print(f"    Error: {err_msg[:500]}")
                print(f"    코드 변경은 커밋하지 않고 작업 트리에 남겨두었습니다. 검토 후 커밋하거나 stash하고 재실행하세요.")
                sys.exit(1)

        return False  # unreachable

    def _execute_all_steps(self, max_steps: Optional[int] = None):
        executed = 0
        while True:
            if max_steps is not None and executed >= max_steps:
                print(f"\n  --steps {max_steps} 제한에 도달해 중단합니다.")
                return

            index = self._read_json(self._index_file)
            pending = next((s for s in index["steps"] if s["status"] == "pending"), None)
            if pending is None:
                print("\n  All steps completed!")
                return

            step_num = pending["step"]
            if "started_at" not in pending:
                self._update_step(step_num, started_at=self._stamp())

            self._execute_single_step(pending)
            executed += 1

    def _all_steps_completed(self) -> bool:
        index = self._read_json(self._index_file)
        return all(s["status"] == "completed" for s in index["steps"])

    def _finalize(self):
        index = self._read_json(self._index_file)
        index["completed_at"] = self._stamp()
        self._write_json(self._index_file, index)
        self._update_top_index("completed")

        self._run_git("add", "-A", "--", "phases")
        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = f"chore({self._phase_name}): mark phase completed"
            r = self._run_git("commit", "-m", msg)
            if r.returncode == 0:
                print(f"  ✓ {msg}")

        if self._auto_push:
            branch = self._branch_name()
            r = self._run_git("push", "-u", "origin", branch)
            if r.returncode != 0:
                print(f"\n  ERROR: git push 실패: {r.stderr.strip()}")
                sys.exit(1)
            print(f"  ✓ Pushed to origin/{branch}")

        print(f"\n{'='*60}")
        print(f"  Phase '{self._phase_name}' completed!")
        print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="Harness Step Executor")
    parser.add_argument("phase_dir", help="Phase directory name (e.g. 0-mvp)")
    parser.add_argument("--push", action="store_true", help="Push branch after completion")
    parser.add_argument("--steps", type=int, default=None, help="한 번에 실행할 step 개수 제한 (기본: 전부)")
    args = parser.parse_args()

    StepExecutor(args.phase_dir, auto_push=args.push).run(max_steps=args.steps)


if __name__ == "__main__":
    main()
