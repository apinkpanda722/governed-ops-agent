"""Sanitize all contract startup failures before server startup."""

from govops.tools.a_mcp import AMcpClient, check_contract


class StartupError(Exception):
    """Contract verification prevented startup."""


async def verify_contract(client: AMcpClient) -> None:
    reason = None
    try:
        await check_contract(client)
    except Exception as error:  # noqa: BLE001 — sanitize all startup failure details
        reason = type(error).__name__
    if reason is not None:
        # Raise outside the handler to discard credential-bearing context.
        raise StartupError(f"Contract startup check failed: {reason}")
