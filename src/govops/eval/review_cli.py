"""User-run cards and read-only review status commands."""

import argparse
import json
from pathlib import Path

from govops.eval.a_source import ASource, ASourceError
from govops.eval.cards import build_cards
from govops.eval.meta import load_meta
from govops.eval.review import REVIEW_PATH, review_report
from govops.eval.spec import load_specs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["cards", "status"])
    parser.add_argument(
        "--a-root", type=Path, default=None, help="A 저장소(읽기 전용, cards에 필요)"
    )
    args = parser.parse_args(argv)
    paths = [
        Path(p)
        for p in [
            "data/eval/tasks.json",
            "data/eval/meta.json",
            "data/seed/users.json",
            "data/seed/tickets.json",
        ]
    ]
    if not all(p.is_file() for p in paths):
        print("입력 파일 없음: 먼저 make eval-meta-extract·make eval-gen을 실행하세요.")
        return 1
    try:
        meta = load_meta(paths[1])
        users = json.loads(paths[2].read_text(encoding="utf-8"))["users"]
        tickets = json.loads(paths[3].read_text(encoding="utf-8"))["tickets"]
        specs = load_specs(paths[0], meta, users, tickets)
        if args.command == "cards":
            if args.a_root is None or not args.a_root.is_dir():
                print("A 저장소 경로 필요: make eval-cards A_ROOT=<A 저장소 경로>")
                return 1
            try:
                source = ASource(args.a_root, {d.doc_id for d in meta.docs})
            except ASourceError as error:
                print(str(error))
                return 1
            for path in build_cards(specs, meta, users, tickets, source):
                print(path)
            return 0
        report = review_report(specs, REVIEW_PATH)
        for key, label in [
            ("confirmed", "확정"),
            ("rejected", "반려"),
            ("needs_change", "수정 필요"),
            ("pending", "미검수"),
            ("stale", "무효"),
        ]:
            ids = getattr(report, key)
            print(f"{label}: {len(ids)} — {', '.join(ids)}")
        return 0 if len(specs) == len(report.confirmed) == 60 else 1
    except (ValueError, OSError, KeyError, TypeError):
        print("입력 또는 확정본 형식 오류: 검수 상태를 확인할 수 없습니다.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
