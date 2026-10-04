"""메모리 또는 앱 DB checkpointer를 여는 비동기 팩토리."""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


@asynccontextmanager
async def open_checkpointer(url: str | None) -> AsyncIterator[BaseCheckpointSaver]:
    """DB 테이블을 준비하고 컨텍스트 종료 시 연결을 닫는다."""
    if url in (None, "", "memory"):
        yield InMemorySaver()
        return

    stack = AsyncExitStack()
    try:
        try:
            saver = await stack.enter_async_context(
                AsyncPostgresSaver.from_conn_string(url)
            )
            await saver.setup()
        except Exception:  # noqa: BLE001 — DB 오류의 자격 증명 노출 방지
            # 공급사 오류에는 비밀번호가 포함될 수 있으므로 체인을 출력하지 않는다.
            raise RuntimeError("checkpointer DB 연결 실패") from None
        yield saver
    finally:
        try:
            await stack.aclose()
        except Exception:  # noqa: BLE001 — DB 오류의 자격 증명 노출 방지
            raise RuntimeError("checkpointer DB 연결 종료 실패") from None
