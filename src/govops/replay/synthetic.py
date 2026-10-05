"""Shared whole-response screening for synthetic A recordings."""

from dataclasses import dataclass

from govops.replay.recordings import Recording, RecordingRejected, validate_recording


@dataclass(frozen=True)
class RecordOutcome:
    recorded: bool
    reason: str | None = None


@dataclass
class SkipStats:
    total: int = 0
    recorded: int = 0
    skipped: int = 0

    def ratio(self) -> float:
        return self.skipped / self.total if self.total else 0.0


def screen_recording(entry: Recording) -> RecordOutcome:
    try:
        validate_recording(entry)
    except RecordingRejected as error:
        if str(error).startswith("non-synthetic doc_id:"):
            return RecordOutcome(False, "non_synthetic_doc")
        raise

    def non_synthetic(value) -> bool:
        if isinstance(value, dict):
            return any(
                (
                    key == "doc_id"
                    and (not isinstance(child, str) or not child.startswith("syn-"))
                )
                or non_synthetic(child)
                for key, child in value.items()
            )
        if isinstance(value, list):
            return any(non_synthetic(child) for child in value)
        return False

    if non_synthetic(entry.result):
        return RecordOutcome(False, "non_synthetic_doc")
    return RecordOutcome(True)
