# Step 8: approval-docs-smoke

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-005·006·008·011·012·017·018), `/docs/ARCHITECTURE.md` ("에이전트 상태 전이", "도구와 부작용·멱등성"), `/docs/THREAT_MODEL.md` (V5·V6·V9·V15)
- `/docs/PRD.md`, `/docs/EVAL.md`, `/Makefile`, `/.env.example`, `/src/govops/tools/smoke.py`, `/tests/test_smoke_tools.py`, `/tests/test_makefile.py`, `/tests/test_docs_consistency.py`
- 이전 step 산출물 전부: `/src/govops/agent/`(draft·checks·scope·approval·graph), `/src/govops/api/`(app·tasks·wiring), `/src/govops/eval/scope_index.py`, `/src/govops/eval/tcr.py`, `/src/govops/tools/effects.py`

## 작업

문서를 결정 내용에 맞게 갱신하고 사용자 실행 스모크 `smoke-effects`를 만든다. 문서에는 **결정 내용만** 쓴다(누가 정했는지·변경 경위는 쓰지 않는다).

### 문서

- **ADR-018**: ① 8항의 "ID만 돌려준 `lookup_id` 결과는 제외" 문구를 **구조 규칙**으로 바꾼다: 근거 = 도구 결과에서 비어 있지 않은 `text`를 가진 문서(`search_docs` 발췌·`get_document`·`lookup_id` 모두)와 레코드가 돌아온 티켓. ② 실행 중 V15 검사는 문서 단위이고 값 기준인 평가 채점(`eval.audience`)보다 엄격하다는 점, 잘못 막힌 건은 TCR 실패로 세고 dev 결과 문서에 건수를 보고한다는 점. ③ 인덱스에 없는 문서(비합성 문서 등)는 fail-closed로 차단한다는 점과, step 1의 조사 결과(가/나/다)에 맞는 비합성 문서 처리(가: 생성기가 합침 / 나: 사용자 실행 타깃 / 다: 합성 문서만이므로 비합성 문서가 근거에 섞이면 차단 — 이 한계를 결과 문서에 적는다). ④ 시작 시 정리는 checkpointer 전체 순회이며 수백 건 규모를 전제한다(규모가 커지면 별도 테이블이 필요하다). ⑤ 수정 가능 필드(`text`·`body`·`title`·`description`)와 불변 필드, 수정 후 결정론 검사 재실행, 마스킹본 해시. ⑥ 에이전트 프로필(`READ_ONLY`·`WITH_WRITES`)과 `GOVOPS_AGENT_PROFILE`. ⑦ V15 근거에 `run_sql` 결과의 `ticket_id` 열 값이 티켓 근거로 들어가고, **ID 없는 `run_sql` 결과(집계·열 값)는 V15 검사를 받지 않는다**(T4 집계는 채점 쪽과 같이 범위 검사에서 제외한다). 이 한계를 `docs/THREAT_MODEL.md`의 V15 행(잔여 위험)에도 쓴다. ⑧ 승인자 GET은 결정론 검사를 통과한 승인 대기 초안만 보여 준다는 불변식, 403은 전사 공지의 요청자 본인에게만이라는 표.
- `docs/ARCHITECTURE.md`: 디렉토리 구조에 새 모듈(`agent/draft.py`·`checks.py`·`scope.py`·`approval.py`, `guardrails/policy.py`, `eval/scope_index.py`, `data/seed/access_index.json`, `data/policy/team_policy.json`)을 한 줄씩 추가하고, 상태 전이 그림의 상태 이름(`blocked`·`rejected`·`expired`·`interrupted`)과 API 응답 코드를 실제 구현과 맞춘다.
- `docs/THREAT_MODEL.md`: V6·V9·V15의 "확인하는 테스트"에 실제 테스트 파일 이름을 연결한다.
- `docs/EVAL.md`: TCR 절의 `approval_rejected` 조건에 "받아들여진 reject 기록이 있어야 한다"를 한 줄 추가한다. 안정성(M3) 절의 승인 API 검증 문장은 구현에 맞게 둔다.
- `AGENTS.md`: **명령어 블록만** 수정한다. `make test-outbox-pg`, `make scope-index`(·step 1에서 만들었다면 `scope-index-a`), `make smoke-effects` 항목을 추가한다(다른 부분은 건드리지 마라).
- 이 phase가 푼 "미정"이 남아 있으면 갱신한다. 장애 주입 표본 수·모델 장애 주입 방법은 **그대로 둔다**.

### `make smoke-effects` (사용자 실행, `tools/smoke.py`에 `effects` 하위 명령)

- 전제: compose mock 기동, mock 토큰·시드 준비. 실제 mock 컨테이너(호스트 8081·8082)에 대해 `SMOKE_USER`(기본 `demo-platform-g3`)로 ① 자기 팀 채널에 합성 메시지 1건을 `execute_effect`(InMemory outbox·임시 감사 로그·표식 포함)로 발송 ② 표식 조회로 발송이 보이는지 확인 ③ **같은 키로 한 번 더 실행**해 호출이 일어나지 않고(`already_succeeded`) 채널 이력의 같은 표식 메시지가 정확히 1건인지 확인 ④ 코멘트·후속 티켓도 같은 방식으로 1건씩. 출력은 PASS/FAIL 줄만이며 **토큰·본문 원문·Authorization을 출력하지 않는다**. 합성 메시지에는 `smoke` 표시를 넣는다.
- `Makefile` 타깃 `smoke-effects`(`.PHONY`, 주석에 "compose mock·토큰 필요, 사용자 실행"). 이 smoke는 `make verify`에 넣지 않는다. 테스트는 가짜 transport로 smoke 로직만 검증한다(실제 네트워크 접속 없음).
- `tests/test_makefile.py`·`tests/test_docs_consistency.py`가 통과해야 한다(테스트를 약화하지 말고 문서·Makefile을 맞춘다).

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

- 결정 주체나 변경 경위를 문서에 쓰지 마라. 이유: 문서에는 결정 내용만 쓴다.
- 장애 주입 표본 수와 모델 장애 주입 방법의 "미정"을 채우지 마라. 이유: 다음 phase에서 사전 등록 문서와 함께 정한다.
- `docs/results/hypotheses.md`를 수정하지 마라. 이유: 사전 등록 파일이다.
- smoke가 실제 사용자 데이터(합성 아닌 값)나 토큰을 출력하게 만들지 마라.
- `AGENTS.md`는 명령어 블록 외를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.
