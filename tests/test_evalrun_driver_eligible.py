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
