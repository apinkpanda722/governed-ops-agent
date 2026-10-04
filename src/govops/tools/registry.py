"""Frozen tool contracts and strictly read-only dispatch."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

from govops.broker.credentials import Audience
from govops.tools.a_mcp import AMcpClient
from govops.tools.contract import load_snapshot
from govops.tools.jira import JiraClient
from govops.tools.slack import SlackClient

TicketId = Annotated[str, Field(pattern=r"^[A-Z]{3}-\d+$")]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class GetTicket(Input):
    ticket_id: TicketId


class SearchTickets(Input):
    query: str | None = None
    project: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    status: str | None = None
    limit: Annotated[int, Field(ge=1, le=20)] = 20


class AddComment(GetTicket):
    body: str


class CreateFollowup(Input):
    parent_ticket_id: TicketId
    title: str
    description: str | None = None
    priority: str | None = None


class SendMessage(Input):
    channel: str
    text: str


_B_MODELS = {
    "jira_get_ticket": GetTicket,
    "jira_search_tickets": SearchTickets,
    "jira_add_comment": AddComment,
    "jira_create_followup_ticket": CreateFollowup,
    "slack_send_message": SendMessage,
}
_WRITES = {"jira_add_comment", "jira_create_followup_ticket", "slack_send_message"}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    side_effect: bool
    requires_approval: bool
    audience: Audience
    version: int


def tool_specs() -> dict[str, ToolSpec]:
    specs = {
        name: ToolSpec(
            name, t.description, t.input_schema, False, False, "kodocs-mcp", t.version
        )
        for name, t in load_snapshot().items()
    }
    descriptions = {
        "jira_get_ticket": "티켓을 조회합니다.",
        "jira_search_tickets": "티켓을 검색합니다.",
        "jira_add_comment": "승인된 티켓 코멘트 초안.",
        "jira_create_followup_ticket": "승인된 후속 티켓 초안.",
        "slack_send_message": "승인된 채널 메시지 초안.",
    }
    for name, model in _B_MODELS.items():
        specs[name] = ToolSpec(
            name,
            descriptions[name],
            model.model_json_schema(),
            name in _WRITES,
            name in _WRITES,
            "mock-slack" if name.startswith("slack_") else "mock-jira",
            1,
        )
    return specs


def agent_tool_definitions() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": s.name,
                "description": s.description,
                "parameters": s.input_schema,
            },
        }
        for s in tool_specs().values()
        if not s.side_effect
    ]


@dataclass(frozen=True)
class ToolOutcome:
    payload: dict
    is_error: bool
    observed_ids: tuple[str, ...]


class ToolNotExecutable(Exception):
    """Unknown or side-effect tools have no executable route."""


def _a_model(spec: ToolSpec) -> type[BaseModel]:
    # The frozen v1 contract has only string, enum, and optional string inputs.
    fields = {}
    for name, schema in spec.input_schema["properties"].items():
        value = schema.get("anyOf", [schema])[0]
        kind = Literal[tuple(value["enum"])] if "enum" in value else str
        if "anyOf" in schema:
            kind = kind | None
        constraints = {k: value[k] for k in ("minLength", "maxLength") if k in value}
        fields[name] = (
            kind,
            Field(
                ... if name in spec.input_schema["required"] else None,
                **{
                    "min_length" if k == "minLength" else "max_length": v
                    for k, v in constraints.items()
                },
            ),
        )
    return create_model(spec.name, __base__=Input, **fields)


def _ids(payload: Any, keys: set[str]) -> tuple[str, ...]:
    found = set()

    def walk(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in keys and isinstance(child, str):
                    found.add(child)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return tuple(sorted(found))


class ToolRuntime:
    def __init__(self, a_client: AMcpClient, jira: JiraClient, slack: SlackClient):
        self.a_client = a_client
        self.jira = jira
        self.slack = slack

    async def call(
        self, name: str, arguments: Mapping[str, Any], *, user_id: str
    ) -> ToolOutcome:
        spec = tool_specs().get(name)
        if spec is None or spec.side_effect:
            raise ToolNotExecutable("Tool is not executable")
        model = _B_MODELS[name] if name in _B_MODELS else _a_model(spec)
        try:
            validated = model.model_validate(dict(arguments))
        except ValidationError:
            return ToolOutcome(
                {
                    "error": {
                        "code": "invalid_argument",
                        "message": "Invalid tool arguments",
                    }
                },
                True,
                (),
            )
        if spec.audience == "kodocs-mcp":
            result = await self.a_client.call_tool(user_id, name, dict(arguments))
            return ToolOutcome(
                result.payload, result.is_error, _ids(result.payload, {"doc_id"})
            )
        args = validated.model_dump()
        if name == "jira_get_ticket":
            payload = await self.jira.get_ticket(user_id, **args)
            if payload is None:
                return ToolOutcome(
                    {"error": {"code": "not_found", "message": "Ticket not found"}},
                    True,
                    (),
                )
        else:
            payload = {"tickets": await self.jira.search_tickets(user_id, **args)}
        return ToolOutcome(payload, False, _ids(payload, {"ticket_id", "parent_id"}))
