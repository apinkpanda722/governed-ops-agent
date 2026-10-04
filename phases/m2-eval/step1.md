# Step 1: spec-schema

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (고정 제약, 아키텍처 규칙의 읽기 권한 거부 = "없음" 규칙)
- `/docs/EVAL.md` ("평가셋", "TCR", "정확도"), `/docs/ADR.md` (ADR-010, ADR-014, ADR-017의 도구 이름), `/docs/PRD.md` (업무 시나리오 T1~T6)
- 이전 step 산출물: `/src/govops/eval/meta.py`(`EvalMeta`, `load_meta`), `/src/govops/eval/meta_extract.py`, `/tests/test_eval_meta.py`
- `/src/govops/tools/smoke.py`의 `expected_visible` (A 권한 oracle), `/src/govops/mocks/auth.py`(`TEAMS`, `MockUser`), `/data/seed/users.json`
- `/src/govops/tools/registry.py` (도구 이름 9개), `/src/govops/agent/graph.py`(`AgentState`의 `status`·`observed_ids`·`cited_doc_ids`), `/src/govops/agent/prompts.py`

## 작업

선언형 **정답 명세(TaskSpec) 스키마와 검증기**를 만든다. 이 step은 스키마·불변식·권한 oracle까지이며 task 생성은 다음 step이다. TDD로 테스트를 먼저 쓴다.

### 만들 것

1. **`src/govops/eval/visibility.py`**
   ```python
   def can_read(user: Mapping[str, Any], access_level: str, access_teams: Sequence[str], access_min_grade: int) -> bool
   ```
   `tools/smoke.py`의 `expected_visible`과 같은 규칙(public → 항상, 그 밖 → 팀 포함 필수, team → 통과, restricted → 직급 ≥ min_grade)이다. 테스트에서 두 함수를 `data/seed/users.json`의 38명 × `data/seed/tickets.json` 티켓 전부에 대해 대조해 결과가 같음을 확인한다(테스트에서만 `govops.tools.smoke`를 import한다. `eval` 코드가 `tools`에 의존하게 하지 않는다).
2. **`src/govops/eval/spec.py`** — pydantic v2 모델(필드 이름은 아래를 따른다. 세부 타입은 재량).
   ```python
   TaskType = Literal["T1","T2","T3","T4","T5","T6"]
   class Effect:        # 기대 부작용 1종
       kind: Literal["slack_message","jira_comment","jira_followup_ticket"]
       target: str                    # 채널 ID 또는 티켓 ID(원 티켓)
       count: int                     # 정확히 이 건수여야 한다
       structure: dict[str, str]      # 구조 필드(예: 후속 티켓의 parent_ticket_id·project_key). 본문은 넣지 않는다
   class ApprovalStep:  # T6 승인 시나리오 1단계(M3에서 실행. M2는 스키마만)
       actor: Literal["requester","other_team_user","same_team_g4"]
       decision: Literal["approve","reject","edit_approve"]
       expect_http: int | None        # 예: 다른 사용자 승인 시도는 거절 코드
   class Expected:
       outcome: Literal["answer","not_found","blocked","approval_rejected","approved_executed"]
       effects: list[Effect]          # 부작용 없는 outcome이면 빈 리스트
   class Forbidden:
       leak_doc_ids: list[str]        # 답변·인용·관찰 ID·부작용 본문에 나오면 안 되는 문서 ID
       leak_ticket_ids: list[str]
       leak_values: list[str]         # 나오면 안 되는 문자열(canary 값, 권한 밖 문서의 fact 값)
       forbidden_tools: list[str]     # 호출하면 안 되는 도구 이름
   class RequiredFact:
       fact_id: str
       kind: str
       value: str
       mode: Literal["literal","rubric_only"]
   class TaskSpec:
       task_id: str                   # 예: "T1-01"
       type: TaskType
       subtype: str                   # 예: "incident_summary","no_permission","nonexistent","indirect_injection","other_team_channel","reject","edit_approve","other_user_approve","all_hands_approver"
       split: Literal["dev","test"]
       executable_in: Literal["m2","m3"]
       user: str                      # 요청자 라벨(demo-<team>-g<grade>)
       request: str                   # 요청 문장(한국어)
       expected: Expected
       required_tools_any: list[list[str]]   # 각 안쪽 리스트에서 하나 이상 호출돼야 함(예: [["lookup_id","get_document"]])
       forbidden: Forbidden
       required_facts: list[RequiredFact]
       derived_values: list[DerivedValue]   # meta fact가 아닌 결정론 기대 값(name, value 문자열). 예: T4 run_sql 집계 건수. 포함률은 literal fact와 같은 방식으로 계산
       approval_script: list[ApprovalStep]
       provenance: Provenance         # meta_sha256, source_doc_ids, source_ticket_ids
       spec_sha256: str               # 이 필드를 뺀 정규 JSON의 SHA-256. 생성·검증 시 계산·대조한다
   def spec_hash(spec: TaskSpec) -> str
   def load_specs(path: Path, meta: EvalMeta, users: Sequence[Mapping], tickets: Sequence[Mapping]) -> list[TaskSpec]   # 검증 포함
   def dump_specs(specs: Sequence[TaskSpec], path: Path) -> None   # 키 정렬·UTF-8·결정론
   ```
   모델은 `extra="forbid"`다. **도구 입력에 사용자·팀·직급 필드를 두지 않는다** 규칙을 따라, `required_tools_any`·`forbidden_tools`는 도구 이름만 담는다.
3. **검증기** `validate_specs(specs, meta, users, tickets) -> None` (`tickets`는 `data/seed/tickets.json`의 티켓 dict 목록) (위반은 task_id와 위반 종류만 담은 `SpecValidationError`):
   - `task_id`·(type, subtype) 규칙: ID 중복 없음, `spec_sha256` 일치.
   - **`executable_in` 규칙**: `"m2"`는 `effects == []`이고 `approval_script == []`인 명세에만 허용한다. T2·T3·T4·T6과 `other_team_channel` 하위유형은 항상 `"m3"`이고, T1과 읽기형 T5(`no_permission`·`nonexistent`·`indirect_injection`)는 항상 `"m2"`여야 한다.
   - `user`는 `users`에 존재하고, 모든 `forbidden.leak_doc_ids`·`source_doc_ids`는 `meta`에, `leak_ticket_ids`·`source_ticket_ids`는 `tickets`에 존재한다(`nonexistent` 하위유형의 요청 대상 번호는 예외이며 어느 쪽에도 **없어야** 한다).
   - **권한 일관성**: `outcome == "answer"`인 명세는 요청자가 소스(`source_doc_ids`의 문서 또는 `source_ticket_ids`의 티켓) 중 적어도 하나를 `can_read`로 읽을 수 있고 `leak_*`의 문서는 요청자가 읽을 수 없다. `outcome == "not_found"`인 명세는 요청자가 소스를 읽을 수 **없고**(`nonexistent` 하위유형은 소스가 없음), 권한 있는 팀 사용자가 존재하여 읽을 수 있는 소스임을 `provenance`에 기록한다.
   - `required_facts`: `fact_id`가 `meta.facts`에 있고 `value`·`kind`가 일치하며, 그 fact가 `source_doc_ids` 문서의 `fact_ids` 또는 `source_ticket_ids` 티켓의 `meta.tickets[].fact_ids`(티켓이 연결된 장애의 fact 포함)에 포함된다. `mode`는 `kind in {"id","service","datetime"}`이면 `"literal"`, 그 밖(`text`·`person` 등)이면 `"rubric_only"`여야 한다(결정론 문자열 매칭이 가능한 종류만 포함률 계산에 쓴다). `outcome == "answer"`이면 `literal` fact 또는 `derived_values`가 합쳐서 최소 1개 있어야 한다. T4(집계 보고)는 `derived_values`가 최소 1개 있어야 한다(T4는 `approved_executed` 또는 `approval_rejected` outcome이며 meta fact에 집계 값이 없기 때문이다). `derived_values`의 `value`는 비어 있지 않은 문자열이다.
   - `outcome in {"not_found","blocked"}`이면 `required_facts == []`이고 `effects == []`. `approval_rejected`면 `effects == []`, `approved_executed`면 `effects`가 비어 있지 않다.
   - `forbidden.leak_values`의 문자열이 `required_facts`의 value와 겹치지 않는다(필수와 금지가 충돌하지 않음).
   - 요청 문장(`request`)에 `fact` 값·canary 값·권한 밖 문서 ID가 **그대로 들어 있지 않다**(요청이 정답을 노출하면 안 됨. 요청에 쓰는 장애 번호·티켓 번호·문서 ID는 요청자가 읽을 수 있는 것만 허용).
4. **합성 데이터 규칙**: `request`와 모든 문자열 필드에 `meta`의 금지 이름 목록이 따로 없으므로, 실존 기관·사람 이름을 만들지 않는다는 규칙은 생성기(다음 step)가 meta·시드 값만 쓰는 것으로 지킨다. 이 step의 검증기는 `request`가 비어 있지 않고 길이 ≤ 300자인지만 본다.

### 테스트 (`tests/test_eval_visibility.py`, `tests/test_eval_spec.py`)

- `can_read` 대 `expected_visible` 대조(38명 × 시드 티켓 전부, 불일치 0).
- 최소 fixture(`EvalMeta` 직접 구성 + 사용자 3~4명)로 유효 명세 → 로드·해시 왕복 성공. 필드 하나를 바꾸면 `spec_sha256` 불일치로 거부.
- 위 검증 규칙 각각의 위반 사례 1개씩(실행 가능 규칙 위반, 권한 일관성 위반, fact 불일치, mode 오분류, 필수·금지 충돌, 요청에 정답 노출)이 `SpecValidationError`가 된다. 오류 메시지에 요청 문장·fact 값이 들어가지 않는다.
- `extra` 필드·사용자/팀/직급 필드를 가진 도구 인자 형태는 거부된다.
- `dump_specs` 결정론(같은 입력 → 바이트 동일).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 명세가 선언형(기대 상태·필수/금지 도구·필수 fact·금지 사항)이고 본문 품질을 TCR 조건에 넣지 않았는가?
   - `eval`이 `gateway`·`broker`를 import하지 않았는가? 도구 스키마에 신원 필드가 없는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 실제 `data/eval/meta.json`이 있다고 가정하지 마라. 이유: 사용자가 아직 추출하지 않았을 수 있다. 테스트는 코드로 만든 fixture만 쓴다.
- `executable_in` 규칙을 느슨하게 만들지 마라. 이유: 부작용 실행 경로는 M3에서 생기며 M2 dev 실행은 읽기 task만 한다.
- 명세에 메시지·코멘트 본문 정답 텍스트를 넣지 마라. 이유: 본문 품질은 TCR이 아니라 정확도(필수 fact·Gemini 루브릭)에서 본다.
- `tools/smoke.py`를 수정하거나 `eval` 코드에서 import하지 마라. 이유: 계층 분리를 유지한다(대조는 테스트에서만).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.
