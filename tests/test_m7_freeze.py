"""공식 test 동결 경로의 digest와 내용 비노출 계약."""

import hashlib
import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "freeze_digest", REPO_ROOT / "scripts/freeze_digest.py"
)
assert _spec is not None and _spec.loader is not None
freeze = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(freeze)

EXPECTED: dict[str, str] = {
    "src/govops/agent": "f801700264f23227445cc0dd1d248da52a2af9708b004ba2341e9bc598c9d58e",
    "src/govops/tools": "1b3c8ab089b6ba49b5228bfe53543c84c2f657b1a598dc602506ea26976aa47c",
    "src/govops/guardrails": "76169bc4d9c92bd1443dd5424c9e7259bb1948aba00514261dbac550bcdf1b00",
    "src/govops/gateway": "9765bf09b778169ed8e4f78e2dddc5bc82613a17f78ebe525ee0b9a8afe70679",
    "src/govops/broker": "f158c23fb037ee830830819a4c23b1a7600b659f07b35eca74120dda96077008",
    "src/govops/eval": "589f05a290d9c3c311f03321257df92b3b6cd04df02d174c306ebbc3ee7801b0",
    "src/govops/evalrun": "caf142ef8056401be1ddcbc14d953b1ee89a9b010705501db4e01b84e7cc5507",
    "src/govops/replay": "0659a59c9164436a4a4a0882003717ed8f5426d03b6cef0d372a4e405a837d41",
    "src/govops/mocks": "f89061671393d1141e91ceb5735499f367401de842270e40a228a50f66982acc",
    "data/contract": "2a93b0688290247f135ed4bfe33a75bf6212c8e08741af1340076479375d6380",
    "data/policy": "d4016859fc0f671f62871128d15e25f2364c7025a73f0e348c3c8e9eb99cf8a5",
    "data/replay": "58f558abb6e07eab16426ec7e7e84ec9bf1704257ebb0e57794233f7125fd173",
    "data/seed": "549d356b8a7b52f3b2e2fe5fbec45e03591dee3ed6a0f5a0d3708c922700cc3d",
    "data/eval": "1efdcc559daa1ea84452dacb408bfb99c9e0627454517a3978201b6ca76ec4c5",
    "data/redteam": "835705710c2344c49a8d15cf8180a3044882bf1399a854b766af9d1b55c02553",
    "docs/results/m6-redteam-run": "b9ec95c306c43bab174bfa0aeb5e3e07a579551e2d485dbd9f5c51e1bef990f9",
    "src/govops/costs.py": "4460adf2d3745ee0368450f1cf8c70f4274fe082dc7772dde89711de8ea80a98",
    "src/govops/golden.py": "972d343c894cccbe4bf647dcc1abc8b980a805e42b7361aa096884388642c0df",
    "infra/litellm/config.yaml": "3e3c3837e9541d4cf97279d7d1daeb48b5179f316d097a7931a376267722935e",
    "src/govops/audit/__init__.py": "69e5aad3174fbdc1c26681b458d724d627c8f137fadbcd7bea1b3858901aeee3",
    "src/govops/audit/log.py": "19765856f3f96aa13f6fb7abe071452b5566e8c07c59563329f17976a28949b2",
    "src/govops/audit/crypto.py": "7d6d12e3e90296898b4334a6b28dd854a9bc34a5b481e3874bc501899a5eb45d",
    "src/govops/audit/anchor.py": "6219855f996ae057e545f258fe371f567ca757bfd68639277b3c7fb34216b725",
    "src/govops/audit/verify.py": "98d1fea8ebda1aa1b6461b3995595160c1355a2fb82b973d854824e3f1a32c9f",
    "src/govops/audit/smoke.py": "9597da4e364b52825d0d02d558bcb4fd22e18935beeed28c527e302f69c0d007",
    "docs/results/hypotheses.md": "b2484cf51f827e2aee789792f619b92cd731d57bd340dabbf0d6394f594a9392",
    "docs/results/hypotheses-m3.md": "69518e70eb9a1194e137faab9515ef4a3079058c55875a08d0c1dc4bb29e382a",
    "docs/results/hypotheses-m3-faults.md": "29515506dda4d3f82708506766f59a5b4ac4f9e2a7efab0a81e39ab09232d96b",
    "docs/results/hypotheses-m3-grading.md": "573bbccaf8956e3579b845e6c0c5f7eefe3f1e467f36bf7aa75e2fced16f3bf2",
    "docs/results/hypotheses-m3-prompt.md": "633587e96126c95ac714de8a604f4f94fc8aa117e88aa83f14e1da85a86dc3ba",
    "docs/results/hypotheses-m4-guardrail.md": "0be9b8a2dea604ffc4fbc582cf4f1ed7cc026c129012d854ff36cb32ac8ea614",
    "docs/results/hypotheses-m4-redteam.md": "53e44a04b6e0f9a59ba437a9f6d9f9764b02d3e6343f1d3365eb430051447fe1",
    "docs/results/hypotheses-m5-budget.md": "54d41ea8670131fef75a27c2e93a89302e8edaa23e9b9ab06a24c7b4a82f248c",
    "docs/results/hypotheses-m5-prompt.md": "564318030d2f716aeaf9565dcbe7b00e205285a6d888365106270c2dee3af958",
    "docs/results/hypotheses-m6-api-authz.md": "1e1ad1dbe2a77b52036ef329d539e10fe6e6aefc4a732af6269d718a83fdb5c0",
    "docs/results/hypotheses-m6-finalize.md": "b706bdd2b1526813a694d93e6e91190a58592cd271ccb0e25107eb02870ef109",
    "docs/results/hypotheses-m6-grading-r2.md": "cc6e147a5feb5d9b1b69ee091cf88920bd04be508f61aa68dd3df25eaa1167f5",
    "docs/results/hypotheses-m6-guardrail-v2.md": "d626b100c47481c2a7a86885769eede1fbd5943d234c745a5a323f7b1550ea90",
    "docs/results/hypotheses-m6-guardrail-v2-r2.md": "d97617788d4a40343ad23f3de4c12846e7ea88c7f5a4ba8c797da9fe2a89d272",
    "docs/results/hypotheses-m6-redteam-addendum.md": "fd16fc00a8597f13e70c1b6c46067d55b6359dc4f0bd45beb11a93c998e62213",
    "docs/results/hypotheses-m6-redteam-run.md": "58134a63f0fb1a966be100ec827ae8e303664ce917b454bdb3a9857b76a5f212",
    "docs/results/hypotheses-test-targets.md": "76ef4a0aadbfd96a83783c1443da8838e92eb6d8692d29d031242f7ca263259a",
}


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, timeout=30
    )


@pytest.fixture
def tracked_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init")
    (tmp_path / "frozen").mkdir()
    (tmp_path / "frozen/b.txt").write_bytes(b"second\n")
    (tmp_path / "frozen/a.txt").write_bytes(b"first\n")
    _git(tmp_path, "add", "--", "frozen")
    return tmp_path


def test_expected_covers_exactly_the_freeze_paths() -> None:
    assert set(EXPECTED) == set(freeze.FREEZE_PATHS)


def test_digests_match_expected() -> None:
    actual = freeze.digests(REPO_ROOT)
    changed = sorted(
        path
        for path in actual.keys() | EXPECTED.keys()
        if actual.get(path) != EXPECTED.get(path)
    )
    if changed:
        # pytest의 dict 비교는 실제·기대 digest를 출력하므로 경로만 보고한다.
        pytest.fail("\n".join(changed), pytrace=False)


def test_digest_uses_sorted_paths_and_sha256(tracked_repo: Path) -> None:
    rows = "".join(
        f"{path}\0{hashlib.sha256(content).hexdigest()}\n"
        for path, content in (
            ("frozen/a.txt", b"first\n"),
            ("frozen/b.txt", b"second\n"),
        )
    )
    assert (
        freeze.digest_for("frozen", tracked_repo)
        == hashlib.sha256(rows.encode("utf-8")).hexdigest()
    )


def test_digest_changes_with_file_content(tracked_repo: Path) -> None:
    before = freeze.digest_for("frozen", tracked_repo)
    (tracked_repo / "frozen/a.txt").write_bytes(b"changed\n")
    assert freeze.digest_for("frozen", tracked_repo) != before


def test_digest_changes_with_file_name(tracked_repo: Path) -> None:
    before = freeze.digest_for("frozen", tracked_repo)
    _git(tracked_repo, "mv", "--", "frozen/a.txt", "frozen/renamed.txt")
    assert freeze.digest_for("frozen", tracked_repo) != before


def test_digest_ignores_untracked_files(tracked_repo: Path) -> None:
    before = freeze.digest_for("frozen", tracked_repo)
    untracked = tracked_repo / "frozen/untracked.txt"
    untracked.write_bytes(b"untracked\n")
    assert freeze.digest_for("frozen", tracked_repo) == before
    untracked.write_bytes(b"changed untracked\n")
    assert freeze.digest_for("frozen", tracked_repo) == before


def test_digest_rejects_empty_tracked_set(tracked_repo: Path) -> None:
    (tracked_repo / "untracked.txt").write_bytes(b"untracked\n")
    for path in ("missing", "untracked.txt"):
        with pytest.raises(ValueError):
            freeze.digest_for(path, tracked_repo)


def test_output_has_only_path_and_digest(capsys: pytest.CaptureFixture[str]) -> None:
    freeze.main()
    output = capsys.readouterr()
    lines = output.out.splitlines()
    assert lines
    assert len(lines) == len(freeze.FREEZE_PATHS)
    assert all(re.fullmatch(r"[^ ]+ [0-9a-f]{64}", line) for line in lines)
    assert [line.split(" ")[0] for line in lines] == list(freeze.FREEZE_PATHS)
    assert output.err == ""


def test_excluded_files_are_not_in_the_freeze_paths() -> None:
    assert freeze.EXCLUDED == frozenset(
        {
            "docs/results/audit-anchors.jsonl",
            "data/manifest/costs.jsonl",
            "data/manifest/costs_grading.jsonl",
        }
    )
    assert not {
        excluded
        for excluded in freeze.EXCLUDED
        for path in freeze.FREEZE_PATHS
        if Path(excluded).is_relative_to(path)
    }


@pytest.mark.parametrize(
    "path",
    [
        "docs/results/audit-anchors.jsonl",
        "data/manifest/costs.jsonl",
        "data/manifest/costs_grading.jsonl",
        "docs/results",
        "data/manifest",
    ],
)
def test_digests_rejects_excluded_coverage(
    path: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(freeze, "FREEZE_PATHS", (path,))
    with pytest.raises(AssertionError):
        freeze.digests(tmp_path)
