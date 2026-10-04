import httpx
import pytest

from govops.api.cli import main


@pytest.mark.parametrize(
    "mode", ["completed", "failed", "timeout", "401", "connection"]
)
def test_cli(mode, capsys):
    calls = []
    sleeps = []

    def handle(request):
        calls.append(request)
        if mode == "connection":
            raise httpx.ConnectError("PRIVATE-TOKEN", request=request)
        if mode == "401":
            return httpx.Response(401, json={"detail": "PRIVATE-TOKEN"})
        if request.url.path == "/sessions":
            return httpx.Response(201, json={"session_token": "PRIVATE-TOKEN"})
        assert request.headers["Authorization"] == "Bearer PRIVATE-TOKEN"
        if request.method == "POST":
            return httpx.Response(202, json={"task_id": "a" * 32})
        status = "running" if mode == "timeout" or len(calls) == 3 else mode
        return httpx.Response(
            200,
            json={
                "status": status,
                "answer": "답변",
                "cited_doc_ids": ["syn-one"],
                "observed_doc_ids": ["syn-two"],
                "steps": 2,
                "error": "internal_error" if mode == "failed" else None,
            },
        )

    code = main(
        ["ask", "--as", "a", "조회", "--timeout", "0.02", "--interval", "0.01"],
        transport=httpx.MockTransport(handle),
        sleep=sleeps.append,
    )
    output = capsys.readouterr()
    assert "PRIVATE-TOKEN" not in output.out + output.err
    assert code == (0 if mode == "completed" else 1)
    if mode in {"completed", "failed"}:
        assert all(x in output.out for x in ("답변", "syn-one", "syn-two", "steps"))
        assert sleeps
