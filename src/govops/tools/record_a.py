"""User-run CLI recording whole synthetic A responses after contract validation."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from govops.broker.credentials import CredentialError, UnknownUserError, known_users
from govops.replay.recordings import (
    Recording,
    RecordingSet,
    arguments_key,
    validate_recording,
    write_recordings,
)
from govops.replay.synthetic import SkipStats, screen_recording
from govops.tools.a_mcp import AMcpClient, AMcpError, check_contract
from govops.tools.contract import ContractMismatchError, snapshot_sha256


async def _record(requests_file: Path, out: Path, url: str) -> None:
    payload = json.loads(requests_file.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or set(payload) != {"requests"}
        or not isinstance(payload["requests"], list)
    ):
        raise ValueError("invalid requests")
    users = known_users("kodocs-mcp")
    for request in payload["requests"]:
        if not isinstance(request, dict) or set(request) != {
            "caller",
            "tool",
            "arguments",
        }:
            raise ValueError("invalid request")
        if request["caller"] not in users:
            raise UnknownUserError("kodocs-mcp: user not found")
        # Validate fields and credential exclusion before any network request.
        validate_recording(Recording(**request, result={}, is_error=False))
    client = AMcpClient(url)
    await check_contract(client)
    merged = {
        (r.caller, r.tool, arguments_key(r.arguments)): r
        for r in RecordingSet.load(out).entries()
    }
    skipped = []
    stats = SkipStats()
    for request in payload["requests"]:
        result = await client.call_tool(
            request["caller"], request["tool"], request["arguments"]
        )
        entry = Recording(**request, result=result.payload, is_error=result.is_error)
        outcome = screen_recording(entry)
        stats.total += 1
        if not outcome.recorded:
            stats.skipped += 1
            skipped.append(
                (entry.caller, entry.tool, arguments_key(entry.arguments)[:12])
            )
            continue
        stats.recorded += 1
        merged[(entry.caller, entry.tool, arguments_key(entry.arguments))] = entry
    write_recordings(out, merged.values())
    print(
        f"저장 {stats.recorded} (전체 {len(payload['requests'])}, 건너뜀 {len(skipped)})"
    )
    print(f"건너뜀 목록: {json.dumps(skipped, ensure_ascii=False)}")
    print(f"A 계약 스냅샷 해시: {snapshot_sha256()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--url", default=os.environ.get("KODOCS_MCP_URL"))
    args = parser.parse_args(argv)
    if not args.url:
        print("기록 실패: KODOCS_MCP_URL 또는 --url이 필요합니다", file=sys.stderr)
        return 1
    try:
        asyncio.run(_record(args.requests, args.out, args.url))
    except (
        AMcpError,
        CredentialError,
        ContractMismatchError,
        ValueError,
        OSError,
        TypeError,
    ):
        print(
            "기록 실패: 요청·계약·자격 증명 또는 A 연결을 확인하세요", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
