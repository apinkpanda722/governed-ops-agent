"""User-run, network-free audit checks; all synthetic writes stay in tempfile."""

import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from govops.audit.anchor import AuditAnchorError
from govops.audit.crypto import (
    AuditKeyError,
    canonical_json,
    kid_of,
    load_signing_key,
    record_hash,
)
from govops.audit.log import AuditLog, AuditWriteError
from govops.audit.verify import LEGACY, ROOT, read_anchor_lines, verify_chain
from govops.env import env_or


def _emit(log):
    log.append(
        "service.start",
        task_id=None,
        actor_sub="system",
        policy_sha256="1" * 64,
        rules_sha256="2" * 64,
        access_index_sha256="3" * 64,
        hook_rules_digest="4" * 12,
        hook_code_digest="5" * 12,
    )
    log.append(
        "model.call",
        task_id="1" * 32,
        actor_sub="demo-platform-g3",
        team="platform",
        model="agent-primary",
        deployment_id="synthetic",
        fallback=False,
        cost_usd="0.000000",
    )
    log.append(
        "side_effect.draft",
        task_id="1" * 32,
        actor_sub="demo-platform-g3",
        tool="slack_send_message",
        draft_hash="6" * 64,
    )


def _write(path, rows):
    path.write_text(
        "".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8"
    )


def _temporary_checks(root, keys, check):
    directory = root / "basic"
    log = AuditLog(directory)
    _emit(log)
    report = verify_chain(directory, public_keys=keys)
    check("chain_basic", report.ok and report.records == 4)
    print(f"INFO records={report.records} kid={report.kid}")
    pending = root / "pending.jsonl"
    fixed = log.anchor("smoke", repo_root=root, pending_file=pending)
    source = next(directory.glob("chain-*.jsonl"))
    original = [json.loads(line) for line in source.read_text().splitlines()]
    for kind, expected in [
        ("modify", {"hash_mismatch"}),
        ("delete_middle", {"seq_gap"}),
        ("reorder", {"seq_order", "prev_hash_mismatch"}),
        ("tail_truncate", {"tail_truncated"}),
        ("other_key", {"kid_unknown"}),
    ]:
        target = root / kind
        shutil.copytree(directory, target)
        path = target / source.name
        rows = [dict(row) for row in original]
        if kind == "modify":
            rows[1]["policy_sha256"] = "7" * 64
        elif kind == "delete_middle":
            del rows[2]
        elif kind == "reorder":
            rows[1], rows[2] = rows[2], rows[1]
        elif kind == "tail_truncate":
            rows = rows[:-1]
        else:
            # Recompute and sign a valid replacement chain with another key.
            other = Ed25519PrivateKey.generate()
            previous = "0" * 64
            for row in rows:
                row["kid"] = kid_of(other.public_key())
                row["prev_hash"] = previous
                row["hash"] = record_hash(
                    {k: v for k, v in row.items() if k not in {"hash", "sig"}}
                )
                row["sig"] = other.sign(row["hash"].encode("ascii")).hex()
                previous = row["hash"]
        _write(path, rows)
        anchors = [{**fixed, "dir": kind}] if kind == "tail_truncate" else []
        report = verify_chain(
            target,
            public_keys=keys,
            anchors=anchors,
            repo_root=root,
        )
        check(
            f"tamper_{kind}",
            bool(expected & {p.code for p in report.problems}),
            "not_detected",
        )

    scene = AuditLog(root / "scene5")
    _emit(scene)
    anchor = scene.anchor(
        "scene-five", repo_root=root, pending_file=root / "scene-pending"
    )
    _emit(scene)
    path = next(scene.directory.glob("chain-*.jsonl"))
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    anchors = read_anchor_lines([root / "scene-pending"])
    _write(path, [r for r in rows if r["seq"] <= anchor["seq"]])
    after = verify_chain(
        scene.directory, public_keys=keys, anchors=anchors, repo_root=root
    )
    _write(path, [r for r in rows if r["seq"] < anchor["seq"]])
    cut = verify_chain(
        scene.directory, public_keys=keys, anchors=anchors, repo_root=root
    )
    check(
        "scene5_tail_cut",
        after.ok
        and after.anchors_checked == 1
        and cut.anchors_checked == 1
        and "tail_truncated" in {p.code for p in cut.problems},
        "not_detected",
    )
    print("INFO known_limit=tail_after_last_anchor_not_detected")


def _real_check(keys, check):
    directory = Path(env_or("AUDIT_DIR", "data/interim/audit"))
    try:
        if any(directory.glob("chain-*.jsonl")):
            anchors = read_anchor_lines(
                [
                    Path(
                        env_or(
                            "AUDIT_ANCHORS_PENDING_FILE",
                            "data/interim/audit/anchors-pending.jsonl",
                        )
                    ),
                    ROOT / "docs/results/audit-anchors.jsonl",
                ]
            )
            report = verify_chain(
                directory, public_keys=keys, anchors=anchors, repo_root=ROOT
            )
            check(
                "real_chain",
                report.ok,
                report.problems[0].code if report.problems else "verification_failed",
            )
            print(f"INFO records={report.records} anchors={report.anchors_checked}")
        else:
            print("INFO real_chain=none (first write creates genesis)")
        files = list(directory.glob(LEGACY))
        lines = 0
        for path in files:
            # Count lines without decoding or displaying legacy record content.
            with path.open("rb") as stream:
                lines += sum(1 for _ in stream)
        print(
            f"INFO legacy_unverified files={len(files)} lines={lines} (체인 없음, 미검증)"
        )
    except ValueError:
        check("real_chain", False, "anchor_file_invalid")
    except OSError:
        check("real_chain", False, "audit_read_failed")


def main() -> int:
    failed = False

    def check(name, ok, code="verification_failed"):
        nonlocal failed
        failed |= not ok
        print(f"PASS {name}" if ok else f"FAIL {name} {code}")

    try:
        key = load_signing_key()
    except AuditKeyError as error:
        print(f"FAIL key {error.code}\nRESULT FAIL")
        return 1
    print(f"PASS key\nINFO kid={key.kid}\nINFO key_mode=0600\nINFO key_pair=match")
    keys = {key.kid: key.public}
    try:
        with TemporaryDirectory(prefix="govops-audit-smoke-", dir="/tmp") as temporary:
            _temporary_checks(Path(temporary), keys, check)
    except (AuditKeyError, AuditWriteError, AuditAnchorError) as error:
        check("chain_basic", False, error.code)
    except (OSError, ValueError):
        check("chain_basic", False, "smoke_io_failed")
    _real_check(keys, check)
    print("RESULT FAIL" if failed else "RESULT PASS")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
