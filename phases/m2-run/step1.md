# Step 1: a-record-through

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ARCHITECTURE.md` ("replay"), `/docs/ADR.md` (ADR-004), `/docs/EVAL.md` ("golden trace"), `/docs/PRD.md` (M2 행: `search_docs` 기록은 상위 결과가 모두 `syn-*`인 운영 질의, 건너뛴 비율 보고, 응답 편집 금지)
- `/src/govops/tools/record_a.py` (기존 `record-a`: `requests.json`의 정해진 질의만 기록, 비합성 문서 응답은 건너뜀), `/src/govops/replay/recordings.py` (`Recording`, `validate_recording`, `RecordingSet`, `write_recordings`, `non-synthetic doc_id` 거부), `/src/govops/replay/fake_a.py`, `/data/replay/requests.json`
- `/src/govops/tools/registry.py`·`/src/govops/tools/a_mcp.py` (A 도구 호출 경로: 에이전트가 쓰는 `ToolExecutor`), `/tests/test_record_a.py`, `/tests/test_replay_recordings.py`, `/tests/test_replay_data_rules.py`
- 이전 step: `/src/govops/replay/llm.py`

## 작업

`record-a`는 `requests.json`의 정해진 질의만 기록한다. 골든 재생에는 **모델이 실제로 보낸 질의**의 A 응답이 필요하므로, 에이전트 실행 중 **실제로 나간 A 호출을 응답과 함께 기록하는 record-through**를 만들고, **A 기록 검증을 `record-a`와 record-through에 똑같이 적용**한다. TDD로 테스트를 먼저 쓴다.

### 만들 것

1. **공통 검증 — `src/govops/replay/synthetic.py`** (또는 `recordings.py`에 함수 추가):
   ```python
   @dataclass(frozen=True) class RecordOutcome: recorded: bool; reason: str | None   # reason: "non_synthetic_doc" 등 값 없는 코드
   def screen_recording(entry: Recording) -> RecordOutcome     # validate_recording을 호출, 비합성 문서가 있으면 recorded=False
   @dataclass class SkipStats: total: int; recorded: int; skipped: int; ratio() -> float
   ```
   규칙: 응답에 `syn-*`가 아닌 `doc_id`가 있으면 **그 호출은 기록하지 않고 건너뛴다**(응답을 일부만 남기거나 고쳐 쓰지 않는다 = 응답 편집 금지). 토큰·Authorization·`api_key` 값이 있으면 건너뛰지 않고 `RecordingRejected`로 실패한다(기존 동작 유지). 건너뛴 호출 수와 비율을 `SkipStats`로 센다. 기존 `record_a.py`의 검사를 이 함수로 옮겨 **`record-a`가 같은 함수를 쓰게** 하고 동작·출력은 바꾸지 않는다(기존 테스트 통과).
2. **record-through 실행기 래퍼 — `src/govops/replay/a_through.py`**:
   ```python
   class RecordingToolExecutor:   # 안쪽 ToolExecutor를 감싸는 래퍼(agent의 ToolExecutor Protocol과 같은 call 시그니처를 구조적으로 만족)
       def __init__(self, inner, *, task_id: str, directory: Path)
       async def call(self, name, arguments, *, user_id) -> ToolOutcome-like
       def finish(self) -> RunRecordSummary   # SkipStats, 건너뛴 호출 목록(도구 이름·인자 해시만, 값 없음), 기록 경로
   ```
   A 도구(`search_docs`, `lookup_id`, `run_sql`, `get_document`)의 호출만 기록한다(mock-jira·mock-slack 읽기 도구는 기록 대상이 아니다. mock은 시드에서 결정론이다). 기록 형식은 기존 `Recording`(`caller`=user_id, `tool`, `arguments`, `result`, `is_error`)이고 파일은 `data/replay/a_run/<task_id>.jsonl`이다. 호출 순서를 보존한다. 같은 (caller, tool, 인자 해시)가 반복되면 마지막 응답이 아니라 **호출 순서대로** 전부 남긴다. 비합성 문서가 있어 건너뛴 호출이 하나라도 있으면 `RunRecordSummary.golden_eligible = False`와 `skipped_calls`를 담는다(task별 자격 판정 재료).
3. **재생용 A 실행기 — `src/govops/replay/a_replay.py`**: `ReplayToolExecutor`가 기록 파일을 읽어 같은 (caller, tool, 인자 해시) 요청에 **기록한 응답을 순서대로 그대로** 돌려준다(가짜 A 서버의 자체 데이터로 응답하지 않는다). 기록에 없는 질의가 오면 `RerecordRequired`(step 0의 예외)를 던진다. 안쪽에 mock 읽기 실행기를 주입하면 jira·slack 읽기 도구는 그쪽으로 위임한다.
4. **`record-a` 확장 없음**: `requests.json`·`record-a` 대상은 늘리지 않는다. 문서 `search_docs` 운영 질의 선별은 record-through가 맡는다.

### 테스트 (`tests/test_replay_a_through.py` + 기존 `test_record_a.py` 보강)

- `record-a`와 record-through가 **같은 `screen_recording`**을 쓴다(두 경로에 비합성 문서 응답을 넣어 둘 다 건너뛴다).
- 비합성 문서가 섞인 응답은 일부만 남기지 않고 호출 전체를 건너뛴다, `SkipStats`가 건너뛴 수·비율을 센다, 건너뛴 호출 요약에 값·본문이 없다.
- 응답 편집 금지: 기록된 응답은 안쪽 실행기가 돌려준 값과 바이트 단위로 같다.
- 토큰·Authorization이 있는 응답은 실패한다.
- 호출 순서 보존, 같은 질의 반복도 모두 기록, `golden_eligible`이 건너뛴 호출이 하나라도 있으면 False.
- `ReplayToolExecutor`: 기록한 응답을 그대로·순서대로 돌려준다, 기록에 없는 질의는 `RerecordRequired`, 안쪽 mock 위임이 동작한다, **가짜 A 서버(`replay/fake_a.py`)를 쓰지 않는다**.
- `data/replay/` 아래 파일의 문서가 모두 `syn-*`라는 기존 데이터 규칙 테스트(`test_replay_data_rules.py`)가 `a_run/` 파일도 검사하도록 확장한다(파일이 아직 없어도 통과).
- 계층 가드 통과.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조와 AGENTS.md 계층 경계를 따르는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - AGENTS.md CRITICAL 규칙(토큰 비노출, 실제 API 호출 금지, 합성 문서 `syn-*`만 기록)을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `Recording` 형식과 `data/replay/a/recordings.jsonl`·`requests.json` 내용을 바꾸지 마라. 이유: 기존 계약·가짜 A 테스트가 의존한다.
- 비합성 문서가 있는 응답에서 해당 문서만 지우거나 고쳐 기록하지 마라. 이유: 응답 편집은 재생을 live와 다르게 만든다(건너뛰고 비율을 보고한다).
- record-through를 별도 실행 타깃이나 추가 live 실행으로 만들지 마라. 이유: 실행 계획표(dev 20×1)에 없는 추가 dev 실행이다. 다음 step들의 `make eval-run-dev`가 한 번의 실행에서 함께 기록한다.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다(AGENTS.md CRITICAL).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/tasks.json`, `data/eval/meta.json`을 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.
