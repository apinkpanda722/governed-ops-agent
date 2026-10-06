from govops.evalrun.dev import _Capture
from govops.evalrun.explore import longest_streak
from govops.evalrun.tool_trace import extract_tool_trace, structure


def test_structure_matches_m2_and_drops_bodies():
    payload = {
        "hits": [{"doc_id": "syn-1", "body": "private", "token": "secret"}],
        "ticket_id": "SYN-1",
        "text": "body",
    }
    expected = {"hits": [{"doc_id": "syn-1"}], "ticket_id": "SYN-1"}
    assert structure(payload) == expected
    calls, results = extract_tool_trace(
        [
            {"name": "search_docs", "ok": True, "result": payload},
            {"name": "lookup_id", "ok": False, "result": {"error": {"code": "absent"}}},
        ]
    )
    assert calls == [
        {"name": "search_docs", "ok": True},
        {"name": "lookup_id", "ok": False},
    ]
    assert results[0]["result"] == expected
    assert results[1]["result"] == {"error": {}}
    assert longest_streak(results) == 1
    assert "private" not in repr(results) and "secret" not in repr(results)
    assert _Capture is not None


def test_capture_and_common_extraction_are_identical():
    import asyncio

    from govops.tools.registry import ToolOutcome

    class Tools:
        async def call(self, *args, **kwargs):
            return ToolOutcome(
                {"items": [{"doc_id": "syn-1", "text": "private"}]}, False, ()
            )

    capture = _Capture(Tools())
    asyncio.run(capture.call("search_docs", {}))
    calls, results = extract_tool_trace(capture.results)
    assert results == capture.results
    assert calls == [{"name": "search_docs", "ok": True}]
