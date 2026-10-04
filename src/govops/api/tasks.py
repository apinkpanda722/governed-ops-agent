"""Background read-only tasks with session owner isolation."""

import asyncio
import re
from uuid import uuid4

from govops.agent.graph import get_task, run_task
from govops.obs.trace import set_attributes, trace

_TASK_ID = re.compile(r"[0-9a-f]{32}")


class TaskService:
    def __init__(self, graph, *, max_request_chars: int = 2000):
        self.graph = graph
        self.max_request_chars = max_request_chars
        self._owners: dict[str, str] = {}
        self._failed: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self._closed = False

    async def start(self, user_id: str, request: str) -> str:
        if not request.strip() or len(request) > self.max_request_chars:
            raise ValueError("Invalid request")
        if self._closed:
            raise RuntimeError("Task service closed")
        task_id = uuid4().hex
        self._owners[task_id] = user_id
        task = asyncio.create_task(self._run(task_id, user_id, request))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task_id

    async def _run(self, task_id: str, user_id: str, request: str) -> None:
        with trace(task_id, user_id=user_id):
            set_attributes(
                **{"input.request": request, "output.answer": None},
                steps=0,
                cited_doc_ids=[],
            )
            try:
                state = await run_task(
                    self.graph, task_id=task_id, user_id=user_id, request=request
                )
            except asyncio.CancelledError:
                self._failed.add(task_id)
                set_attributes(final_status="failed", error_code="cancelled")
                raise
            except Exception:  # noqa: BLE001 — never expose background exception details
                self._failed.add(task_id)
                set_attributes(final_status="failed", error_code="internal_error")
            else:
                set_attributes(
                    final_status=state["status"],
                    steps=state["steps"],
                    error_code=state["error"],
                    cited_doc_ids=state["cited_doc_ids"],
                    **{"output.answer": state["answer"]},
                )

    async def get(self, user_id: str, task_id: str) -> dict | None:
        if not _TASK_ID.fullmatch(task_id):
            return None
        state = await get_task(self.graph, task_id)
        owner = state.get("user_id") if state else self._owners.get(task_id)
        if owner != user_id:
            return None
        state = state or {}
        failed = task_id in self._failed
        return {
            "task_id": task_id,
            "status": "failed" if failed else state.get("status", "running"),
            "answer": state.get("answer"),
            "cited_doc_ids": state.get("cited_doc_ids", []),
            "observed_doc_ids": state.get("observed_ids", []),
            "steps": state.get("steps", 0),
            "error": "internal_error" if failed else state.get("error"),
        }

    async def aclose(self) -> None:
        self._closed = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
