"""Record model tool calls without persisting original names or arguments."""

import hashlib

from govops.audit.argmac import ArgsMac
from govops.audit.log import TOOL_CALL_NAMES, AuditPort


class ToolCallAuditor:
    def __init__(self, audit: AuditPort, mac: ArgsMac):
        self._audit = audit
        self._mac = mac

    def record(
        self,
        *,
        task_id: str,
        actor_sub: str,
        tool_name: object,
        tool_call_id: object,
        arguments: object,
        status: str,
        result_count: int = 0,
    ) -> int:
        name = (
            tool_name
            if isinstance(tool_name, str) and tool_name in TOOL_CALL_NAMES - {"unknown"}
            else "unknown"
        )
        call_id = tool_call_id if isinstance(tool_call_id, str) else ""
        return self._audit.append(
            "tool.call",
            task_id=task_id,
            actor_sub=actor_sub,
            tool_name=name,
            status=status,
            result_count=result_count,
            tool_call_sha256=hashlib.sha256(call_id.encode("utf-8")).hexdigest(),
            args_hmac=self._mac.digest(str(tool_name), arguments),
            args_hmac_kid=self._mac.kid,
        )
