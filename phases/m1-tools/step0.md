# Step 0: contract-check

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (계층 경계 표의 `tools` 행, "고정 제약", 아키텍처 규칙의 "A 계약 스냅샷" 항목)
- `/docs/ARCHITECTURE.md` ("A 연동 모드"의 계약 고정 항목, "도구와 부작용·멱등성" 표)
- `/docs/ADR.md` (ADR-004, ADR-005의 불변식 "도구 스키마에 사용자·팀·직급 필드 없음")
- `/data/contract/manifest.json`, `/data/contract/index.json`, `/data/contract/*.v1.json` (A 커밋 `d2eec9c`에서 복사한 계약 스냅샷. **읽기 전용**)
- `/src/govops/tools/__init__.py`, `/tests/test_architecture.py`

## 작업

A MCP 계약 스냅샷을 검증·비교하는 순수 Python 모듈을 만든다. 네트워크 호출은 없다. TDD로 테스트를 먼저 쓴다.

### 스냅샷 파일 구조 (이미 복사되어 있음)

- `data/contract/{search_docs,lookup_id,run_sql,get_document}.v1.json`: 각 파일은 `{"name", "description", "input_schema", "output_schema", "version"}`.
- `data/contract/index.json`: `{"files": {<파일명>: <sha256>}}` (A가 만든 원본).
- `data/contract/manifest.json`: `{"a_commit": <A 커밋 전체 해시>, "source": ..., "files": {<파일명>: <sha256>}}` (B가 만든 것).

### 만들 파일: `src/govops/tools/contract.py`

```python
@dataclass(frozen=True)
class ContractTool:
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    version: int

class ContractMismatchError(RuntimeError): ...

def load_snapshot(root: Path | None = None) -> dict[str, ContractTool]
    # root 기본값은 저장소의 data/contract (이 파일 위치 기준으로 계산).
    # manifest.json의 파일별 sha256, index.json의 sha256과 실제 파일 내용을 모두 대조한다.
    # 하나라도 다르거나 파일이 빠졌거나 4개가 아니면 ContractMismatchError.

def snapshot_sha256(root: Path | None = None) -> str
    # manifest["files"]를 키 정렬·공백 없는 JSON(UTF-8)으로 직렬화해 SHA-256 hex.
    # live 실행 기록(M2)에 쓸 "계약 스냅샷 해시"다.

def snapshot_a_commit(root: Path | None = None) -> str   # manifest의 a_commit

def diff_tools_list(snapshot: Mapping[str, ContractTool], live_tools: Sequence[Any]) -> list[str]
    # live_tools는 MCP tools/list 결과다. 항목은 dict 또는 MCP SDK의 Tool 객체일 수 있다
    # (필드: name, description, inputSchema, outputSchema, _meta={"version": N}). 둘 다 받는다.
    # 비교 항목: 도구 이름 집합, description, input 스키마, output 스키마, version.
    # 같으면 빈 리스트, 다르면 사람이 읽을 수 있는 차이 목록(도구 이름·항목 이름만, 스키마 전문은 넣지 않는다).

def ensure_contract(snapshot: Mapping[str, ContractTool], live_tools: Sequence[Any]) -> None
    # diff가 비어 있지 않으면 ContractMismatchError (서비스 시작 중단용).

IDENTITY_FIELD_NAMES: frozenset[str]
def find_identity_fields(schema: Mapping[str, Any]) -> list[str]
    # JSON Schema를 재귀적으로(properties, $defs, items, anyOf/oneOf/allOf, additionalProperties가 스키마인 경우)
    # 훑어 사용자·팀·직급 신원 필드 이름을 찾는다. 반환은 "경로: 필드명" 문자열 목록.
    # 속성 이름 정규화: 소문자로 바꾸고 `_`, `-` 제거. 정규화한 이름이 다음 집합에 있으면 위반이다:
    # {user, userid, username, team, teamid, teamname, grade, rank, role, asker, requester, actor, identity, asuser}
```

핵심 규칙:
- 스냅샷 파일은 **수정하지 않는다**. 로더는 읽기만 한다.
- 비교는 구조적 동등성이다(키 순서 무시). `title` 같은 스키마 키도 그대로 비교한다.
- 오류 메시지에 스키마 전문이나 파일 내용 전체를 넣지 않는다.

### 테스트: `tests/test_contract.py`

- 저장소의 실제 `data/contract`를 로드하면 도구 4개(`search_docs`, `lookup_id`, `run_sql`, `get_document`)이고 모두 `version == 1`이며 `snapshot_a_commit`이 `d2eec9c`로 시작한다.
- 임시 디렉토리로 스냅샷을 복사한 뒤 (a) 파일 내용 한 글자 변경 (b) 파일 삭제 (c) manifest 해시 변경 (d) index.json 해시 변경 각각이 `ContractMismatchError`를 낸다.
- `diff_tools_list`: 동일하면 빈 리스트(dict 형식과 MCP `Tool` 객체 형식 둘 다. SDK 객체는 `mcp` 패키지의 `types.Tool`로 만든다), description 변경·input 스키마 변경·output 스키마 변경·도구 누락·도구 추가·version 변경을 각각 감지한다.
- `ensure_contract`가 불일치에서 예외를 낸다.
- `find_identity_fields`: 중첩된 `properties`, `$defs`, `anyOf` 안의 위반을 찾고, 대소문자·`_` 변형(`User_ID`, `teamName`, `asUser`)도 잡고, 무해한 이름(`query`, `sql`, `doc_id`, `kind`, `value`, `section_key`)은 잡지 않는다. **실제 A 도구 4개의 input 스키마에는 위반이 없다**는 테스트를 둔다.
- `snapshot_sha256`이 결정론이다(두 번 호출해 같은 값, 64자 hex).
- `data/contract/` 아래 파일이 이 step에서 바뀌지 않았다는 확인은 manifest 해시 대조 테스트로 갈음한다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - `govops.tools`가 금지된 계층(agent, api, eval, mocks, langgraph, LLM SDK)을 import하지 않는가?
   - `data/contract/`의 어떤 파일도 수정하지 않았는가?
   - 신원 필드 스캐너가 A 스냅샷 4개에 대해 위반 0건인가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `data/contract/` 아래 파일을 수정·재생성하지 마라. 이유: AGENTS.md CRITICAL, A 계약 스냅샷은 수정하지 않는다.
- MCP 서버에 접속하거나 네트워크를 쓰지 마라. 이유: 이 step은 순수 비교 로직이다. live 비교는 이후 step에서 클라이언트가 한다.
- docker, 설치·네트워크 명령(`uv add`, `uv sync`, `pip`, `curl`)을 실행하지 마라. 이유: 설치는 사용자가 한다. 필요한 패키지가 없으면 `blocked`로 보고한다.
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- `scripts/`, `phases/`, `web/`을 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.
