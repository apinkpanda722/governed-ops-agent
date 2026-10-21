"""User-run rollout controls; print only registered codes and counts."""

import argparse
import re
from collections import Counter
from datetime import UTC, datetime

from govops.rollout import store
from govops.rollout.policy import RolloutState


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("m8_rollout_invalid_arguments")


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(add_help=False, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", add_help=False, allow_abbrev=False)
    set_parser = commands.add_parser("set", add_help=False, allow_abbrev=False)
    set_parser.add_argument("--pct-bp", type=int, required=True)
    kill_parser = commands.add_parser("kill", add_help=False, allow_abbrev=False)
    kill_parser.add_argument("--reason", default="manual_kill")
    try:
        args = parser.parse_args(argv)
        if args.command == "set" and not 0 <= args.pct_bp <= 10000:
            raise ValueError("m8_rollout_invalid_arguments")
        if args.command == "kill" and not re.fullmatch(
            r"[a-z][a-z_]{0,39}", args.reason
        ):
            raise ValueError("m8_rollout_invalid_arguments")
    except ValueError:
        print("FAIL m8_rollout_invalid_arguments")
        return 1

    directory = store.rollout_dir()
    if args.command == "status":
        state = store.read_state(directory)
        try:
            records = store.read_exposures(directory)
        except ValueError:
            print("FAIL m8_rollout_records_invalid")
            return 1
        counts = Counter((row["event"], row.get("variant")) for row in records)
        print(
            f"pct_bp={state.pct_bp} kill={str(state.kill).lower()} "
            f"reason={state.reason or 'none'} "
            f"assigned_v1={counts['assigned', 'v1']} "
            f"assigned_v2={counts['assigned', 'v2']} "
            f"variant_missing={counts['variant_missing', None]} "
            f"rollbacks={counts['rollback', None]}"
        )
        return 0

    state = (
        RolloutState(args.pct_bp, False, None, None)
        if args.command == "set"
        else RolloutState(0, True, datetime.now(UTC).isoformat(), args.reason)
    )
    try:
        store.write_state(directory, state)
    except OSError:
        print("FAIL m8_rollout_state_write_failed")
        return 1
    if args.command == "set":
        print(f"OK pct_bp={state.pct_bp}")
    else:
        print(f"OK kill reason={state.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
