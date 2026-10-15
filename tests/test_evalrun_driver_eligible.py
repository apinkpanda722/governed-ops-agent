import ast
from pathlib import Path

import pytest

from govops.agent.users import UserInfo
from govops.eval.spec import ApprovalStep
from govops.evalrun.driver import ApprovalDriver


def users(*rows):
    return {label: UserInfo(label, team, grade) for label, team, grade in rows}


def test_eligible_uses_higher_grades_and_lexical_order():
    driver = ApprovalDriver(
        None,
        users(
            ("owner", "synthetic", 4),
            ("z", "synthetic", 5),
            ("a", "synthetic", 6),
            ("other", "elsewhere", 4),
            ("low", "synthetic", 3),
        ),
    )
    assert driver._actor("owner", "same_team_eligible") == "a"


@pytest.mark.parametrize(
    "rows", [[], [("other", "elsewhere", 4)], [("low", "synthetic", 3)]]
)
def test_eligible_excludes_requester_other_team_and_low_grade(rows):
    driver = ApprovalDriver(None, users(("owner", "synthetic", 4), *rows))
    with pytest.raises(ValueError, match="^no_approver$"):
        driver._actor("owner", "same_team_eligible")


def test_existing_actors_and_serialization_unchanged():
    driver = ApprovalDriver(
        None,
        users(
            ("owner", "synthetic", 4),
            ("g4", "synthetic", 4),
            ("a5", "synthetic", 5),
            ("other", "elsewhere", 1),
        ),
    )
    for actor, expected in [
        ("requester", "owner"),
        ("same_team_g4", "g4"),
        ("other_team_user", "other"),
    ]:
        assert driver._actor("owner", actor) == expected
        assert ApprovalStep(
            actor=actor, decision="reject", expect_http=None
        ).model_dump() == {
            "actor": actor,
            "decision": "reject",
            "expect_http": None,
        }
    assert (
        ApprovalStep(
            actor="same_team_eligible", decision="reject", expect_http=None
        ).actor
        == "same_team_eligible"
    )


def test_eligible_imports_policy_constant_without_new_grade_literal():
    source = Path("src/govops/evalrun/driver.py").read_text()
    tree = ast.parse(source)
    assert any(
        isinstance(n, ast.ImportFrom)
        and n.module == "govops.agent.approval"
        and any(a.name == "ANNOUNCE_APPROVER_MIN_GRADE" for a in n.names)
        for n in ast.walk(tree)
    )
    branch = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.If)
        and ast.unparse(n.test) == "actor == 'same_team_eligible'"
    )
    assert "ANNOUNCE_APPROVER_MIN_GRADE" in ast.unparse(branch)
    assert not any(
        isinstance(n, ast.Constant) and n.value == 4
        for stmt in branch.body
        for n in ast.walk(stmt)
    )


def _old_actor(all_users, requester, actor):
    """Resolution before the same_team_g4 fallback (reference table)."""
    owner = all_users[requester]
    if actor == "requester":
        return requester
    if actor == "same_team_eligible":
        pool = [
            u.label
            for u in all_users.values()
            if u.team == owner.team and u.grade >= 4 and u.label != requester
        ]
    elif actor == "same_team_g4":
        pool = [
            u.label
            for u in all_users.values()
            if u.team == owner.team and u.grade == 4 and u.label != requester
        ]
    else:
        pool = [u.label for u in all_users.values() if u.team != owner.team]
    return min(pool) if pool else None


def test_all_registered_specs_resolve_and_old_results_unchanged():
    import json

    from govops.agent.users import load_users

    all_users = load_users()
    driver = ApprovalDriver(None, all_users)
    tasks = json.loads(Path("data/eval/tasks.json").read_text())
    assert len(tasks) == 60
    changed = []
    for task in tasks:
        for step in task.get("approval_script", []):
            resolved = driver._actor(task["user"], step["actor"])
            old = _old_actor(all_users, task["user"], step["actor"])
            if old is None:
                changed.append(task["task_id"])
            else:
                assert resolved == old
    # only tasks whose old resolution failed may change
    assert len(changed) <= 1


def test_same_team_g4_falls_back_to_grade_5_when_requester_is_only_grade_4():
    driver = ApprovalDriver(
        None,
        users(
            ("owner", "synthetic", 4),
            ("boss", "synthetic", 5),
            ("a5", "synthetic", 6),
            ("other", "elsewhere", 4),
            ("low", "synthetic", 3),
        ),
    )
    assert driver._actor("owner", "same_team_g4") == "a5"


def test_same_team_g4_prefers_grade_4_and_still_fails_without_eligible():
    rows = [("owner", "synthetic", 4), ("g4", "synthetic", 4), ("g5", "synthetic", 5)]
    assert ApprovalDriver(None, users(*rows))._actor("owner", "same_team_g4") == "g4"
    for extra in ([], [("low", "synthetic", 3)], [("other", "elsewhere", 5)]):
        driver = ApprovalDriver(None, users(("owner", "synthetic", 4), *extra))
        with pytest.raises(ValueError, match="^no_approver$"):
            driver._actor("owner", "same_team_g4")
