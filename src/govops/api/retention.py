"""Audit retention intent, clean turn indexes, and expose a user-run CLI."""

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime

from govops.agent.checkpointer import open_checkpointer
from govops.agent.retention import PruneResult, prune_checkpoints
from govops.api.conversations import PostgresTurnIndex, TurnIndex
from govops.audit.log import AuditLog, AuditPort
from govops.env import env_or


async def run_prune(
    saver,
    turns: TurnIndex,
    audit: AuditPort | None,
    *,
    now: datetime,
    apply: bool,
) -> PruneResult:
    """Record intent before deleting; remove only successfully deleted indexes."""
    deleted_conversation_ids = await turns.deleted_conversation_ids()
    result = await prune_checkpoints(
        saver, now=now, apply=False, deleted_conversation_ids=deleted_conversation_ids
    )
    if not apply or not result.candidates or audit is None:
        return result
    # Append errors propagate before the only deletion branch (fail-closed).
    audit.append(
        "checkpoint.pruned",
        task_id=None,
        actor_sub="system",
        pruned_count=len(result.candidates),
        cutoff=result.cutoff,
    )
    result = await prune_checkpoints(
        saver, now=now, apply=True, deleted_conversation_ids=deleted_conversation_ids
    )
    if result.deleted_ids:
        await turns.delete_conversations(result.deleted_ids)
    return result


async def startup_prune(service, saver, turns) -> None:
    """Best-effort retention after startup cleanup, without exposing errors."""
    try:
        await run_prune(
            saver, turns, audit=service.audit, now=datetime.now(UTC), apply=True
        )
    except Exception:  # noqa: BLE001 — maintenance must not prevent serving
        print("WARN checkpoint_prune_failed", file=sys.stderr)


async def _run_cli(url: str, *, apply: bool) -> PruneResult:
    async with open_checkpointer(url) as saver:
        turns = PostgresTurnIndex()
        audit = AuditLog(env_or("AUDIT_DIR", "data/interim/audit")) if apply else None
        return await run_prune(saver, turns, audit, now=datetime.now(UTC), apply=apply)


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("invalid_arguments")


def main(argv: list[str] | None = None) -> int:
    """Print counts only; transport, key, and argument failures stay opaque."""
    parser = _Parser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    try:
        args = parser.parse_args(argv)
        url = os.environ.get("GOVOPS_DATABASE_URL", "").strip()
        if not url:
            print("GOVOPS_DATABASE_URL required", file=sys.stderr)
            return 1
        result = asyncio.run(_run_cli(url, apply=args.apply))
    except Exception:  # noqa: BLE001 — never print credentials or backend details
        print("ERROR checkpoint_prune_failed", file=sys.stderr)
        return 1
    print(
        f"cutoff={result.cutoff} scanned={result.scanned} "
        f"candidates={len(result.candidates)} active={result.active} "
        f"deleted={result.deleted} apply={str(args.apply).lower()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
