# Step 8: smoke-docs

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` ("고정 제약", "명령어", 개발 프로세스), `/docs/PRD.md`(M1·M2·M3 행), `/docs/EVAL.md`(비용 상한 절)
- `/docs/ARCHITECTURE.md` ("디렉토리 구조", "상태 관리", "게이트웨이와 Guardrail 비대칭 구조"의 호출자 식별, "자격 증명 중개 흐름"), `/docs/ADR.md` (ADR-007, ADR-016, ADR-017)
- 이 phase의 산출물: `/src/govops/costs.py`, `/src/govops/gateway/client.py`, `/src/govops/gateway/agent_key.py`, `/src/govops/agent/checkpointer.py`, `/src/govops/agent/graph.py`, `/src/govops/api/wiring.py`, `/src/govops/api/tasks.py`, `/src/govops/api/cli.py`, `/Makefile`, `/tests/test_makefile.py`, `/tests/test_docs_consistency.py`
- m1-tools 산출물: `/src/govops/tools/smoke.py`(기존 스모크의 구조와 `SmokeResult`), `/docs/results/2026-10-04-m1-tools-live.md`

## 작업

사용자가 live 환경에서 실행할 **스모크 2개**(`smoke-checkpointer`, `smoke-agent`)를 만들고 문서를 이 phase의 결과에 맞춘다. 기대값은 **실행 전에 코드에 고정**한다. 하네스와 `make verify`는 스모크를 실행하지 않고, 로직은 가짜로 단위 테스트한다. TDD로 테스트를 먼저 쓴다.

### 1. 스모크 (`src/govops/api/smoke.py`, `python -m govops.api.smoke <checkpointer|agent>`)

```python
@dataclass(frozen=True) class SmokeResult: ok: bool; lines: list[str]     # tools/smoke.py와 같은 형태(재사용할 수 있으면 import)
async def smoke_checkpointer(url: str) -> SmokeResult
async def smoke_agent(service: TaskService, ledger: CostLedger, *, users: Sequence[str] = ("demo-platform-g3", "demo-finance-g3"),
                      request: str = ..., poll_interval: float = 1.0, timeout_s: float = 180.0) -> SmokeResult
def main(argv: list[str] | None = None) -> int     # 모두 ok면 0, 하나라도 어긋나면 1
```
- **`checkpointer`**: `GOVOPS_DATABASE_URL`로 `open_checkpointer`를 열고, 무작위 `thread_id`로 노드 1개짜리 작은 그래프를 실행해 체크포인트를 쓰고 `aget_state`로 같은 값을 읽는다. 읽은 값이 쓴 값과 같으면 통과. 끝에 해당 thread를 삭제한다(`adelete_thread`). 연결 문자열을 출력하지 않는다. URL이 비어 있으면 실패(in-memory로 대체하지 않는다).
- **`agent`**: 운영 조립(`api/wiring.py`)으로 만든 `TaskService`로 두 사용자(`demo-platform-g3`, `demo-finance-g3`)가 **같은 요청 문장**(`INC-2026-012 장애를 요약해 주세요. 근거로 쓴 문서 ID를 본문에 표기해 주세요.`)을 보낸다. 기대값(**실행 전 고정, 결과를 본 뒤 바꾸지 않는다**):
  1. 두 task 모두 `status == "completed"`.
  2. `EXPECTED_DOC = "syn-incident-022"`: platform의 `observed_doc_ids`와 `cited_doc_ids`에 있다.
  3. finance의 `observed_doc_ids`와 `cited_doc_ids`에 **없다**(문자열이 아니라 구조로 판정한다. 답변 문구는 판정에 쓰지 않는다).
  4. 실행 전후 원장(`data/manifest/costs.jsonl`)에 `agent_primary` 행이 **추가**되었고(두 task 합쳐 1건 이상), OpenAI 누적액이 상한(`9.13`) 미만이다.
  - 출력: 사용자별 상태·`steps`·`observed_doc_ids`·`cited_doc_ids`·답변(합성 문서 기반이라 출력해도 된다), 추가된 원장 행 수와 OpenAI 누적액·잔여 상한. 어긋난 기대값은 항목과 함께 출력하고 종료 코드 1이다. **어긋나도 기대값을 조정하는 옵션을 만들지 마라.** 모델이 `lookup_id`를 호출하지 않아 platform 기대값이 어긋나면 실패로 보고하고 원인을 조사한다.
  - 시작 전 점검: `LITELLM_AGENT_KEY`·`KODOCS_MCP_URL`·`GOVOPS_DATABASE_URL`(비어 있으면 in-memory로 계속하되 경고 한 줄)·원장 파일 존재를 확인하고, 없으면 **변수 이름만** 말하고 실패한다.
- **Makefile**: `smoke-checkpointer`(`uv run --env-file .env python -m govops.api.smoke checkpointer`), `smoke-agent`(`... smoke agent`) 추가. 각 타깃 위에 한 줄 주석(`smoke-checkpointer`: "compose·`make db-init` 필요, 쓰기·읽기 왕복", `smoke-agent`: "live A·LiteLLM·`make agent-key` 필요, 실제 호출 수 센트, 기대값: 두 사용자 completed·platform만 syn-incident-022 관찰·인용·원장 증가"). `verify`는 호출하지 않는다. `tests/test_makefile.py`를 확장한다.

### 2. 문서 동기화 (코드 변경 없음, 결정 내용만 쓰고 변경 경위는 쓰지 않는다)

- `AGENTS.md` "명령어" 섹션에 이 phase의 make 타깃(`costs-init`, `agent-key`, `db-init`, `serve`, `ask`, `smoke-checkpointer`, `smoke-agent`)을 한 줄씩 추가한다. **CRITICAL 규칙·계층 경계 표·기술 스택은 그대로 둔다.**
- `docs/ARCHITECTURE.md`: "디렉토리 구조"에 이 phase의 새 모듈(`agent/prompts.py`·`graph.py`·`checkpointer.py`, `gateway/client.py`·`agent_key.py`, `api/` 세션·task·wiring·cli·smoke, `costs.py`)을 반영하고, "상태 관리"의 **비용 원장**에 경로(`data/manifest/costs.jsonl`·`costs_grading.jsonl`, 커밋 대상, 원장이 없으면 호출 거부)를, **비밀 값**에 "M1은 에이전트 전용 가상 키 1개(`LITELLM_AGENT_KEY`)를 쓰고 팀별 키는 M4"를 적는다. M1 에이전트가 읽기 도구 6개만 연결한다는 점(부작용 도구는 spec만, 연결은 M3), `MAX_STEPS`가 임시 상수라는 점(최종값 M5)을 적는다. 확정되지 않은 것을 확정으로 적지 않는다.
- `docs/ADR.md` ADR-007: 원장 경로와 "원장이 없으면 호출 거부"를 결정 문장에 한 줄로 반영한다(기존 변경 기록에는 줄을 추가하지 않는다).
- `tests/test_docs_consistency.py`를 확장한다: AGENTS.md 명령어 섹션에 위 make 타깃이 모두 있고 CRITICAL 항목 수가 이전과 같다. ARCHITECTURE에 원장 경로 `data/manifest/costs.jsonl`이 있다.

### 3. 테스트 (`tests/test_api_smoke.py`)

- `smoke_agent`: 가짜 `TaskService`(또는 실제 `TaskService` + 스크립트 모델)와 `tmp_path` 원장으로 통과 경로(두 사용자 completed, platform에만 EXPECTED_DOC, 원장 행 증가, 상한 미만)를 검증한다.
- **불일치 감지**: (a) finance 결과에도 EXPECTED_DOC (b) platform 결과에 EXPECTED_DOC 없음 (c) 한 task가 `failed` (d) 원장 행 증가 없음 (e) 누적액이 상한 이상 — 각각 `ok=False`이고 어긋난 항목이 출력에 나온다. `EXPECTED_DOC`을 바꾸는 옵션·환경 변수가 없다(소스 검사).
- 시작 전 점검: 필수 변수가 없으면 변수 **이름만** 출력하고 실패. 출력(`SmokeResult.lines`·`capsys`·`caplog`)에 키·토큰 센티널이 없다.
- `smoke_checkpointer`: `open_checkpointer`를 `monkeypatch`로 in-memory 가짜로 바꿔 왕복 통과를, 읽은 값이 다르게 돌아오는 가짜로 실패를 검증한다. URL이 비어 있으면 실패. 출력에 연결 문자열이 없다.
- `tests/test_makefile.py`: 위 타깃 존재, `--env-file .env` 사용, `verify`가 호출하지 않음.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트를 확인한다:
   - 스모크 기대값(`syn-incident-022`, 상태, 원장 증가, 상한)이 실행 전 코드에 고정되고 어긋나면 실패로 보고하는가? 문자열이 아니라 관찰·인용 ID로 판정하는가?
   - 스모크가 키·연결 문자열을 출력하지 않는가? `make verify`가 스모크를 호출하지 않는가?
   - AGENTS.md의 CRITICAL 규칙·계층 경계 표·기술 스택이 변경되지 않았는가? 문서에 변경 경위가 없는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 스모크 명령이나 `make smoke-*`·`costs-init`·`agent-key`·`db-init`을 이 step에서 실행하지 마라. 이유: live 실행과 키 발급은 사용자가 한다. 테스트는 가짜와 `tmp_path`만 쓴다.
- 저장소의 `data/manifest/`에 원장 파일을 만들거나 수정하지 마라. 이유: 빈 원장 생성은 phase 완료 후 별도로 하고, 스모크 실행으로 쌓이는 행은 다음 브랜치 첫 커밋에 넣는다.
- 어긋난 결과에 맞춰 기대값을 조정하거나 불일치를 무시하는 옵션을 만들지 마라. 이유: 기대값은 사전 등록이며 어긋나면 실패로 보고하고 원인을 조사한다.
- 답변 문구(문자열)로 platform·finance를 판정하지 마라. 이유: 모델 출력은 비결정적이다. 관찰·인용 ID로만 판정한다.
- AGENTS.md의 CRITICAL 규칙·계층 경계 표를 약화·삭제하지 마라. 확정되지 않은 것(탐색 예산 N, MAX_STEPS 최종값, 세션 TTL 최종값, 쿠키·CSRF, 표식 형식, outbox 저장소)을 확정으로 적지 마라.
- docker, 설치·네트워크 명령을 실행하지 마라. `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.
