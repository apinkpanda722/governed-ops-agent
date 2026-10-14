# Step 1: v2-semantic-core

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_GUARDRAIL_V2_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m6-guardrail-v2.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/guardrails/screen.py`(`screen_user_messages`, `screen_tool_messages`, `screen_request`, `INPUT_PII_KINDS`, `QUARANTINE_MARKER`, `valid_tool_call_id`), `/src/govops/guardrails/pii.py`(`find_pii`, `mask_pii`), `/src/govops/guardrails/injection.py`(함수와 `scope` 인자만 읽는다. 규칙 파일은 열람하지 않는다)
- `/src/govops/agent/graph.py`(`read_tools_body`의 `content` 구성: `json.dumps` 뒤 `TOOL_RESULT_MAX_CHARS` 잘라내기), `/src/govops/agent/prompts.py`(읽기만 한다)
- `/tests/test_guardrails_screen.py`(`test_json_quarantine_and_idempotence`), `/tests/test_guardrails_boundary.py`, `/tests/test_redteam_boundary.py`, `/tests/test_guardrail_registration.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/guardrails/semantic.py`, `src/govops/guardrails/classifier_v2.json`, `tests/test_guardrails_semantic.py`. 단, 아래 **소비자 점검 대상** 단락의 파일은 그 단락의 조건(단언이 실제로 깨질 때, 새 값을 반영하는 것에 한함)에서만 이 목록에 포함된다. TDD.

열람 금지 경로: `data/interim/`, `data/redteam/`, `src/govops/guardrails/rules_v1.json`
수정 허용 경로: `src/govops/guardrails/semantic.py`, `src/govops/guardrails/classifier_v2.json`, `tests/test_guardrails_semantic.py`, `tests/test_architecture.py`, `tests/test_guardrails_boundary.py`, `tests/test_redteam_boundary.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/`·`data/redteam/` 아래 파일과 `src/govops/guardrails/rules_v1.json`의 내용을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 `data/interim/`·`data/redteam/`의 파일 내용을 읽지 않는다. v1 판정 함수(`screen_user_messages`·`screen_tool_messages`·`screen_request`)가 실행 중에 규칙 파일을 로드하는 것은 코드의 런타임 접근이므로 이 금지의 대상이 아니다. 테스트에는 규칙 내용을 옮겨 적지 않고 합성 문장과 함수 호출 결과 비교만 쓴다(규칙 파일의 패턴·사전을 모르는 상태에서 쓸 수 있는 입력은 ASCII가 아닌 평범한 한국어 문장, 형식만 맞춘 합성 PII, 그리고 `[정책으로 일부 제외]` 마커다).

**소비자 점검 대상**(**~에 한함, 단언 약화 금지**): 이 step의 변경을 시뮬레이션한 전체 `pytest`에서 깨진 테스트는 본문의 `테스트`·`검증` 절에 명시한 것뿐이며 명시가 없으면 깨진 테스트는 없었다. 다음 파일은 변경 대상 모듈·파일을 import·참조하는 소비자로 `수정 허용 경로`에 올려 두었다. **단언이 실제로 깨질 때만, 정확히 새 값을 반영하는 것에 한해** 수정한다. 정확 일치를 부분 집합·포함 비교로 바꾸지 않고, 깨지지 않는 파일은 수정하지 않는다: `tests/test_architecture.py`, `tests/test_guardrails_boundary.py`, `tests/test_redteam_boundary.py`. 이 단락의 파일을 수정했다면 최종 응답 summary에 파일명과 깨진 단언 이름을 적는다.

**배경**: 호스트 수집 지점이 쓸 **순수 로직**을 `guardrails/`에 만든다(표준 라이브러리와 `govops.guardrails.*`만 import. I/O·`httpx`·`litellm`·`fastapi` 없음). 분류 호출 자체는 step 2, 그래프 결선은 step 3이다. 이름 `semantic`·`classifier_v2`의 기존 사용처는 없다.

**경계(중요)**: `classifier_v2.json`의 프롬프트 문구와 어휘는 코드 머지 **뒤 smoke 전에** 별도 등록 커밋에서 확정한다. 이 step은 **구조와 해시 일치 테스트만** 만든다. 프롬프트·어휘 값은 자리표시(`PLACEHOLDER_…`)로 두고 실제 문구를 만들어 넣지 않는다.

### 1. `src/govops/guardrails/classifier_v2.json`(구조 고정, 값은 자리표시)

키 집합은 정확히 아래와 같다(추가·누락 불가):

```json
{
  "version": 1,
  "model": "guardrail-v2",
  "labels": {"safe": "PLACEHOLDER_SAFE", "unsafe": "PLACEHOLDER_UNSAFE"},
  "max_completion_tokens": 16,
  "timeout_s": 4.0,
  "connect_timeout_s": 2.0,
  "concurrency": 8,
  "round_item_limit": 16,
  "min_item_chars": 40,
  "item_max_chars": 2500,
  "head_chars": 1600,
  "tail_chars": 900,
  "truncation_marker": "\n…(중략)…\n",
  "tool_notice": "PLACEHOLDER_TOOL_NOTICE",
  "system_prompts": {"user_input": "PLACEHOLDER_USER_INPUT_PROMPT", "tool_result": "PLACEHOLDER_TOOL_RESULT_PROMPT"}
}
```

`head_chars + tail_chars + len(truncation_marker) <= item_max_chars`여야 한다(테스트로 단언).

### 2. `src/govops/guardrails/semantic.py`(순수 함수·데이터 클래스, 시그니처 수준)

- `load_config(path=None) -> dict`: 위 JSON을 읽고 키 집합·형식을 엄격 검사한다(알 수 없는 키·누락·타입 오류는 `ValueError`). `config_sha256(path=None) -> str`: 파일 **바이트**의 전체 sha256.
- `@dataclass(frozen=True) class Item: kind: Literal["user", "tool"]; text: str; message_index: int | None; path: tuple; truncated: bool` — `text`는 **분류기로 보낼 문자열**(v1 변환·6종 PII 마스킹·길이 상한 적용 뒤)이다.
- `select_user_item(request: str, config) -> Item | None`: `screen_user_messages([{"role": "user", "content": request}])`가 `block`이면 `None`(v2를 호출하지 않는다). 아니면 `mask_pii`로 6종(`INPUT_PII_KINDS`) 모두를 마스킹하고 길이 상한을 적용한 항목 하나. 최소 길이 미만이어도 사용자 입력은 항목이다.
- `select_tool_items(messages: Sequence[Mapping], config) -> tuple[list[Item], int]`: `role == "tool"`인 메시지만 대상으로 `screen_tool_messages`(게이트웨이와 같은 함수)를 적용해 v1 변환 뒤 `content`를 얻고, `content`가 JSON이면 문자열 값 중 **`min_item_chars` 이상이고 식별자 형식(`syn-…`·`[A-Z]{2,5}-[0-9]+`·32/64 hex·UUID·날짜)이 아니며 `QUARANTINE_MARKER`만으로 이루어지지 않은** 값을, JSON이 아니면(잘려 깨진 경우 포함) 본문 전체를 항목으로 한다. 각 항목 텍스트는 `mask_pii`로 6종을 마스킹하고 길이 상한(`head_chars` + 마커 + `tail_chars`)을 적용한다. 항목은 메시지 순서의 **역순(최신 도구 메시지 먼저)** 으로 `round_item_limit`개까지 고르고, 초과 개수를 두 번째 값으로 돌려준다.
- `@dataclass(frozen=True) class ClassifierRequest: messages: list[dict]; tools: list[dict] | None; tool_choice: str | None`와 `build_request(item: Item, config) -> ClassifierRequest`:
  - 사용자형: `messages = [system(config.system_prompts.user_input), user(item.text)]`, `tools=None`, `tool_choice=None`.
  - 도구형: `messages = [system(config.system_prompts.tool_result), user(config.tool_notice), assistant(content=None, tool_calls=[{id: "call_v2_0", type: "function", function: {name: "read_document", arguments: "{}"}}]), tool(tool_call_id="call_v2_0", content=item.text)]`, `tools=[{type: "function", function: {name: "read_document", description: "도구 결과를 읽는다", parameters: {type: "object", properties: {}}}}]`, `tool_choice="none"`.
  - **`item.text`는 래퍼·태그·접두어 없이 그대로** 넣는다(호스트 사전 선별과 게이트웨이 v1 판정의 동치성 때문이다).
- `parse_label(output: str | None, finish_reason: str | None, config) -> Literal["safe", "unsafe"] | None`: 앞뒤 공백 제거 후 `labels.safe`/`labels.unsafe`와 **정확 일치**일 때만 라벨이다. `finish_reason`이 `"length"`거나 어휘 밖·빈 값이면 `None`(파싱 오류).
- `apply_quarantine(messages: Sequence[Mapping], flagged: Mapping[tuple[int, tuple], bool]) -> tuple[list[dict], list[str]]`: 플래그된 `(message_index, path)`의 문자열 값(JSON 항목) 또는 본문 전체(비-JSON)를 `QUARANTINE_MARKER`로 바꾼 **새** 메시지 목록(입력은 변경하지 않는다)과 치환된 `tool_call_id` 목록을 돌려준다. 이미 마커뿐인 값은 건드리지 않는다.

### 3. 테스트(`tests/test_guardrails_semantic.py`, 가짜 분류기·함수 호출만, 네트워크 없음)

1. **구조·해시**: `load_config()`가 키 집합을 검사하고, 알 수 없는 키·누락 키는 `ValueError`다. `head_chars + tail_chars + len(truncation_marker) <= item_max_chars`. `docs/results/hypotheses-m6-guardrail-v2.md`에서 `^v2_config_sha256: (.+)$` 줄을 읽어 **둘 중 하나**를 단언한다: (a) 값이 64자리 hex이면 `config_sha256()`와 같다, (b) 값이 `UNREGISTERED`이면 `classifier_v2.json`의 `system_prompts`·`tool_notice`·`labels` 값이 모두 `PLACEHOLDER_`로 시작한다. 두 경우가 아니면 실패한다(등록 상태가 모호한 채 통과하지 않는다).
2. **선별**: v1 차단 사용자 입력은 `select_user_item`이 `None`이다. 전송 텍스트에 6종 PII가 없다(비-JSON 도구 본문의 `landline`·`card`·`account` 형식 합성값 포함, 형식만 맞춘 합성값을 쓴다). v1이 격리한 문장은 전송 텍스트에서 마커로만 남는다. 40자 미만·식별자 형식·마커-only는 제외된다. 길이 상한과 `truncated`. 라운드 항목 16개 상한과 초과 개수 계수, 최신 도구 메시지가 먼저 선택됨.
3. **요청 구성**: 사용자형은 2메시지·`tools is None`, 도구형은 4메시지이며 `assistant.tool_calls[0].id == tool.tool_call_id`이고 `valid_tool_call_id(...)`가 참, `tool_choice == "none"`. `messages[-1].content == item.text`(래퍼 없음).
4. **동치성(핵심, 호스트 선별 ↔ 게이트웨이 v1)**: 합성 문장 코퍼스(정상 업무 문장, 형식만 맞춘 합성 PII 포함 문장, 이미 마커가 있는 문장, 긴 문장)의 각 항목에 대해 `build_request(...).messages`를 `screen_request`(훅이 쓰는 함수)에 넣어 확인한다: 사용자형은 `verdict.action == "allow"`, 도구형은 `verdict.action == "allow"`이고 도구 메시지의 `content`가 **무변화**(해당 `ToolItem`의 `quarantined`·`masked`가 모두 거짓)이다. 고정 `system`·`user`(`tool_notice`) 문장도 `allow`·무변화다. 호스트의 `select_*` 결과가 항목으로 만든 것은 반드시 이 조건을 만족해야 하며, 만족하지 못하는 항목이 있으면 테스트가 실패한다(동치성 위반). 자리표시 `tool_notice`가 `allow`인지도 이 테스트가 확인한다.
5. **라벨 해석**: 정확 일치만 라벨, 장문·빈 값·`finish_reason == "length"`·어휘를 포함한 긴 문장은 `None`. 6. **치환 단조성**: `apply_quarantine`은 입력 메시지를 변경하지 않고, 플래그되지 않은 값·다른 메시지는 바이트 단위로 같다. 이미 마커뿐인 값은 그대로다.
7. **경계**: `semantic.py`가 표준 라이브러리와 `govops.guardrails.*`만 import하고 "redteam"·"data/eval"·"meta.json"·"data/replay" 문자열이 없다(기존 `tests/test_guardrails_boundary.py`가 이미 단언하므로 이 파일은 새로 만든 두 파일에도 통과해야 한다).

**사용자 실행(이 step에서 실행하지 않는다)**: 프롬프트·어휘 확정과 `v2_config_sha256` 등록은 코드 머지 뒤 smoke 전에 사용자가 별도 등록 커밋에서 한다.

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
   - 사용자 개입 필요 또는 지시가 서로 충돌해 판단할 수 없음 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 공통 금지사항

<!-- common-block:start -->
- **선점검**: 코드를 쓰기 전에 이 파일의 작업 지시·금지사항·"수정할 파일" 목록을 서로 대조한다. 충돌(하나가 요구한 것을 다른 것이 금지함, 수정할 파일 목록 밖 파일의 수정이 필요함, 계층 경계 위반)이 있으면 코드를 쓰지 않고 즉시 `blocked`로 보고한다. reason에는 충돌하는 두 문장을 그대로 인용한다. 충돌이 없으면 진행한다.
- **실제 서비스**: 실제 서비스(A MCP·LiteLLM·mock 컨테이너·외부 API)에 접속하는 코드는 작성할 수 있다. step 안에서 그 코드를 실행하거나 그 코드에 접속하는 테스트를 만들지 않는다. 테스트는 가짜 환경(가짜 실행기·로컬 루프백·합성 데이터)으로만 한다.
- **계층 경계**: `AGENTS.md`의 계층 경계 표와 `tests/test_architecture.py`를 따른다. 가드 테스트를 약화하지 않는다.
- **읽기 전용**: `.claude/`, `docs/results/`의 사전 등록 파일, `scripts/`, `phases/`, `data/contract/`, `data/eval/`, `data/replay/`·`data/results/`의 기록, A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)는 읽기만 한다(이 step의 "수정할 파일"에 있는 것만 예외). 기존 테스트의 단언을 약화하지 않는다.
- **비밀 값**: `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 않는다. 토큰·키·Authorization 헤더를 코드·테스트·문서·로그·trace에 남기지 않는다.
<!-- common-block:end -->

## 이 step의 금지사항

- `classifier_v2.json`의 `PLACEHOLDER_` 값을 실제 프롬프트·어휘 문구로 채우지 마라. 이유: 문구는 코드 머지 뒤 smoke 전에 별도 등록 커밋으로 확정한다(첫 live 전 등록, FP-043). 이 step은 구조와 해시 일치 테스트만 만든다.
- `select_*`가 만든 항목 텍스트에 래퍼·태그·접두어를 덧붙이지 마라. 이유: 게이트웨이 v1 판정과의 동치성이 깨진다(설계 0-3).
- 도구 항목 선별에 필드 이름 목록을 하드코딩하지 마라. 이유: mock-jira·A MCP 결과의 필드가 달라도 길이·식별자 규칙으로 선별해 의존을 줄인다(설계 부록).
- 식별자·해시·ID 형식 문자열에 PII 정규식을 돌려 정상 값이 거부되게 하지 마라. 이유: 무작위 hex가 전화번호 규칙에 걸리는 사례(FP-010). 식별자 형식은 항목에서 제외한다.
- 빈 항목 목록에서 `all(...)`·`not any(...)`로 통과시키는 테스트를 쓰지 마라. 이유: 검사 대상이 0건이면 공허하게 통과한다(FP-069). 항목이 1개 이상임을 먼저 단언한다.
