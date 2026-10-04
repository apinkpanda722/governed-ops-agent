from govops.tools.contract import find_identity_fields, load_snapshot
from govops.tools.registry import agent_tool_definitions, tool_specs


def test_registry_contract_and_boundaries():
    specs = tool_specs()
    writes = {"jira_add_comment", "jira_create_followup_ticket", "slack_send_message"}
    assert set(specs) == set(load_snapshot()) | writes | {
        "jira_get_ticket",
        "jira_search_tickets",
    }
    for name, spec in specs.items():
        assert spec.side_effect == spec.requires_approval == (name in writes)
        assert not find_identity_fields(spec.input_schema)
        assert spec.input_schema["additionalProperties"] is False
        assert not any(
            word in key.lower()
            for key in spec.input_schema["properties"]
            for word in ("idempotency", "marker", "metadata")
        )
    for name, snapshot in load_snapshot().items():
        assert specs[name].input_schema == snapshot.input_schema
        assert specs[name].description == snapshot.description
        assert specs[name].version == snapshot.version
    assert (
        "parent_ticket_id"
        in specs["jira_create_followup_ticket"].input_schema["required"]
    )
    definitions = agent_tool_definitions()
    assert {d["function"]["name"] for d in definitions} == set(specs) - writes
    assert all(
        not find_identity_fields(d["function"]["parameters"]) for d in definitions
    )
    assert find_identity_fields({"properties": {"user_id": {"type": "string"}}})
