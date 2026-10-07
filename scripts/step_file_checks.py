"""step 파일의 기계 검사: 열람 금지, 실행 금지 대상, 소비자 테스트 경고.

step 파일 형식 v2는 `## 작업` 절에 아래 두 줄을 가진다(경로는 백틱으로 감싸고 쉼표로 나눈다. 없으면 `(없음)`).

    열람 금지 경로: `src/govops/guardrails/rules_v1.json`, `data/eval/`
    수정 허용 경로: `src/govops/x.py`, `tests/test_x.py`

형식 v2는 `FORMAT_V2_FROM` phase부터 필수다(이전 phase는 소급하지 않는다).
검사 실패는 `*_violations`가 돌려주는 문장이고, 소비자 테스트 목록은 경고일 뿐 실패가 아니다.
외부 도구(rg) 없이 표준 라이브러리만 쓴다.
"""

import ast
import fnmatch
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORMAT_V2_FROM = "m4-guardrail"
FORBIDDEN_LINE = "열람 금지 경로:"
ALLOWED_LINE = "수정 허용 경로:"
NEGATION = re.compile(r"열람하지 않|열람 금지|열람은 금지|읽지 않")
EXEC_PROHIBITION = re.compile(r"실행(?:하거나|하지|해서는|하는 것)")
CATEGORY_PHRASE = re.compile(r"(?:make|live|smoke|dev)\s*타깃|`make`\s*타깃")
COMMAND_PREFIXES = ("make ", "python", "uv run", "pnpm", "docker", "bash ", "sh ")
PATH_SUFFIXES = (".py", ".md", ".json", ".jsonl", ".yaml", ".yml", ".sh", ".toml")
ROOT_FILES = {"Makefile", "AGENTS.md", ".env.example", "pyproject.toml"}
GENERIC_SYMBOLS = {"__all__"}
MAX_PER_PATH = 12


def section(text: str, title: str) -> str:
    """`## title`로 시작하는 절의 본문(다음 `## `까지)."""
    match = re.search(
        rf"^## {re.escape(title)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S
    )
    return match.group(1) if match else ""


def backticked(text: str) -> list[str]:
    return re.findall(r"`([^`\n]+)`", text)


def normalize(path: str) -> str:
    return path.strip().lstrip("/")


def looks_like_path(token: str) -> bool:
    token = token.strip()
    if not token or " " in token or token.startswith("-"):
        return False
    if token.startswith(("http", "GET", "POST")):
        return False
    if "/" in token:
        return True
    return token in ROOT_FILES or token.endswith(PATH_SUFFIXES)


def paths_in(text: str) -> list[str]:
    found = []
    for token in backticked(text):
        token = normalize(token)
        if "/" in token or token in ROOT_FILES:
            if looks_like_path(token) and token not in found:
                found.append(token)
    return found


NOT_MODIFIED = re.compile(
    r"읽기만|고치지|수정하지|않는다|그대로 둔다|그대로다|불변|읽기 전용"
)
NON_PATH_MARKERS = ("*", "::", "…", "<", ">")
TOP_DIRS = {"tests/", "docs/", "src/", "data/", "scripts/", "infra/", "web/", "phases/"}


def modify_targets(paragraph: str) -> list[str]:
    """`**수정할 파일**` 문단에서 실제로 고치는 경로만 뽑는다.

    괄호 안의 보조 경로, `계층:` 이후 설명, 뒤따르는 말(40자 안)이 "고치지 않는다·읽기만"인
    경로, 글롭·심볼 표기는 수정 대상이 아니다.
    """
    paragraph = paragraph.split("계층:", 1)[0]
    targets = []
    depth = 0
    index = 0
    while index < len(paragraph):
        char = paragraph[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "`":
            end = paragraph.find("`", index + 1)
            if end == -1:
                break
            token = paragraph[index + 1 : end]
            tail = re.split(r"(?<=[.。])\s|,\s*(?=`)", paragraph[end + 1 : end + 41])[0]
            path = normalize(token)
            if (
                depth == 0
                and not any(mark in token for mark in NON_PATH_MARKERS)
                and ("/" in path.rstrip("/") or path in ROOT_FILES or path in TOP_DIRS)
                and looks_like_path(path)
                and not NOT_MODIFIED.search(tail)
                and path not in targets
            ):
                targets.append(path)
            index = end
        index += 1
    return targets


def machine_lines(text: str) -> dict[str, list[str]] | None:
    """`열람 금지 경로`·`수정 허용 경로` 줄. 하나라도 없으면 None."""
    work = section(text, "작업")
    result = {}
    for key, label in (("forbidden", FORBIDDEN_LINE), ("allowed", ALLOWED_LINE)):
        match = re.search(rf"^{re.escape(label)}[ \t]*(.*)$", work, re.M)
        if not match:
            return None
        line = match.group(1)
        if "(없음)" in line and not backticked(line):
            result[key] = []
        else:
            result[key] = [normalize(token) for token in backticked(line)]
    return result


def is_inside(path: str, parent: str) -> bool:
    """path가 parent와 같거나 parent 디렉터리 안에 있다."""
    parent = normalize(parent)
    path = normalize(path)
    if "*" in parent:
        return fnmatch.fnmatch(path, parent)
    if parent.endswith("/"):
        return path.startswith(parent) or path == parent.rstrip("/")
    return path == parent or path.startswith(parent + "/")


def format_required(phase: str, root: Path = ROOT) -> bool:
    index = json.loads((root / "phases/index.json").read_text())
    order = [entry["dir"] for entry in index["phases"]]
    if FORMAT_V2_FROM not in order or phase not in order:
        return False
    return order.index(phase) >= order.index(FORMAT_V2_FROM)


def read_list_violations(text: str) -> list[str]:
    """`## 읽어야 할 파일`이 열람 금지 경로를 가리키면 실패 문장을 돌려준다."""
    lines = machine_lines(text)
    if lines is None or not lines["forbidden"]:
        return []
    forbidden = lines["forbidden"]
    problems = []
    for bullet in section(text, "읽어야 할 파일").splitlines():
        if not bullet.lstrip().startswith("-"):
            continue
        negated = bool(NEGATION.search(bullet))
        tokens = backticked(bullet)
        for token in tokens:
            entry = normalize(token)
            for ban in forbidden:
                ban_name = Path(ban.rstrip("/")).name
                if is_inside(entry, ban):
                    problems.append(
                        f"읽기 목록의 `{token}`이 열람 금지 `{ban}` 안에 있다"
                    )
                elif not negated and "/" in entry and is_inside(ban, entry):
                    problems.append(
                        f"읽기 목록의 상위 경로 `{token}`이 열람 금지 `{ban}`를 포함한다"
                        "(열람하지 않는다는 문구로 좁히지 않았다)"
                    )
                elif not negated and "/" not in entry and entry == ban_name:
                    problems.append(
                        f"읽기 목록이 열람 금지 `{ban}`의 이름 `{token}`을 가리킨다"
                    )
    return problems


def ac_commands(text: str) -> list[str]:
    block = section(text, "Acceptance Criteria")
    commands = []
    for fence in re.findall(r"```(?:bash|sh)?\n(.*?)```", block, re.S):
        for line in fence.splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                commands.append(" ".join(line.split()))
    for token in backticked(section(text, "검증 절차")):
        token = " ".join(token.split())
        if token.startswith(COMMAND_PREFIXES):
            commands.append(token)
    return commands


def prohibition_bullets(text: str) -> list[str]:
    body = (
        text.split("## 이 step의 금지사항", 1)[1]
        if "## 이 step의 금지사항" in text
        else ""
    )
    return [line for line in body.splitlines() if line.startswith("- ")]


def command_violations(text: str) -> list[str]:
    """실행 금지 문장은 명령(타깃 이름)을 명시해야 하고 AC·검증 명령과 겹치면 안 된다."""
    problems = []
    acs = ac_commands(text)
    for bullet in prohibition_bullets(text):
        if not (EXEC_PROHIBITION.search(bullet) and re.search(r"마라|금지", bullet)):
            continue
        head = bullet[:60]
        if CATEGORY_PHRASE.search(bullet):
            problems.append(
                f"범주 표현으로 실행을 금지했다(타깃 이름을 명시하라): {head}…"
            )
        commands = [
            " ".join(token.split())
            for token in backticked(bullet)
            if token.strip().startswith(COMMAND_PREFIXES)
        ]
        if not commands:
            problems.append(
                f"실행 금지 대상(명령·타깃 이름)이 명시되지 않았다: {head}…"
            )
        for command in commands:
            if "*" in command or "?" in command:
                problems.append(
                    f"글롭·범주 표현 `{command}`: 타깃 이름을 하나씩 명시하라"
                )
            for ac in acs:
                if ac == command or ac.startswith(command + " "):
                    problems.append(
                        f"AC·검증 명령 `{ac}`가 실행 금지 대상 `{command}`와 겹친다"
                    )
    return problems


def declared_path_violations(text: str) -> list[str]:
    """`**수정할 파일**` 문단의 경로가 `수정 허용 경로` 줄에 모두 있어야 한다."""
    lines = machine_lines(text)
    if lines is None:
        return []
    match = re.search(
        r"\*\*수정할 파일\*\*(.*?)(?:\n\n|\Z)", section(text, "작업"), re.S
    )
    if not match:
        return ["`**수정할 파일**` 문단이 없다"]
    problems = []
    for path in modify_targets(match.group(1)):
        if not any(is_inside(path, allowed) for allowed in lines["allowed"]):
            problems.append(f"수정할 파일 `{path}`가 `수정 허용 경로` 줄에 없다")
    return problems


def format_violations(text: str) -> list[str]:
    if machine_lines(text) is None:
        return [f"`{FORBIDDEN_LINE}`·`{ALLOWED_LINE}` 줄이 `## 작업`에 없다"]
    lines = machine_lines(text)
    problems = []
    for key in ("forbidden", "allowed"):
        for path in lines[key]:
            if ".." in path.split("/") or path.startswith("~"):
                problems.append(f"허용되지 않는 경로 표기 `{path}`")
    return problems


# ---- 소비자 테스트 경고(실패 아님) ----


def _test_files(root: Path) -> dict[str, str]:
    files = {}
    for pattern in ("tests/**/*.py", "scripts/test_*.py"):
        for path in sorted(root.glob(pattern)):
            if "__pycache__" in path.parts:
                continue
            files[str(path.relative_to(root))] = path.read_text(
                encoding="utf-8", errors="replace"
            )
    return files


def _module_name(path: str) -> str | None:
    if path.startswith("src/") and path.endswith(".py"):
        name = path[len("src/") : -len(".py")].replace("/", ".")
        return name[: -len(".__init__")] if name.endswith(".__init__") else name
    return None


def _symbols(source: str) -> list[str]:
    names = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return names
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return [
        n
        for n in names
        if not n.startswith("_") and len(n) >= 8 and n not in GENERIC_SYMBOLS
    ]


def _allowed(consumer: str, allowed: list[str]) -> bool:
    return any(is_inside(consumer, entry) for entry in allowed)


def consumer_warnings(text: str, root: Path = ROOT) -> list[str]:
    """수정 허용 경로의 소비자 테스트 중 허용 목록에 없는 것을 찾는다(경고용)."""
    lines = machine_lines(text)
    if lines is None:
        return []
    allowed = lines["allowed"]
    tests = _test_files(root)
    warnings = []
    for path in allowed:
        if path.startswith("tests/") or path.startswith("scripts/test_"):
            continue
        target = root / path
        found: dict[str, str] = {}
        module = _module_name(path)
        if module and target.exists():
            leaf = module.rsplit(".", 1)[-1]
            parent = module.rsplit(".", 1)[0] if "." in module else ""
            symbols = _symbols(target.read_text(encoding="utf-8"))
            for name, source in tests.items():
                if re.search(rf"\b{re.escape(module)}\b", source) or (
                    parent
                    and re.search(
                        rf"from\s+{re.escape(parent)}\s+import\s+[^\n]*\b{re.escape(leaf)}\b",
                        source,
                    )
                ):
                    found[name] = "모듈 import"
                else:
                    # 심볼 이름만 같은 우연한 일치를 줄이려고 모듈 이름도 언급하는 테스트만 센다.
                    stem = leaf if leaf != "__main__" else parent.rsplit(".", 1)[-1]
                    hit = next(
                        (
                            s
                            for s in symbols
                            if re.search(rf"\b{re.escape(s)}\b", source)
                        ),
                        None,
                    )
                    if hit and stem.lower() in source.lower():
                        found[name] = f"심볼 `{hit}`"
        elif not target.exists() and not path.endswith("/"):
            # 새 파일: 같은 디렉터리를 glob·개수로 훑는 테스트(고정 개수 단언 포함)를 찾는다.
            base = Path(path).name
            folder = Path(path).parent.name
            for name, source in tests.items():
                for literal in re.findall(r"\.r?glob\(\s*[\"']([^\"']+)[\"']", source):
                    if (
                        fnmatch.fnmatch(base, literal.split("/")[-1])
                        and folder in source
                    ):
                        found[name] = f"glob `{literal}`이 새 파일과 일치"
        elif target.exists():
            needle = Path(path).name if path in ROOT_FILES else path
            for name, source in tests.items():
                if needle in source:
                    found[name] = f"`{needle}` 참조"
        extra = [
            f"{path} ← {name} ({reason})"
            for name, reason in sorted(found.items())
            if not _allowed(name, allowed)
        ]
        warnings += extra[:MAX_PER_PATH]
        if len(extra) > MAX_PER_PATH:
            warnings.append(f"{path} ← … 외 {len(extra) - MAX_PER_PATH}건")
    return warnings


def main(argv: list[str]) -> int:
    for arg in argv:
        text = Path(arg).read_text()
        print(f"== {arg}")
        for title, problems in (
            ("형식", format_violations(text)),
            ("읽기 목록 ↔ 열람 금지", read_list_violations(text)),
            ("실행 금지 ↔ AC·검증", command_violations(text)),
            ("수정할 파일 ↔ 수정 허용 경로", declared_path_violations(text)),
        ):
            for problem in problems:
                print(f"FAIL [{title}] {problem}")
        warnings = consumer_warnings(text)
        for warning in warnings:
            print(f"WARN [소비자 테스트] {warning}")
        print(f"경고 {len(warnings)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
