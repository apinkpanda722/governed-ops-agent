# Step 1: evidence-loader

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`(ADR-015·019·026), `/docs/EVAL.md`
- `/docs/M8_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m8-grader.md`, `/docs/results/hypotheses-m8-citation.md`, `/docs/results/hypotheses-m8-hallucination.md`(사전 등록, 읽기만 한다), `/docs/HUMAN_GRADING_GUIDE_M8.md`
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/grounding/probe.py`, `/src/govops/grounding/db.py`(이전 step 산출물: `RowLink`, `link_results`, `open_readonly_saver`), `/tests/test_grounding_probe.py`, `/tests/test_grounding_db.py`
- `/src/govops/eval/observation.py`, `/src/govops/eval/spec.py`(`TaskSpec.request`), `/src/govops/eval/accuracy.py`(`deliverable_text`를 **쓰지 않는다**, 비교용으로만 읽는다)
- `/src/govops/agent/graph.py`(`TOOL_RESULT_MAX_CHARS`, tool 메시지 구성, 읽기만 한다)
- `/data/replay/a_run/T1-01.jsonl`의 **키 이름과 구조만**(합성 문서, 본문은 인용하지 않는다)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/grounding/sentences.py`, `src/govops/grounding/deliverable.py`, `src/govops/grounding/evidence.py`, `tests/test_grounding_sentences.py`, `tests/test_grounding_deliverable.py`, `tests/test_grounding_evidence.py`. TDD(테스트를 먼저 쓰고 통과하는 구현을 쓴다).

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`
수정 허용 경로: `src/govops/grounding/sentences.py`, `src/govops/grounding/deliverable.py`, `src/govops/grounding/evidence.py`, `tests/test_grounding_sentences.py`, `tests/test_grounding_deliverable.py`, `tests/test_grounding_evidence.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 출력 경로 `data/interim/m8-grounding/`, 평가 명세를 읽는 `load_specs`)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·합성 명세·합성 관측만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다.

**배경**: 채점기에 줄 근거 본문과 채점 대상 문서를 만든다. 근거 출처는 checkpointer의 tool 메시지 하나뿐이고(hypotheses-m8-grader §3), 채점 대상은 발송 본문 우선이다(§4). 인용은 `cited_doc_ids`가 아니라 문서 문자열에서 추출한다(hypotheses-m8-citation §1).

**배경**: 채점기에 줄 근거 본문과 채점 대상 문서를 만든다. 일치 단위는 **결정론 문장**이다(hypotheses-m8-grader §4): 채점 대상 문서를 규칙 `sent-v1`으로 번호 붙은 문장 목록으로 만들고, 채점기와 사람이 같은 번호마다 라벨을 단다. 근거 출처는 checkpointer의 tool 메시지 하나뿐이고(§3), 채점 대상은 발송 본문 우선이다(§4). 인용은 `cited_doc_ids`가 아니라 문서 문자열에서 추출한다(hypotheses-m8-citation §1).

### 1. `src/govops/grounding/sentences.py` (LLM·DB 접근 없는 순수 함수)

```python
SPLITTER_RULES_VERSION = "sent-v1"
@dataclass(frozen=True)
class Sentence:
    sentence_id: str            # "s1", "s2", … 등장 순서
    text: str
    citations: tuple[str, ...]  # 이 문장에 나온 인용 ID(등장 순서, 중복 제거)
def split_sentences(text: str) -> list[str]: ...
def build_sentences(text: str) -> list[Sentence]: ...
def splitter_rules_sha256() -> str: ...        # 이 모듈 소스 파일 바이트의 sha256
```

분할 규칙(등록 문서 §4의 S1~S5를 그대로 구현한다. 규칙을 임의로 바꾸지 않는다):

- S1: `\r\n`·`\r` → `\n`, 줄바꿈마다 분리, 빈 줄 버림.
- S2: 줄 맨 앞의 글머리표(`-` `*` `•` `·` `▪` `‣`), 번호(`1.` `1)` `(1)`, 1~2자리), 제목 표지(`#`~`######`)와 뒤 공백을 한 번 제거. 표지만 있고 내용이 없는 줄은 버림.
- S3: `.`·`?`·`!`·`。` 뒤에 공백이 오거나 줄 끝이면 경계. 닫는 따옴표·괄호(`"` `'` `”` `’` `)` `」` `』` `]`)는 부호에 붙여 앞 문장에 둠. 마침표 앞뒤가 모두 숫자(`3.5`)이거나 마침표 바로 뒤가 공백이 아닌 문자(`a.b`, `v1.2`)이면 경계 아님.
- S4: 한글 종결 어미 `다`·`요`·`죠`·`까`로 끝나는 어절 바로 뒤에 공백이 있고, 다음 어절이 한글·영문 대문자·숫자·`[`·`(`로 시작하며 접속 조각(`고` `라고` `하고` `며` `면서` `는데` `지만` `니까` `서`)이 아니면 부호가 없어도 경계.
- S5: 앞뒤 공백 제거, 연속 공백 하나로, 비면 버림. 번호는 1부터 `s1`….
- `Sentence.citations`는 `extract_citations`(아래)로 문장 텍스트에서 뽑는다. `splitter_rules_sha256`은 파일 내용이 바뀌면 값이 바뀌어야 한다(등록 후 변경 금지의 기계 확인용).

### 2. `src/govops/grounding/deliverable.py` (순수 함수)

```python
def select_deliverable(obs: Observation) -> tuple[str | None, Literal["effect_body", "answer", "empty"]]: ...
def extract_citations(text: str) -> list[str]: ...          # 정규화·중복 제거, 등장 순서
def fabricated_citations(citations, observed_ids, request_text) -> list[str]: ...
```

- `select_deliverable`: `effect_bodies[0]`가 공백 아닌 내용이면 `("…", "effect_body")`, 아니면 `answer`가 공백 아닌 내용이면 `answer`, 둘 다 비면 `(None, "empty")`. **`eval.accuracy.deliverable_text`를 import·호출하지 않는다**(답변 우선 순서가 반대다).
- `extract_citations` 정규식(hypotheses-m8-citation §2에서 고정): 문서 `syn-[a-z]+-\d{3}`, 티켓 `\b[A-Z]{2,5}-\d{4}\b`, 장애 번호 `INC-\d{4}-\d{3}`. 앞뒤가 영숫자인 경우(`xsyn-incident-004`, `syn-incident-0044`)는 인용이 아니다. 식별자 문자열에 PII 정규식을 돌리지 않는다(FP-010).
- `fabricated_citations`: `observed_ids`에 없고 요청 문장 안의 ID 집합에도 없는 인용만 돌려준다.

### 3. `src/govops/grounding/evidence.py`

```python
@dataclass(frozen=True)
class EvidenceItem:
    tool_name: str | None      # 직전 assistant tool_calls에서 찾은 이름, 없으면 None
    content: str               # 이미 절단된 본문 그대로(재절단 금지)
    chars: int

@dataclass(frozen=True)
class Evidence:
    status: Literal["ok", "evidence_missing", "evidence_oversize"]
    items: tuple[EvidenceItem, ...]
    total_chars: int
    truncated_items: int       # 본문이 `…(잘림)`으로 끝나는 수

async def load_evidence(saver, link: RowLink, *, max_chars: int) -> Evidence: ...
```

- thread별 `aget_tuple`(`asyncio.wait_for` 30초) → `channel_values["messages"]`에서 `role=="tool"`이고 본문이 공백 아닌 메시지를 순서대로 모은다. dict와 LangChain 메시지 객체 두 형태를 모두 처리한다. 여러 `thread_ids` 후보가 있으면 tool 메시지가 있는 첫 thread를 쓰고 어느 thread인지는 값 대신 개수로만 남긴다.
- **`saver`는 호출자가 주입한다. 이 모듈은 DB에 직접 연결하지 않고 `open_checkpointer`를 import하지 않는다**(호출자는 `db.open_readonly_saver`만 쓴다).
- thread 없음·tool 메시지 0개·연결 없음 → `evidence_missing`. `total_chars > max_chars` → `evidence_oversize`(items는 비워 둔다). 본문을 **절단하거나 요약하지 않는다**. `max_chars`는 인자로 받는다(값은 사전 등록 이후 `m8-grader-registration.json`에서 확정되어 CLI가 넘긴다).

### 4. 테스트 (합성 데이터만)

- `tests/test_grounding_sentences.py`: 규칙별 케이스 — 줄바꿈·빈 줄, 글머리표(`-`·`*`·`•`)·번호(`1.`·`2)`·`(3)`)·제목 표지 제거와 표지만 있는 줄 버림, `.`·`?`·`!`·`。` 뒤 공백 경계와 줄 끝 경계, 닫는 따옴표·괄호가 앞 문장에 붙음, `3.5`·`v1.2`·`a.b`·`syn-incident-004.` 문장 끝(뒤에 공백/줄 끝)의 올바른 처리, 한국어 종결 `…했다 다음 …`(S4 분할)과 접속 조각 `…했다 고 …`(비분할), 연속 공백 정리, 번호 `s1…` 순서, 빈 입력 → 빈 목록, 같은 입력 같은 출력(결정론), 시드 고정 무작위 문자열 5,000개(건수 명시)를 넣어도 예외 없이 번호가 연속임, `splitter_rules_sha256`이 64 hex이고 같은 소스에서 안정적임, `Sentence.citations` 추출.
- `tests/test_grounding_deliverable.py`: 발송 본문과 완료 안내문이 둘 다 있으면 발송 본문 선택, 효과가 없으면 답변, 둘 다 비면 `empty`, `deliverable_text`와 반대 우선순위임을 보이는 케이스. 인용 추출 경계(`syn-incident-004a`·`syn-incident-0044`는 비추출, 한글 조사 붙은 `syn-incident-004를`은 추출, 소문자 티켓 키 비추출), 중복 제거·순서, 요청 문장 ID 제외, 시드 고정 무작위 10,000개 hex/숫자 문자열을 넣어도 예외·PII 오탐이 없음(FP-010·011, 건수 명시).
- `tests/test_grounding_evidence.py`: `InMemorySaver`에 합성 체크포인트를 넣어 tool 메시지 수집 순서·`tool_name` 매칭, 이미 절단된 본문을 그대로 두는지, `…(잘림)` 개수, 두 메시지 형태, thread 없음/tool 0개 → `evidence_missing`, 상한 초과 → `evidence_oversize`, 상한 경계(같으면 `ok`), 잠금 계약 가짜 saver에서 `wait_for` 안에 끝남(FP-068), 출력·예외 메시지에 합성 본문이 없음, 모듈이 `open_checkpointer`를 import하지 않음.

**사용자 실행**: 없음(이 step의 산출물은 라이브러리다).

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
   - 동결 경로(그래프·프롬프트·도구·Guardrail·평가 러너·골든·감사 쓰기·mock·시드·`costs.py`·`infra/litellm/config.yaml`)를 바꾸지 않았는가?
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

- `eval.accuracy.deliverable_text`를 호출하지 마라. 이유: 답변 우선이라 승인 발송 task에서 완료 안내문이 채점 입력이 된다(이슈 #160, hypotheses-m8-grader §4).
- 분할 규칙 S1~S5를 임의로 바꾸거나 모델·LLM으로 문장을 나누지 마라. 이유: 문장 번호는 사람 라벨과 채점기 출력을 맞추는 결정론 키이며 등록 이후 바뀌면 비교가 무효다(hypotheses-m8-grader §4).
- tool 메시지 본문을 다시 자르거나 요약하지 마라. 이유: 모델이 본 내용을 그대로 근거로 쓰는 것이 이 측정의 전제다(hypotheses-m8-grader §3).
- `cited_doc_ids`를 읽거나 쓰지 마라. 이유: 정의상 관측 ID의 부분집합이라 항상 참이다(`agent/graph.py:518`, hypotheses-m8-citation §1).
- ID·해시 문자열에 개인정보 정규식을 돌리지 마라. 이유: 무작위 hex가 전화번호 규칙에 걸려 정상 값이 거부된다(FP-010).
- `open_checkpointer`를 import하거나 `saver.alist` 순회 안에서 같은 saver를 부르지 마라. 이유: 읽기 전용 연결만 쓴다는 계약과 교착 방지(FP-068).

## 소비자 점검(시뮬레이션 통과)

신규 파일만 더하므로 기존 단언을 깨는 소비자는 찾지 못했다. `tests/test_architecture.py`가 `grounding`의 import를 검사하므로 `gateway`·`broker`·`tools`·`api`·`mocks`·`evalrun` import가 없어야 한다. `govops.eval.accuracy`는 읽기용 import도 하지 않는다(위 금지).
