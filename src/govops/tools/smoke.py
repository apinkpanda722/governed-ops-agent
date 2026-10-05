"""User-run live checks with preregistered, independent expectations."""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import random
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx

from govops.audit.log import AuditLog
from govops.broker import credentials
from govops.tools.a_mcp import AMcpClient, check_contract
from govops.tools.contract import (
    ContractMismatchError,
    snapshot_a_commit,
    snapshot_sha256,
)
from govops.tools.effects import EffectRequest, execute_effect
from govops.tools.jira import JiraClient
from govops.tools.markers import has_marker
from govops.tools.outbox import InMemoryOutboxStore, OutboxState
from govops.tools.registry import _ids
from govops.tools.resilience import ToolPolicy
from govops.tools.rest import ToolHttpError
from govops.tools.slack import SlackClient

EXPECTED_DOC = "syn-incident-022"
SAMPLE_SEED = 20261004
SAMPLE_SIZE = 40
EXPECTED_USERS = 38
EXPECTED_CHANNELS = 9
DEMO_USERS = ("demo-platform-g3", "demo-finance-g3")
_ROOT = Path(__file__).resolve().parents[3]
_REACHABILITY = (
    "사용자 도달 확인: docker compose -f infra/docker-compose.yml exec mock-jira "
    "python -c \"import httpx;print(httpx.post('http://host.docker.internal:8765/mcp').status_code)\""
    " (401이면 도달 가능, 연결 오류면 도달 불가)"
)


@dataclass(frozen=True)
class SmokeResult:
    ok: bool
    lines: list[str]


def expected_visible(user: dict, ticket: dict) -> bool:
    """Independent A policy oracle; never consult either service."""
    level = ticket["access_level"]
    if level == "public":
        return True
    teams = ticket["access_teams"]
    if not isinstance(teams, list) or user["team"] not in teams:
        return False
    if level == "team":
        return True
    minimum = ticket["access_min_grade"]
    return level == "restricted" and type(minimum) is int and user["grade"] >= minimum


def select_sample(tickets: list[dict]) -> list[dict]:
    ordered = sorted(tickets, key=lambda t: t["ticket_id"])
    selected = []
    for level, count in [("public", 1), ("team", 2), ("restricted", 2)]:
        for team in sorted({t["owner_team"] for t in ordered}):
            selected.extend(
                [
                    t
                    for t in ordered
                    if t["access_level"] == level and t["owner_team"] == team
                ][:count]
            )
    ids = {t["ticket_id"] for t in selected}
    remaining = [t for t in ordered if t["ticket_id"] not in ids]
    if len(selected) < SAMPLE_SIZE:
        selected.extend(
            random.Random(SAMPLE_SEED).sample(
                remaining, min(SAMPLE_SIZE - len(selected), len(remaining))
            )
        )
    return selected[:SAMPLE_SIZE]


def sample_sha256(tickets: list[dict]) -> str:
    value = json.dumps([t["ticket_id"] for t in tickets], separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


def _identity_ticket(tickets):
    for level in ("team", "restricted"):
        candidates = [
            t
            for t in tickets
            if t["access_level"] == level and t["access_teams"] == ["platform"]
        ]
        if candidates:
            return min(candidates, key=lambda t: t["ticket_id"])
    raise ValueError("identity ticket unavailable")


async def smoke_contract(client: AMcpClient) -> SmokeResult:
    try:
        await check_contract(client)
    except ContractMismatchError as error:
        return SmokeResult(False, ["계약 불일치", *str(error).split("; ")])
    except Exception:  # noqa: BLE001 — do not expose transport or credential details
        return SmokeResult(False, ["계약 검사 실패"])
    return SmokeResult(
        True,
        [
            "계약 일치",
            f"A 기준 커밋: {snapshot_a_commit()}",
            f"snapshot_sha256: {snapshot_sha256()}",
        ],
    )


async def smoke_identity(a_client, jira, tickets: list[dict]) -> SmokeResult:
    lines = []
    try:
        chosen = _identity_ticket(tickets)
        for label in DEMO_USERS:
            user = {"team": label.split("-")[1], "grade": 3}
            result = await a_client.call_tool(
                label, "lookup_id", {"kind": "incident", "value": "INC-2026-012"}
            )
            visible = EXPECTED_DOC in _ids(result.payload, {"doc_id"})
            if result.is_error or visible != (user["team"] == "platform"):
                lines.append(f"{label} {EXPECTED_DOC}: A 불일치")
            actual = await jira.get_ticket(label, chosen["ticket_id"]) is not None
            expected = expected_visible(user, chosen)
            if actual != expected:
                lines.append(f"{label} {chosen['ticket_id']}: mock 불일치")
            if expected != (user["team"] == "platform"):
                lines.append(f"{label} {chosen['ticket_id']}: 시드 기대 가시성 불일치")
    except Exception:  # noqa: BLE001
        lines.append("신원 검사 실패")
    return SmokeResult(not lines, lines or ["신원 분리 일치"])


def _sql(tickets):
    literals = ", ".join("'" + t["ticket_id"].replace("'", "''") + "'" for t in tickets)
    return f"SELECT ticket_id FROM tickets WHERE ticket_id IN ({literals})"


async def smoke_visibility(
    a_client, jira, tickets: list[dict], users: list[dict]
) -> SmokeResult:
    sample = select_sample(tickets)
    lines = [f"표본 SHA-256: {sample_sha256(sample)}"]
    differences = []
    try:
        if not sample or not users:
            raise ValueError("empty input")
        for user in sorted(users, key=lambda u: u["label"]):
            label = user["label"]
            result = await a_client.call_tool(label, "run_sql", {"sql": _sql(sample)})
            if result.is_error or result.payload.get("truncated", False):
                raise ValueError("invalid SQL result")
            column = result.payload["columns"].index("ticket_id")
            a_ids = {row[column] for row in result.payload["rows"]}
            sample_ids = {t["ticket_id"] for t in sample}
            if a_ids - sample_ids:
                differences.append(f"{label}: A 표본 외 ID 불일치")
            for t in sample:
                expected = expected_visible(user, t)
                mock = await jira.get_ticket(label, t["ticket_id"]) is not None
                sides = []
                if mock != expected:
                    sides.append("mock")
                if (t["ticket_id"] in a_ids) != expected:
                    sides.append("A")
                if sides:
                    differences.append(
                        f"{label} {t['ticket_id']}: {', '.join(sides)} 불일치"
                    )
    except Exception:  # noqa: BLE001
        differences.append("가시성 검사 실패 (응답·연결·시드 확인)")
    lines.append(
        f"사용자 {len(users)}명 · 티켓 {len(sample)}건 · 비교 {len(users) * len(sample)}건 · 불일치 {len(differences)}건"
    )
    return SmokeResult(not differences, lines + differences)


async def _invalid_token_status(client, path):
    async def request(http):
        response = await http.get(
            client.base_url + path,
            headers={"Authorization": "Bearer smoke-invalid-credential"},
            timeout=30,
            follow_redirects=False,
        )
        return response.status_code

    if client.http_client is not None:
        return await request(client.http_client)
    async with httpx.AsyncClient(trust_env=False) as http:
        return await request(http)


async def smoke_mock(jira, slack, tickets: list[dict]) -> SmokeResult:
    lines = []
    try:
        chosen = _identity_ticket(tickets)
        for label in DEMO_USERS:
            team = label.split("-")[1]
            user = {"team": team, "grade": 3}
            if (
                await jira.get_ticket(label, chosen["ticket_id"]) is not None
            ) != expected_visible(user, chosen):
                lines.append(f"{label} {chosen['ticket_id']}: mock get 불일치")
            found = await jira.search_tickets(label, query=chosen["title"])
            if (
                chosen["ticket_id"] in {t["ticket_id"] for t in found}
            ) != expected_visible(user, chosen):
                lines.append(f"{label}: mock search 불일치")
            if len(await slack.list_channels(label)) != EXPECTED_CHANNELS:
                lines.append(f"{label}: 채널 수 불일치 (기대 9)")
            await slack.channel_history(label, "team-" + team)
            other = "finance" if team == "platform" else "platform"
            status = 200
            try:
                await slack.channel_history(label, "team-" + other)
            except ToolHttpError as error:
                status = error.status
            if status != 403:
                lines.append(f"{label}: 타 팀 이력 불일치 (기대 403)")
        for client, path in [(jira, "/tickets"), (slack, "/channels")]:
            if await _invalid_token_status(client, path) != 401:
                lines.append(f"{client.audience}: 잘못된 토큰 불일치 (기대 401)")
    except Exception:  # noqa: BLE001
        lines.append("mock 검사 실패")
    return SmokeResult(not lines, lines or ["mock 권한·인증 일치"])


async def smoke_host_header(
    url: str, *, user_id: str, host_header: str = "host.docker.internal:8765"
) -> SmokeResult:
    # httpcore DEBUG traces can include raw request headers. Silence the transport
    # loggers for this request and restore the caller's configuration afterwards.
    names = {"httpx", "httpcore"} | {
        name
        for name in logging.Logger.manager.loggerDict
        if name.startswith(("httpx.", "httpcore."))
    }
    loggers = [
        (logging.getLogger(name), logging.getLogger(name).level) for name in names
    ]
    for logger, _ in loggers:
        logger.setLevel(logging.CRITICAL + 1)
    status = None
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=30) as http:
            request = http.build_request(
                "POST",
                url,
                headers={
                    "Host": host_header,
                    "Accept": "application/json, text/event-stream",
                },
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
            request.headers["Authorization"] = "Bearer " + credentials.credential_for(
                user_id, "kodocs-mcp"
            )
            response = await http.send(request, follow_redirects=False)
            status = response.status_code
    except Exception:  # noqa: BLE001 — never format secret-bearing exceptions
        status = None
    finally:
        for logger, level in loggers:
            logger.setLevel(level)
    if status in (421, 403):
        verdict = "거절됨(가설 일치)"
    elif status is not None and 200 <= status < 300:
        verdict = "수락됨(가설과 다름)"
    else:
        verdict = "판정 불가"
    return SmokeResult(
        status in (421, 403),
        [
            f"상태 코드: {status if status is not None else '연결 실패'}",
            verdict,
            _REACHABILITY,
        ],
    )


async def smoke_effects(jira, slack, tickets, users, *, user_id) -> SmokeResult:
    lines = []
    try:
        user = next(u for u in users if u["label"] == user_id)
        chosen = min(
            (
                t
                for t in tickets
                if t["owner_team"] == user["team"] and expected_visible(user, t)
            ),
            key=lambda t: t["ticket_id"],
        )
        channel, ticket_id = "team-" + user["team"], chosen["ticket_id"]
        cases = [
            (
                "slack_send_message",
                {"channel": channel, "text": "smoke 합성 메시지"},
                slack.channel_history,
                channel,
            ),
            (
                "jira_add_comment",
                {"ticket_id": ticket_id, "body": "smoke 합성 코멘트"},
                jira.ticket_comments,
                ticket_id,
            ),
            (
                "jira_create_followup_ticket",
                {
                    "parent_ticket_id": ticket_id,
                    "title": "smoke 합성 후속 티켓",
                    "description": "smoke 합성 점검",
                    "priority": "low",
                },
                jira.ticket_children,
                ticket_id,
            ),
        ]
        outbox, policy = InMemoryOutboxStore(), ToolPolicy()
        with TemporaryDirectory(prefix="govops-smoke-") as directory:
            audit = AuditLog(Path(directory))
            for tool, args, lookup, target in cases:
                try:
                    req = EffectRequest(
                        "smoke-" + uuid4().hex, user_id, tool, args, "smoke-approved"
                    )
                    deps = {
                        "jira": jira,
                        "slack": slack,
                        "outbox": outbox,
                        "audit": audit,
                        "policy": policy,
                    }
                    first = await execute_effect(req, **deps)
                    row = next(
                        r for r in outbox.rows.values() if r.task_id == req.task_id
                    )
                    visible = has_marker(
                        await lookup(user_id, target), row.idempotency_key
                    )
                    second = await execute_effect(req, **deps)
                    count = sum(
                        has_marker([item], row.idempotency_key)
                        for item in await lookup(user_id, target)
                    )
                    ok = (
                        first.state == OutboxState.SUCCEEDED
                        and first.sends == 1
                        and not first.audit_gap
                        and visible
                        and second.reason == "already_succeeded"
                        and second.sends == 0
                        and not second.audit_gap
                        and count == 1
                    )
                except Exception:  # noqa: BLE001 — never expose bodies or credentials
                    ok = False
                lines.append(("PASS " if ok else "FAIL ") + tool)
    except Exception:  # noqa: BLE001
        return SmokeResult(False, ["FAIL effects"])
    return SmokeResult(all(line.startswith("PASS ") for line in lines), lines)


async def _run(command, user_id):
    url = os.environ.get("KODOCS_MCP_URL")
    if command not in {"mock", "effects"} and not url:
        return SmokeResult(False, ["KODOCS_MCP_URL이 필요합니다"])
    if command == "host-header":
        return await smoke_host_header(url, user_id=user_id)
    a = AMcpClient(url) if url else None
    if command == "contract":
        return await smoke_contract(a)
    tickets = json.loads((_ROOT / "data/seed/tickets.json").read_text())["tickets"]
    jira = JiraClient(os.environ.get("MOCK_JIRA_URL", "http://127.0.0.1:8081"))
    if command == "identity":
        return await smoke_identity(a, jira, tickets)
    if command == "effects":
        users = json.loads((_ROOT / "data/seed/users.json").read_text())["users"]
        slack = SlackClient(os.environ.get("MOCK_SLACK_URL", "http://127.0.0.1:8082"))
        return await smoke_effects(jira, slack, tickets, users, user_id=user_id)
    if command == "mock":
        slack = SlackClient(os.environ.get("MOCK_SLACK_URL", "http://127.0.0.1:8082"))
        return await smoke_mock(jira, slack, tickets)
    users = json.loads((_ROOT / "data/seed/users.json").read_text())["users"]
    if len(users) != EXPECTED_USERS or len(select_sample(tickets)) != SAMPLE_SIZE:
        return SmokeResult(False, ["가시성 입력 불일치: 기대 사용자 38명 × 표본 40건"])
    return await smoke_visibility(a, jira, tickets, users)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "contract",
            "identity",
            "visibility",
            "mock",
            "host-header",
            "effects",
        ],
    )
    parser.add_argument("--user", default=os.environ.get("SMOKE_USER", DEMO_USERS[0]))
    args = parser.parse_args(argv)
    previous_logging = logging.root.manager.disable
    if args.command == "effects":
        logging.disable(logging.CRITICAL)
    try:
        result = asyncio.run(_run(args.command, args.user))
    except Exception:  # noqa: BLE001
        result = SmokeResult(
            False,
            ["FAIL effects"]
            if args.command == "effects"
            else ["스모크 실패: 시드·자격 증명·연결 설정 확인"],
        )
    finally:
        if args.command == "effects":
            logging.disable(previous_logging)
    for line in result.lines:
        print(line)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
