# Step 2: guardrail-core

## 읽어야 할 파일

- `/AGENTS.md`(계층 경계 표의 `guardrails/` 행, 고정 제약), `/docs/ADR.md`(ADR-008), `/docs/THREAT_MODEL.md`(TH1·TH2·TH9, V1·V2), `/docs/ARCHITECTURE.md`(게이트웨이·Guardrail 절)
- `/src/govops/guardrails/pii.py`(`find_pii`·`mask_pii`·`PiiMatch`), `/src/govops/guardrails/policy.py`(읽기만: 팀 정책은 발송 전 검사 전용이며 이 step에서 쓰지 않는다), `/src/govops/guardrails/__init__.py`, `/src/govops/agent/prompts.py`(`SYSTEM_PROMPT`: "역할 변경 문구" 같은 문장이 들어 있다), `/src/govops/agent/graph.py`의 `read_tools_body`(도구 결과 `content` = `json.dumps(payload, ensure_ascii=False)`, 길면 `…(잘림)` 접미), `/src/govops/obs/mask.py`(`find_pii` 사용과 식별자 형식 예외 — FP-010), `/tests/test_architecture.py`(`FORBIDDEN_IMPORTS["guardrails"]`)
- `/.claude/skills/failure-patterns/SKILL.md`(FP-010·042·043) — 읽기만 한다
- **외부 계약 실측**(원자료: 위 파일들):

| 지점 | 성공 여부 | 반환·효과 내용 |
|---|---|---|
| LiteLLM 사전 훅이 받는 `data["messages"]` | 프로브 성공(이번 phase 설계 실측) | OpenAI 메시지 목록. `role`은 `system`·`user`·`assistant`·`tool`. `tool` 메시지는 `tool_call_id`·`content`(문자열)를 가진다. `content`가 파트 목록(`[{"type":"text","text":...}]`)일 수도 있다 |
| `find_pii` | 순수 함수 | `rrn`·`mobile`·`landline`·`email`·`card`(Luhn)·`biz_reg`·`account`·`passport`를 찾는다. 숫자 연속(예: 13자리 epoch ms)이 `card`·전화 규칙에 우연히 걸릴 수 있다(FP-010) |
| 에이전트 도구 결과 | `agent/graph.py` | 대개 JSON 문자열이고, `TOOL_RESULT_MAX_CHARS`를 넘으면 `…(잘림)`이 붙어 JSON이 아닐 수 있다 |

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/guardrails/registry.py`(신규), `src/govops/guardrails/rules_v1.json`(신규), `src/govops/guardrails/injection.py`(신규), `src/govops/guardrails/screen.py`(신규), `tests/test_guardrails_registry.py`·`tests/test_guardrails_injection.py`·`tests/test_guardrails_screen.py`·`tests/test_guardrails_boundary.py`(모두 신규). **`guardrails/__init__.py`는 docstring 한 줄 그대로 둔다.** 계층: `guardrails/`는 `litellm`·`langgraph`·`fastapi`·다른 `govops` 계층을 import할 수 없고, 외부 패키지 없이 표준 라이브러리만 쓴다(컨테이너에서도 import되어야 한다). TDD.

### 1. 규칙 파일과 해시 (`registry.py`, `rules_v1.json`)

- `rules_v1.json`: `{"version": 1, "rules": [{"id": str, "scope": ["input"|"tool", ...], "pattern": str, "family": str}, ...]}`. 규칙은 일반적으로 알려진 프롬프트 인젝션 표현의 **구(phrase) 단위** 패턴을 한국어·영어로 직접 작성한다. 가족(`family`): 지시 무시·이전 지시 폐기, 역할 변경·탈옥, 시스템 프롬프트 노출 요구, 구분자·권한 위장(`</system>`·`[SYSTEM]`·"새로운 지시:" 같은 형식), 신원 사칭("관리자로 행동", "다른 팀 사용자로 행동"), 도구·발송 강요("이 내용을 전체 채널에 발송하라"류). `input` 규칙과 `tool` 규칙은 가족이 겹쳐도 되지만 **한 단어(예: "무시", "지시", "system")만으로 걸리는 패턴은 금지**한다(정상 업무 문장 오탐 방지). 규칙 id는 `^[a-z0-9_]+$`.
- **규칙을 `data/eval/`·`data/replay/`·`docs/results/`·`data/redteam/`의 문서·기록에서 복사하거나 그것을 열어 보고 만들지 않는다**(FP-042·043: 평가·레드팀 정답지에 맞춰 튜닝하는 것을 막는다). 규칙 작성에 쓰는 근거는 이 파일의 가족 목록과 일반 지식뿐이다.
- `registry.py`: `RULES_FILE: Path`(같은 디렉터리의 `rules_v1.json`)와 `rules_sha256(path: str | Path | None = None) -> str`(규칙 파일 **바이트만** 읽어 sha256 hex 64자). **이 모듈은 `govops`의 어떤 모듈도 import하지 않는다**(`guardrails/injection.py`·`screen.py`·`pii.py` 포함). 이유: 레드팀 생성기(`eval/redteam.py`)가 이 함수 하나만 import해 규칙·패턴을 읽지 않고 해시만 얻기 위해서다(다음 step들에서 경계 테스트가 이 예외를 이 함수 하나로 한정한다).

### 2. 인젝션 탐지 (`injection.py`)

- `load_rules(path=None) -> tuple[Rule, ...]`(정규식 컴파일, 잘못된 규칙은 `ValueError`로 시작 시 거부), `find_injection(text: str, *, scope: Literal["input","tool"], rules=None) -> list[InjectionMatch]`(`rule_id`·`start`·`end`: 원문 기준 오프셋).
- **우회 정규화**: 탐지 전에 NFKC 정규화(전각 → 반각, 한글 자모 → 음절 결합), 제로폭·서식 문자(`​`·`‌`·`‍`·`⁠`·`﻿`·소프트 하이픈) 제거, 대소문자 접기를 한다. **원문과 공백을 모두 제거한 형태**(한글 사이 공백 삽입 우회) 두 가지에 대해 모두 패턴을 적용하고, 매치 오프셋은 원문으로 되돌린다(`pii.py::_normalized`의 오프셋 방식을 참고). 정규식 재귀·치명적 역추적이 생기지 않게 입력 길이 상한(예: 한 번에 20,000자 청크로 나눠 탐지)을 둔다.

### 3. 판정 함수 (`screen.py`) — **역할로 한정**

- 상수: `QUARANTINE_MARKER = "[정책으로 일부 제외]"`, `INPUT_PII_KINDS = frozenset({"rrn","mobile","landline","email","card","account"})`(ADR-008 범위: 주민번호·전화·개인 이메일·계좌·카드. `biz_reg`·`passport`는 제외), `BLOCK_CODES = ("injection_direct", "pii_input")`, `TOOL_CALL_ID = re.compile(r"(?:call|toolu)_[A-Za-z0-9_-]{1,64}")`와 `valid_tool_call_id(value) -> bool`(fullmatch).
- `screen_user_messages(messages) -> InputVerdict(action: Literal["allow","block"], code: str | None)`: **`role == "user"`인 메시지만** 검사한다(`content`가 문자열이거나 `{"type":"text","text":...}` 파트 목록이면 텍스트만). 인젝션(`scope="input"` 규칙)이 있으면 `injection_direct`, `INPUT_PII_KINDS`의 PII가 있으면 `pii_input`으로 **팀과 무관하게** 차단한다. 둘 다면 `injection_direct`가 우선이다. **`system`·`assistant`·`tool` 메시지는 입력 판정에서 읽지도 않는다.** 이유: `agent/prompts.py::SYSTEM_PROMPT`의 "역할 변경 문구" 문장이 스스로 규칙에 걸리면 모든 호출이 차단된다.
- `screen_tool_messages(messages) -> ToolScreen(messages: list[dict], items: tuple[ToolItem, ...], masked: int)`: **`role == "tool"`인 메시지만** 바꾸고 나머지는 같은 객체로 그대로 돌려준다(입력 리스트를 변형하지 않는다. 새 리스트와 바뀐 메시지만 새 dict). `ToolItem(index, tool_call_id: str | None, quarantined: bool, masked: bool)`. 
  - **격리(의심 부분만)**: `scope="tool"` 규칙이 걸린 곳을 포함하는 **문장·줄 하나**(경계: `\n`, 그리고 `.`·`!`·`?`·`。` 뒤 공백·끝)를 `QUARANTINE_MARKER`로 바꾼다. 나머지 내용은 그대로다.
  - **JSON 인지**: `content`가 JSON으로 파싱되면 **문자열 값만** 검사·치환하고(키는 건드리지 않는다) 바뀐 경우에만 `json.dumps(..., ensure_ascii=False)`로 다시 직렬화한다(바뀌지 않았으면 원문 문자열 그대로). 결과는 **여전히 유효한 JSON**이어야 한다(테스트로 고정). JSON이 아니면(잘림 접미 등) 텍스트 전체에 같은 규칙을 적용한다.
  - **PII 마스킹**: 도구 결과 안의 `INPUT_PII_KINDS` PII는 `mask_pii`와 같은 `[PII:{kind}]` 표기로 가린다. **식별자 형식 값은 PII 검사를 하지 않는다**(FP-010): 문서 ID(`syn-…`·`doc-…` 형태)·티켓 키(`[A-Z]{2,5}-[0-9]+`)·hex 32/64자·UUID·ISO 8601 날짜·시각·정수 숫자 값(JSON number)·`tool_call_id`. JSON 문자열 값이 이 형식 전체와 일치하면 건너뛰고, JSON이 아닌 텍스트 모드에서는 `rrn`·`mobile`·`email`만 마스킹한다(`card`·`account`·`landline`은 JSON 값 단위에서만).
  - 격리와 마스킹이 한 메시지에 겹칠 수 있다. `ToolItem`에 둘을 따로 기록한다.
- `scan_output(text: str) -> OutputFlags(pii_kinds: tuple[str, ...], injection: bool)`: 출력은 **차단하지 않는다**. `INPUT_PII_KINDS`의 PII 종류 목록과 `scope="input"` 인젝션 표현 여부만 돌려준다(경보용, 원문·위치는 담지 않는다).
- `screen_request(messages) -> RequestScreen(verdict: InputVerdict, messages: list[dict], items: tuple[ToolItem, ...], masked: int)`: `screen_user_messages`가 차단이면 도구 메시지는 처리하지 않고 `messages`는 원본 그대로다. 아니면 `screen_tool_messages` 결과를 합친다. **멱등**이다: 이미 처리된 결과를 다시 넣으면 내용이 바뀌지 않는다(마커가 규칙에 걸리지 않아야 한다. 테스트로 고정).
- 모든 결과 객체는 **입력 원문·매치된 문자열·패턴을 담지 않는다**. 규칙 id와 개수·위치 정도만 필요하면 `injection.py` 내부에서만 쓰고 `screen.py`의 공개 결과에는 넣지 않는다.

### 4. 테스트

- 역할 한정(고정 테스트): ① `SYSTEM_PROMPT` 원문을 `system` 메시지로 넣어도 `allow`. ② 인젝션 문구가 든 `assistant` 메시지는 `allow`이고 `tool`이 아니므로 바뀌지 않음. ③ 같은 인젝션 문구가 `user`면 `block(injection_direct)`, `tool`이면 격리됨. ④ 같은 PII가 `user`면 `block(pii_input)`, `tool`이면 마스킹, `system`·`assistant`면 아무 일도 없음.
- 우회: 제로폭 삽입·전각·공백 삽입·자모 분해 변형이 걸리고, 같은 우회가 정상 문장에는 걸리지 않음(합성 문장).
- 도구 결과: 격리 뒤 JSON 유효성, 바뀌지 않은 메시지의 객체·문자열 동일성, 멱등성, 격리 단위(의심 문장 하나만 바뀜), 마스킹된 값에서 `find_pii`가 더 이상 `INPUT_PII_KINDS`를 찾지 못함.
- **FP-010 무작위 대량 테스트**(시드 고정, 건수 명시): `uuid4().hex` 10,000개·티켓 키 10,000개·13자리 epoch ms 정수 10,000개·ISO 시각 10,000개를 도구 결과 JSON 값으로 넣었을 때 **한 건도 바뀌지 않는다.**
- 복원: 규칙 하나를 지우면 해당 규칙을 기대한 테스트가 실패해야 한다(규칙 파일이 실제로 쓰인다는 증거, 가짜 PASS 방지).
- 정상 도구 결과 오탐 점검: `data/replay/a_run/*.jsonl`의 도구 결과 텍스트에 `screen_tool_messages`를 적용해 **바뀐 결과의 문서 ID가 `data/eval/meta.json`의 `injections` 문서 ID 집합 안에 있어야** 한다(테스트는 평가 데이터를 읽어도 되고 `src/`는 읽지 않는다). 이 테스트는 **재현율을 단언하지 않는다**(규칙을 그 문서에 맞춰 튜닝하지 않는다. 격리 재현율은 dev 재실행에서 측정한다). 오탐이 나오면 규칙을 일반적인 방식으로 좁히고 문서 문구를 규칙에 복사하지 않는다.
- 경계(`tests/test_guardrails_boundary.py`): `src/govops/guardrails/` 전 파일에 문자열 `redteam`·`data/eval`·`meta.json`·`data/replay`가 없다. `registry.py`는 `govops`를 import하지 않는다(AST). `guardrails/__init__.py`는 import 문이 없다. `guardrails/*.py`의 import는 표준 라이브러리와 `govops.guardrails.*`뿐이다.

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

- 규칙을 `data/eval/`·`data/replay/`·`docs/results/`·`data/redteam/`의 내용을 보고 만들거나 그 문구를 복사하지 마라(`replay` 오탐 점검 테스트만 읽기 예외). 이유: 평가·레드팀 정답지에 맞춘 규칙은 차단율을 부풀린다(FP-042·043).
- 입력 판정에서 `system`·`assistant`·`tool` 메시지를 읽지 마라. 이유: 시스템 프롬프트의 "역할 변경 문구"가 스스로 걸려 모든 호출이 막힌다. 역할 한정 테스트로 고정한다.
- 식별자·해시·ID·숫자 값에 PII 정규식을 돌리지 마라(FP-010). 이유: 무작위 hex와 epoch ms가 카드·전화 규칙에 걸려 정상 도구 결과가 무작위로 훼손된다. 무작위 대량 테스트를 지우거나 입력을 고정해 덮지 마라(FP-011).
- 판정 결과·로그·예외 메시지에 입력 원문·매치 문자열·패턴을 담지 마라. 이유: 입력 원문과 세부 판정 근거는 응답·로그·감사에 나가면 안 된다(AGENTS.md CRITICAL).
- `registry.py`에서 `govops`를 import하거나 `rules_sha256`이 규칙을 파싱하게 하지 마라. 이유: 레드팀 생성기가 규칙 내용을 보지 않고 해시만 얻는 유일한 경로여야 한다.
