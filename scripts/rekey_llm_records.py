"""LLM 기록의 request_key를 새 방식으로 이전한다(사용자 실행, 네트워크 없음).

기존 방식 키가 기록된 request_key와 모든 호출에서 정확히 일치할 때만 파일을 바꾼다.
기본은 점검만 하고(`--apply` 없이는 쓰지 않는다) 호출 수·기존 키 일치 수·변경 수를 출력한다.
"""

import argparse
import asyncio
import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from govops.agent.graph import build_graph
from govops.golden import GOLDEN_TASKS, load_golden_specs
from govops.mocks.auth import TokenHashStore
from govops.mocks.jira import JiraState
from govops.mocks.seed import load_tickets
from govops.mocks.service import create_rest_app
from govops.mocks.slack import SlackState
from govops.mocks.tokens import generate_tokens
from govops.evalrun.rekey import RekeyAborted, contract_schemas, rekey
from govops.tools.jira import JiraClient
from govops.tools.registry import ToolRuntime
from govops.tools.slack import SlackClient


class _NoA:
    async def call_tool(self, *args, **kwargs):
        raise RuntimeError("A is replayed from recordings")


@asynccontextmanager
async def inprocess_mock(tmp: Path):
    credentials = tmp / "mock.json"
    generate_tokens(
        users=ROOT / "data/seed/users.json", plaintext=credentials, hash_dir=tmp
    )
    os.environ["MOCK_TOKENS_FILE"] = str(credentials)
    jira = JiraState(load_tickets(ROOT / "data/seed/tickets.json"))
    slack = SlackState()
    jira_app = create_rest_app(
        "jira",
        state=jira,
        token_store=TokenHashStore.load(tmp / "mock_jira_token_hashes.json"),
    )
    slack_app = create_rest_app(
        "slack",
        state=slack,
        token_store=TokenHashStore.load(tmp / "mock_slack_token_hashes.json"),
    )
    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=jira_app)) as jh,
        httpx.AsyncClient(transport=httpx.ASGITransport(app=slack_app)) as sh,
    ):
        yield ToolRuntime(
            _NoA(),
            JiraClient("http://127.0.0.1", http_client=jh),
            SlackClient("http://127.0.0.1", http_client=sh),
        )


async def main(apply: bool, llm_dir: Path, a_dir: Path) -> int:
    specs = [load_golden_specs()[t] for t in GOLDEN_TASKS]
    with tempfile.TemporaryDirectory() as tmp:
        async with inprocess_mock(Path(tmp)) as runtime:
            try:
                reports = await rekey(
                    specs,
                    llm_dir=llm_dir,
                    a_dir=a_dir,
                    mock_executor=runtime,
                    graph_factory=build_graph,
                    schemas=contract_schemas(ROOT / "data/contract"),
                    apply=apply,
                )
            except RekeyAborted as error:
                print(f"중단: {error}. 아무 파일도 바꾸지 않았습니다.")
                return 1
    total = sum(r.calls for r in reports)
    matched = sum(r.legacy_matches for r in reports)
    for r in reports:
        print(
            f"{r.task_id}: 호출 {r.calls}, 기존 키 일치 {r.legacy_matches}, 변경 {r.changed}"
        )
    print(
        f"합계: 호출 {total}, 기존 키 일치 {matched}, "
        f"변경 {sum(r.changed for r in reports)}(request_key만, 도구 결과가 없는 첫 호출은 키가 같아 불변)"
        + ("" if apply else " — 점검만 했습니다(--apply로 적용)")
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--llm-dir", type=Path, default=ROOT / "data/replay/llm")
    parser.add_argument("--a-dir", type=Path, default=ROOT / "data/replay/a_run")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.apply, args.llm_dir, args.a_dir)))
