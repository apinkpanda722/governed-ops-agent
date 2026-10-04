"""Private local JSONL sink. Records arrive already masked by obs.trace."""

import json
import os
import re
import threading
from dataclasses import asdict
from pathlib import Path

from govops.obs.trace import SpanRecord

_WRITE_LOCK = threading.Lock()


class JsonlSink:
    def __init__(self, root: Path | None = None) -> None:
        default = Path(__file__).resolve().parents[3] / "logs" / "traces"
        self.root = (
            Path(root)
            if root is not None
            else Path(os.environ.get("GOVOPS_TRACE_DIR", str(default)))
        )

    def emit(self, record: SpanRecord) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", record.trace_id):
            raise ValueError("invalid trace_id")
        payload = (
            json.dumps(asdict(record), sort_keys=True, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        with _WRITE_LOCK:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            descriptor = os.open(
                self.root / f"{record.trace_id}.jsonl",
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                0o600,
            )
            try:
                os.fchmod(descriptor, 0o600)
                if os.write(descriptor, payload) != len(payload):
                    raise OSError("incomplete trace write")
            finally:
                os.close(descriptor)

    def close_trace(self, trace_id: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{32}", trace_id):
            raise ValueError("invalid trace_id")
