import json

import pytest

from govops.agent.users import UserInfo
from govops.api.tasks import TaskService

DRAFT = {
    "tool": "slack_send_message",
    "args": {"channel": "team-finance", "text": "합성 공유 본문"},
}
DIGEST = "a" * 64


def service():
    return TaskService(None, users={"a": UserInfo("a", "platform", 3)})


def state(status, check_status):
    return {
        "status": status,
        "user_id": "a",
        "draft": DRAFT,
        "draft_sha256": DIGEST,
        "check": {"status": check_status, "reason": "recipient_not_allowed"},
    }


def test_blocked_view_has_no_draft_or_other_team_channel():
    view = service()._view(state("blocked", "blocked"), "t" * 32)
    assert view["status"] == "blocked"
    assert view["check"]["status"] == "blocked"
    assert "draft" not in view
    assert "draft_sha256" not in view
    assert "team-finance" not in json.dumps(view)


@pytest.mark.parametrize("status", ["awaiting_approval", "completed", "rejected"])
def test_other_status_views_keep_draft(status):
    view = service()._view(state(status, "passed"), "t" * 32)
    assert view["draft"] == DRAFT
    assert view["draft_sha256"] == DIGEST
