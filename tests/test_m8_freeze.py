"""M8 사전 등록 파일을 커밋 전에도 고정한다. M7 동결 경계는 바꾸지 않는다."""

import hashlib
import importlib.util
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

# 새 등록은 이 dict에 파일 키 하나를 추가한다. 기존 등록의 값은 갱신하지 않는다.
EXPECTED: dict[str, str] = {
    "docs/results/hypotheses-m8-regression.md": "e882edc002469a72f37853a0219a9b3ea76cf4809eb03d885064ada2b45bdd6a",
    "docs/results/hypotheses-m8-regression-addendum-1.md": "316dcbac36ad4fad8882836ddfc4cba9a65df96e0ac458ad5dca35e0050b9aac",
    "docs/results/hypotheses-m8-toolcall.md": "897c1278afbd034e950dbfb3084b810abfe374de9f69d44b94821f1305b47fe5",
    "docs/results/hypotheses-m8-grader.md": "8dfe8901a257bbabbe58307798e5169ddb3f789dc1c62eeb0efbad932117c3c0",
    "docs/results/hypotheses-m8-grader-addendum-1.md": "43c86115c50b5cf709f51d4727b5e49624cf30097d4802e64f0c17787698405e",
    "docs/results/hypotheses-m8-grader-addendum-2.md": "db0a5e7e95355e8b6e220a9939a49d4f6549a36910ef94842724328605dc3ded",
    "docs/results/hypotheses-m8-grader-addendum-3.md": "6b4f7b26cac5e8296d317830dbc557ddf86f049fd6a3e670850e3a72366d748c",
    "docs/results/hypotheses-m8-grader-addendum-4.md": "4da069617196e3e2bb239f3d17a507183a87d88ecee4c093c5d59beb0d6eb1cb",
    "docs/results/hypotheses-m8-citation.md": "1be45d174f1f805a523d61e65c768215173c03a850bda81ebbab231fca03d393",
    "docs/results/hypotheses-m8-hallucination.md": "a8c5ce91cdcc08e1d07ffc3d57b99acd21554e61c0786aed100fb7034ebd28ad",
}


def single_file_digest(path: str) -> str:
    """freeze.digest_for의 파일 하나짜리 행 형식. git 추적 여부와 무관하다."""
    content_sha256 = hashlib.sha256((REPO_ROOT / path).read_bytes()).hexdigest()
    row = f"{path}\0{content_sha256}\n"
    return hashlib.sha256(row.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("path", EXPECTED)
def test_registration_is_repo_file_not_ignored_or_m7_frozen(path: str) -> None:
    relative = Path(path)
    assert not relative.is_absolute()
    target = (REPO_ROOT / relative).resolve()
    assert target.is_relative_to(REPO_ROOT)
    assert target.is_file(), path
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "--", path],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert ignored.returncode == 1, path
    assert not any(
        relative.is_relative_to(frozen) or Path(frozen).is_relative_to(relative)
        for frozen in freeze.FREEZE_PATHS
    ), path


@pytest.mark.parametrize("path", EXPECTED)
def test_registration_digest_is_frozen(path: str) -> None:
    if single_file_digest(path) != EXPECTED[path]:
        pytest.fail(path, pytrace=False)


def test_single_file_digest_matches_tracked_freeze_digest() -> None:
    path = "docs/results/hypotheses-m8-grader.md"
    assert single_file_digest(path) == freeze.digest_for(path, REPO_ROOT)
