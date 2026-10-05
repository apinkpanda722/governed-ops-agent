import asyncio
from types import SimpleNamespace

import pytest
from test_outbox import sync

from govops.tools.resilience import FailureKind, ToolPolicy, classify
from govops.tools.rest import ToolHttpError


def test_uncertain_and_control_exceptions():
    assert classify(ToolHttpError(200, "invalid_response")) == FailureKind.UNCERTAIN
    assert classify(ValueError("code failure")) == FailureKind.UNCERTAIN
    for exc in (asyncio.CancelledError(), KeyboardInterrupt()):
        with pytest.raises(type(exc)):
            classify(exc)


@sync
async def test_protocol_no_retry():
    calls = []

    class ProtocolError(Exception):
        kind = "protocol"

    async def call():
        calls.append(1)
        raise ProtocolError()

    with pytest.raises(ProtocolError):
        await ToolPolicy().call_read("a_mcp", call)
    assert len(calls) == 1
    assert (
        classify(SimpleNamespace(kind="protocol", status=None))
        == FailureKind.DEFINITIVE
    )
