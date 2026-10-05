# Step 1: access-index

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-005·006·008·011·012·017·018), `/docs/ARCHITECTURE.md` ("에이전트 상태 전이", "도구와 부작용·멱등성"), `/docs/THREAT_MODEL.md` (V5·V6·V9·V15)
- `/src/govops/eval/audience.py`, `/src/govops/eval/visibility.py`, `/src/govops/eval/meta.py`, `/src/govops/eval/meta_extract.py`, `/data/eval/meta.json`(구조만 확인), `/data/seed/tickets.json`, `/data/seed/users.json`, `/data/seed/manifest.json`, `/docs/EVAL.md`("발송 범위 규칙")
- A 저장소 **읽기 전용 조회**: `~/PycharmProjects/ko-docs-agentic-rag/src/kodocs/retrieval/access.py`, `src/kodocs/retrieval/index_schema.py`, `src/kodocs/tools/contract.py`, 커밋된 `data/seed/`·`data/manifest/`·`data/golden/`

## 작업

에이전트가 실행 중에 쓰는 **열람 범위 인덱스**를 만든다. 에이전트는 `data/eval/meta.json`(facts·canaries·injections가 들어 있음)을 읽으면 안 되므로, 문서·티켓 ID와 접근 필드만 담은 별도 파일을 생성해 `data/seed/`에 커밋한다. TDD로 테스트를 먼저 쓴다.

### 산출물

- `src/govops/eval/scope_index.py`: `build_index(meta, tickets) -> dict`와 CLI(`python -m govops.eval.scope_index`). 출력 `data/seed/access_index.json`:
  `{"generated_from": {"meta_sha256", "tickets_sha256", "a_commit"}, "docs": {doc_id: {"level", "teams", "min_grade"}}, "tickets": {ticket_id: {"level", "teams", "min_grade"}}}`. 키 정렬·UTF-8·끝 개행. 담는 것은 ID와 이 세 필드뿐이다.
- `Makefile`: `scope-index` 타깃(오프라인·결정론, 키·네트워크 불필요). `.PHONY` 등록과 주석.
- 커밋할 `data/seed/access_index.json`을 이 step에서 생성해 둔다.

### 비합성 문서 조사 (A 저장소 읽기 전용)

`meta.json`은 합성 문서(`syn-*`)만 안다. live A에는 비합성 문서도 있고 그 접근 필드가 필요한지 조사한다.
1. A의 **커밋된 파일**(`src/kodocs/retrieval/access.py`, `src/kodocs/synth/`, 커밋된 `data/seed/`·`data/manifest/` 등)에서 비합성 문서의 접근 필드(`access_level`·`access_teams`·`access_min_grade`)를 얻을 수 있는지 본다.
2. **A의 `data/interim/`은 열지 마라**(토큰 파일이 있다). `data/raw/`와 인덱스 데이터도 열지 않는다. 커밋되지 않은 데이터에만 있으면 아래 (나)로 처리한다.
3. 결과는 셋 중 하나다. 어느 경우인지 **최종 응답 summary의 맨 앞에 `[가]`·`[나]`·`[다]`로 적는다**.
   - (가) 커밋된 A 파일에서 읽을 수 있다 → 생성기가 읽기 전용으로 합쳐 인덱스에 넣는다(`a_commit`·개수를 `generated_from`에 기록, 실제 규정 원문은 복사하지 않고 ID·접근 필드만).
   - (나) A의 커밋되지 않은 데이터에만 있다 → 사용자 실행 `make` 타깃(예: `scope-index-a`, `A_ROOT` 사용, 읽을 파일을 코드에 명시하고 토큰 파일은 절대 열지 않음)을 만들고, 결과 파일만 커밋하도록 한다. 이 step에서는 합성 문서 인덱스만 생성해 커밋한다.
   - (다) 둘 다 불가능 → 인덱스에는 합성 문서만 둔다. 인덱스에 없는 문서는 이후 step에서 **fail-closed 차단**된다는 점을 summary에 적는다(문서화는 마지막 step이 한다).

### 테스트

- 재생성 결과가 커밋된 `access_index.json`과 같다(`meta.json`·`tickets.json` 해시가 바뀌면 테스트가 실패해 재생성을 요구한다).
- 인덱스에 `facts`·`canaries`·`injections`·`text`·`title` 등 접근 필드 외 키가 없다. 모든 `docs` 키는 `syn-` 접두어(가의 경우 비합성 항목은 예외로 허용 목록을 명시).
- 접근 필드 값이 `meta.json`·`tickets.json` 원본과 일치한다. 사용자·직급 정보는 인덱스에 넣지 않는다(`users.json` 사용).
- **경계 테스트** `tests/test_agent_data_boundary.py`: `src/govops/agent/` 아래 모든 파일에서 ① `govops.eval` import가 없고(AST) ② 문자열 `data/eval`·`meta.json` 참조가 없다. 지금은 통과해야 하고, 이후 step에서도 이 테스트가 유지된다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조와 AGENTS.md 계층 경계를 따르는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 인덱스에 본문·사실·제목을 넣지 마라. 이유: 에이전트가 실행 중 읽는 파일이므로 접근 필드만 있어야 한다.
- `agent/`가 `govops.eval`을 import하게 하지 마라. 이유: 계층 경계다. 인덱스 생성기는 `eval/`에, 읽는 쪽은 다음 step의 `agent/`에 둔다.
- A의 `data/interim/`, `data/raw/`, 토큰 파일을 열지 마라. 이유: 비밀 값이 있다.
- `data/eval/meta.json`을 수정하지 마라. 이유: 명세 해시가 바뀌면 사람 검수 확정본이 무효가 된다.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.
