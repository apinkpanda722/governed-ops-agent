import importlib

import pytest


@pytest.mark.parametrize(
    "module_name",
    [
        "govops",
        "govops.api",
        "govops.agent",
        "govops.tools",
        "govops.broker",
        "govops.gateway",
        "govops.guardrails",
        "govops.audit",
        "govops.obs",
        "govops.eval",
        "govops.replay",
        "govops.mocks",
        "govops.costs",
    ],
)
def test_package_import(module_name: str) -> None:
    assert importlib.import_module(module_name).__name__ == module_name
