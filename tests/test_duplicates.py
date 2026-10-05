from govops.eval.duplicates import count_effects
from govops.eval.spec import Effect


def test_conservative_effect_counts():
    rows = [
        {
            "metadata": {
                "govops_task": "syn-task",
                "govops_idem": key,
                "govops_approval": "syn-approval",
            }
        }
        for key in ("a", "b")
    ]
    rows += [{"metadata": {}}, {"metadata": {"govops_task": "other"}}]
    counts = count_effects(
        {"slack": {"channels": [{"channel_id": "announce", "messages": rows}]}},
        task_id="syn-task",
        expected=[
            Effect(kind="slack_message", target="announce", count=1, structure={}),
            Effect(kind="jira_comment", target="DAI-1001", count=1, structure={}),
        ],
    )
    assert (counts.duplicates, counts.missing, counts.unapproved, counts.unmarked) == (
        3,
        1,
        2,
        2,
    )
    assert counts.per_key["syn-task", "slack_message", "announce"] == (4, 1)
