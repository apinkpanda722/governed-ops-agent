"""Best-effort runner anchors through the task service boundary."""

import json
import re
from pathlib import Path


def anchor_label(prefix: str, run_name: str) -> str:
    label = re.sub(r"[^a-z0-9-]", "-", f"{prefix}-{run_name}".lower()).strip("-")[:40]
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", label):
        raise ValueError("invalid_anchor_label")
    return label


class AnchorTracker:
    def __init__(self):
        self._services: dict[Path, object] = {}

    def track(self, service) -> None:
        try:
            directory = getattr(getattr(service, "audit", None), "directory", None)
            if directory is not None:
                self._services[Path(directory)] = service
        except Exception:  # noqa: BLE001 — optional sinks must never affect a run
            return

    def finish(self, label: str, *, repo_root: Path) -> list[str]:
        lines = []
        for service in self._services.values():
            try:
                anchor = getattr(service, "anchor", None)
                if not callable(anchor):
                    continue
                row = anchor(label, repo_root=repo_root)
                lines.append(
                    "audit_anchor "
                    + json.dumps(
                        row,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                )
            except Exception as error:  # noqa: BLE001 — fail-open, class names only
                if isinstance(error, RuntimeError) and error.args == (
                    "audit_anchor_unsupported",
                ):
                    continue
                lines.append(f"audit_anchor_failed:{type(error).__name__}")
        return lines
