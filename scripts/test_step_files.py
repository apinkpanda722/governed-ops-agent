"""step 파일의 공통 금지사항 블록은 /harness 양식의 블록을 수정 없이 복사해야 한다.

`## 공통 금지사항` 제목이 있는 step 파일만 검사한다(이전 phase는 소급하지 않는다).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = (ROOT / ".claude/commands/harness.md").read_text()
MATCH = re.search(
    r"<!-- common-block:start -->\n(.*?)\n<!-- common-block:end -->", TEMPLATE, re.DOTALL
)


def canonical_block() -> str:
    assert MATCH, "harness.md에 common-block 표식이 없다"
    return MATCH.group(1).strip()


def step_files_with_common_block():
    return [
        p
        for p in sorted((ROOT / "phases").glob("*/step*.md"))
        if "## 공통 금지사항" in p.read_text()
    ]


def test_template_block_starts_with_pre_check():
    first = canonical_block().splitlines()[0]
    assert first.startswith("- **선점검**")
    assert "blocked" in first and "두 문장" in first


def test_template_block_covers_four_rules():
    block = canonical_block()
    for marker in ("**실제 서비스**", "**계층 경계**", "**읽기 전용**", "**비밀 값**"):
        assert marker in block


def test_step_files_copy_the_common_block_verbatim():
    for path in step_files_with_common_block():
        text = path.read_text()
        assert canonical_block() in text, f"{path}: 공통 금지사항 블록이 수정됐다"
        # 제목 바로 아래에 블록이 있고, 선점검이 그 맨 앞 항목이다.
        section = text.split("## 공통 금지사항", 1)[1].lstrip()
        assert section.startswith("<!-- common-block:start -->"), path
        assert canonical_block().splitlines()[0] in section.splitlines()[1], path
