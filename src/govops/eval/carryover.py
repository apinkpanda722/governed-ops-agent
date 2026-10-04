"""요청 문장만 바뀐 문항의 이전 판정을 카드에 미리 채우기 위한 비교(읽기 전용).

이전 명세는 마지막으로 커밋된 `data/eval/tasks.json`(`git show HEAD:...`)이다. 이전 확정본의
판정이 그 이전 명세의 해시에 대한 것이고, 새 명세가 요청 문장 외에는 같을 때만 이어받는다.
판정을 만들거나 저장하지 않는다. 사람이 카드에서 확인하고 "결과 저장"을 눌러야 새 해시로 기록된다.
"""

import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from govops.eval.spec import TaskSpec, spec_content_sha256, spec_hash


@dataclass(frozen=True)
class Carried:
    status: str
    note: str
    previous_request: str


def request_only_changes(
    new_specs: Sequence[TaskSpec], old_specs: Sequence[TaskSpec]
) -> dict[str, str]:
    """task_id → 이전 요청 문장. 해시가 바뀌었지만 요청 문장을 뺀 내용은 같은 문항만 담는다.

    다른 것이 하나라도 바뀐 문항(교체·신규 포함)은 담지 않는다.
    """
    old = {s.task_id: s for s in old_specs}
    changes = {}
    for spec in new_specs:
        before = old.get(spec.task_id)
        if (
            before is not None
            and spec_hash(before) != spec_hash(spec)
            and spec_content_sha256(before) == spec_content_sha256(spec)
        ):
            changes[spec.task_id] = before.request
    return changes


def carry_over(
    new_specs: Sequence[TaskSpec],
    old_specs: Sequence[TaskSpec],
    decisions: Mapping[str, Mapping[str, str]],
) -> dict[str, Carried]:
    """task_id → 이어받을 판정. 아래를 모두 만족할 때만 이어받는다.

    1. 요청 문장만 바뀐 문항이다(`request_only_changes`). 요청 문장 외에 하나라도 다르면 아니다.
    2. 이전 판정이 바로 그 이전 명세의 해시에 대한 것이다(더 오래된 명세에 대한 판정은 무효).
    """
    old = {s.task_id: s for s in old_specs}
    found = {}
    for task_id, previous in request_only_changes(new_specs, old_specs).items():
        decision = decisions.get(task_id)
        if decision is not None and decision["spec_sha256"] == spec_hash(old[task_id]):
            found[task_id] = Carried(decision["status"], decision["note"], previous)
    return found


def load_committed_specs(path: Path, ref: str = "HEAD") -> list[TaskSpec] | None:
    """커밋된 명세. git 저장소가 아니거나 커밋·파일이 없으면 None(이어받기 없음)."""
    path = Path(path)
    try:
        root = subprocess.run(
            ["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        relative = path.resolve().relative_to(Path(root).resolve())
        shown = subprocess.run(
            ["git", "-C", root, "show", f"{ref}:{relative.as_posix()}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return [TaskSpec.model_validate(raw) for raw in json.loads(shown)]
    except (OSError, subprocess.CalledProcessError, ValueError, ValidationError):
        return None
