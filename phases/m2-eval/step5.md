# Step 5: grader-accuracy

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (고정 제약 — 특히 "실제 API 호출은 사용자가 실행", 비용 상한·원장 규칙, 계층 경계 표의 `eval` 행), `/docs/EVAL.md` ("정확도", "κ 재보정과 폴백", "비용 상한", "사람이 할 일"), `/docs/ADR.md` (ADR-007, ADR-010, ADR-013)
- 이전 step 산출물: `/src/govops/eval/spec.py`, `/src/govops/eval/observation.py`(`Observation`), `/src/govops/eval/tcr.py`, `/src/govops/eval/aggregate.py`, `/src/govops/eval/meta.py`, `/tests/test_eval_tcr.py`
- `/src/govops/costs.py` (`PRICES`, `Price`, `token_cost_usd`, `estimate_call_usd`, `CostLedger.reserve/settle/check_before_call`, `PURPOSES`, 분리 원장 `costs_grading.jsonl`), `/tests/test_costs.py`, `/tests/test_costs_reservation.py`
- `/src/govops/gateway/client.py` (비동기 호출·예약·정산·안전한 오류 처리 패턴. **`eval`은 gateway를 import하지 않는다**: 패턴만 참고), `/tests/test_architecture.py`(`eval/grading.py`에서만 `google.genai` import 허용 사례)
- 읽기 전용 참고(수정 금지): A 저장소 `~/PycharmProjects/ko-docs-agentic-rag/src/kodocs/costs.py`의 Gemini 단가 항목(`_GEMINI_PRICES`)과 `valid_until`
- 설치된 `google-genai` 패키지의 `genai.Client`·`client.models.generate_content`·`types.GenerateContentConfig`·응답의 `usage_metadata` (`.venv`의 `site-packages`에서 읽기만. 이름을 추측하지 말고 실제 시그니처를 확인한다)
- `/Makefile`, `/tests/test_makefile.py`, `/.env.example`(`GEMINI_API_KEY` 이름)

## 작업

정확도 채점 도구 일체를 만든다: 필수 fact 포함률(결정론), Gemini 루브릭 채점기(게이트웨이 미경유, 별도 원장), 가중 κ, 사람 채점 시트 생성기, 그리고 **사람 채점 CSV 해시가 커밋되기 전에는 Gemini 채점을 거부하는 게이트**. M2에서는 **절차·게이트·시트 생성기·합성 fixture 드라이런까지만** 만든다. 실제 사람 채점 30개와 Gemini 채점은 test 1회차 응답이 생긴 뒤 사용자가 한다. TDD로 테스트를 먼저 쓴다. 마지막에 이 phase의 문서를 동기화한다.

### 1. 필수 fact 포함률 — `src/govops/eval/accuracy.py`

```python
def normalize(text: str) -> str                       # NFC, 공백 접기, 대소문자 보존
def fact_inclusion(spec: TaskSpec, text: str) -> FactInclusion   # literal fact·derived_values만 대상
@dataclass(frozen=True) class FactInclusion: required: int; included: int; missing: tuple[str, ...]; rate: float | None   # 대상이 0개면 rate=None
def deliverable_text(obs: Observation) -> str | None  # 채점 대상 텍스트: obs.answer, 없으면 effect_bodies 중 첫 항목
```
- `kind in {"id","service","datetime"}`와 `derived_values`가 결정론 대상이다. 일치는 정규화 후 부분 문자열이다. `datetime`은 `2026-06-02T09:10` 같은 값에 대해 날짜(`2026-06-02`)와 시각(`09:10`)이 각각 나오거나 한국어 날짜 표기(`2026년 6월 2일`)로 나오는 변형을 허용한다(허용 범위를 코드 상수와 테스트로 명시한다). `rubric_only` fact는 포함률에 넣지 않고 루브릭 입력(아래)에만 쓴다.
- `FactInclusion.missing`에는 `fact_id`만 담는다(값 아님).

### 2. 비용 단가 확장 — `src/govops/costs.py`

- `PRICES`에 Gemini 항목을 추가한다: 서비스 이름 `google.gemini-3.8-flash.input`·`.output`(A와 같은 이름 규칙), 단가는 A `costs.py`의 값을 옮기되 `source_url`·`checked_on`은 A의 값을 그대로 쓰고 `valid_until="2026-12-31"`을 둔다. 위에 주석 한 줄: "A costs.py 기준, 재확인 필요 (M2 파일럿 전)".
- ```python
  def gemini_cost_usd(model: str, *, input_tokens: int, output_tokens: int) -> float
  ```
  `token_cost_usd`와 같은 검증(음수·비정수 거부). `valid_until`이 지난 날짜에 호출하면 `PriceExpiredError`를 올린다(시계는 주입 가능하게: 기본 오늘, 테스트에서 고정). 기존 openai 함수의 동작과 기존 테스트는 바뀌지 않는다.
- `estimate_call_usd`는 건드리지 말고, 채점기는 `gemini_cost_usd`로 직접 상한 추정값(입력 상한 토큰·출력 상한 토큰)을 계산한다.

### 3. Gemini 루브릭 채점기 — `src/govops/eval/grading.py` (**`google.genai`를 import하는 유일한 파일**)

```python
GRADER_MODEL = "gemini-3.8-flash"          # freeze 대상 상수
GRADER_REPEATS = 3
GRADER_TEMPERATURE = ...                    # 상수로 고정(값은 재량, 테스트가 값을 단언)
RUBRIC_VERSION = "v1"
DIMENSIONS = ("correctness", "groundedness", "format")   # 각 0~3 정수

class RubricScore: correctness: int; groundedness: int; format: int
class GradeCall(Protocol):                                # 테스트용 주입 지점
    async def __call__(self, prompt: str) -> tuple[RubricScore, TokenUsage]: ...
class GeminiRubricGrader:
    def __init__(self, *, ledger: CostLedger, call: GradeCall | None = None) -> None   # call이 None이면 실제 SDK 클라이언트(지연 import)
    async def grade(self, spec: TaskSpec, text: str, *, task_id: str, repeat_index: int) -> RubricScore
def build_prompt(spec: TaskSpec, text: str) -> str     # 순수 함수. 프롬프트 SHA-256을 prompt_sha256()로 노출
```
- 프롬프트 입력: 요청 문장, 채점 대상 텍스트, 필수 fact 목록(`fact_id`·`kind`·`value`, `rubric_only` 포함)과 `derived_values`, 평가 기준(정답성: 필수 사실과 일치하는가 / 근거 충실성: 근거 없는 사실을 만들지 않았는가·인용 형식 / 형식: 요청한 형태·한국어·길이) 점수표 0~3 정의. **문서 본문은 입력에 없다**(메타데이터 fact만). 출력은 JSON(`response_mime_type="application/json"`과 스키마 지정, 키는 `DIMENSIONS`와 선택적 `rationale`(200자 이하)). 파싱 실패·범위 밖 값은 `GradingError`(재시도하지 않는다. 호출 단위 실패로 기록).
- **게이트웨이를 거치지 않는다**: `govops.gateway`를 import하지 않고 SDK를 직접 쓴다. API 키는 `GEMINI_API_KEY` 환경 변수에서만 읽고 값을 로그·예외·출력에 남기지 않는다.
- **비용**: 호출 직전 `ledger.reserve(purpose="grading", model=GRADER_MODEL, task_id=..., estimate_usd=...)`로 상한 검사와 예약을 하고(상한 초과면 호출하지 않는다 — `BudgetExceededError` 전파), 호출이 끝나면 성공·실패와 관계없이 `ledger.settle(...)`한다(실패는 0 또는 확인된 사용량). 원장은 `data/manifest/costs_grading.jsonl`(Gemini 상한 $7.29)이다.
- 프롬프트에 사용자 요청·답변을 넣으므로 **채점 프롬프트·응답 원문을 로그·예외 메시지에 남기지 않는다**.
- 모델 ID·반복 횟수·온도·`RUBRIC_VERSION`·프롬프트 해시는 결과 파일에 함께 기록한다(평가 전 freeze, 바꾸면 변경 기록을 남기는 규칙의 근거).

### 4. 가중 κ와 폴백 — `src/govops/eval/kappa.py`

```python
def weighted_kappa(a: Sequence[int], b: Sequence[int], *, categories: Sequence[int] = (0,1,2,3)) -> float | None   # 이차 가중 Cohen's κ
@dataclass(frozen=True) class KappaReport: per_dimension: dict[str, float | None]; n: int; trusted: dict[str, bool]; threshold: float   # threshold = 0.6
def kappa_report(human: Mapping[str, RubricScore], gemini: Mapping[str, RubricScore]) -> KappaReport
```
- **이차 가중 Cohen κ**(0~3 서열, 가중 `(i-j)^2/(k-1)^2`)다. 한쪽 점수가 전부 같아 분산이 없어 κ를 정의할 수 없으면 `None`이고 `trusted`는 `False`(신뢰하지 않음)로 둔다.
- **사전 등록 폴백**: 차원별 κ < 0.6이면 그 차원은 `trusted=False`이며, 최종 보고에서 해당 차원은 **사람 채점한 표본 30개의 사람 점수만으로** 보고하고 Gemini 점수는 참고로만 둔다(전체 task의 Gemini 점수로 그 차원을 판정하지 않는다). `final_dimension_source(report) -> dict[str, Literal["gemini","human_sample_only"]]`로 노출한다.
- 알려진 값으로 검증한다: 완전 일치 → 1.0, 손으로 계산한 작은 사례(예: 표 하나)의 기대값, 완전 불일치 방향 → 음수, 분산 0 → `None`.

### 5. 사람 채점 시트 생성기·등록 게이트 — `src/govops/eval/human.py`

```python
HUMAN_SAMPLE_SEED = 20261005
HUMAN_SAMPLE_ALLOCATION = {"T1": 5, "T2": 6, "T3": 6, "T4": 4, "T5": 5, "T6": 4}   # 합 30. test 1회차 응답에서 유형별 층화
def sample_rule_sha256() -> str                         # 시드·배분표·선정 방식을 정규 JSON으로 만든 해시(사전 등록용)
def select_human_sample(specs: Sequence[TaskSpec], observations: Sequence[Observation]) -> list[tuple[TaskSpec, Observation]]
def write_human_sheet(selection, csv_path: Path, manifest_path: Path) -> None
def register_human_grading(csv_path: Path, registration_path: Path) -> HumanRegistration
def require_registered(registration_path: Path, *, repo_root: Path) -> HumanRegistration   # 게이트. 실패 시 HumanGateError
def load_human_scores(csv_path: Path) -> dict[str, RubricScore]
```
- **표본 선정**: 대상은 `split == "test"`이고 `run_index == 0`(1회차)이며 `deliverable_text`가 비어 있지 않은 관측이다. 유형별로 `random.Random(HUMAN_SAMPLE_SEED)`(유형 이름을 시드에 섞어 유형별 독립 스트림)로 task ID를 정렬한 뒤 배분 개수만큼 뽑는다. **후보가 배분보다 적으면 조용히 줄이거나 다른 유형으로 채우지 말고** 원인을 담은 오류로 실패한다.
- **시트(CSV)**: 열은 `sample_id, task_id, type, request, deliverable, correctness, groundedness, format, note`(점수 열은 빈칸). **Gemini 점수·모델 이름·자동 판정 열을 두지 않는다**(블라인드). 행 순서는 유형 순서가 아니라 시드로 섞는다. 함께 `human_sample.json`(task ID 목록, 배분표, 시드, `sample_rule_sha256`)을 쓴다. 기본 경로는 `data/eval/human/`(CSV)이다. 토큰·세션 정보는 시트에 없다.
- **등록**: `register_human_grading`은 CSV가 정확히 30행이고 모든 점수 칸이 0~3 정수이며 Gemini 관련 열이 없음을 검증한 뒤, `docs/results/human-grading-registration.json`(`csv_path`, `csv_sha256`, `n`, `registered_at`, `sample_rule_sha256`)을 쓴다. 사용자가 이 파일과 CSV를 커밋한다.
- **게이트 `require_registered`**: (a) 등록 파일 존재, (b) 등록 파일과 CSV가 **git에 커밋되어 있고 작업 트리에서 수정되지 않음**(`git ls-files --error-unmatch`와 `git status --porcelain -- <경로>`가 비어 있음), (c) CSV의 현재 SHA-256이 등록된 값과 같음, (d) CSV가 여전히 검증을 통과함. 하나라도 어긋나면 `HumanGateError`(어느 조건인지만 담고 CSV 내용은 담지 않는다).

### 6. CLI·Makefile — `src/govops/eval/grading_cli.py`

- `python -m govops.eval.grading_cli human-sheet --observations PATH [--out data/eval/human]` — 시트 생성(Gemini와 무관).
- `python -m govops.eval.grading_cli register-human --csv PATH` — 등록 파일 생성.
- `python -m govops.eval.grading_cli grade --observations PATH --out DIR` — **가장 먼저 `require_registered`를 호출하고, 실패하면 Gemini 클라이언트도 원장도 만들지 않은 채 종료 코드 2로 끝난다.** 통과하면 `deliverable_text`가 있는 모든 test 관측을 `GRADER_REPEATS`회씩 채점하고(차원별 3회의 **중앙값**을 그 관측의 Gemini 점수로 쓴다), 결과 JSONL(관측별 3회 점수·중앙값·프롬프트 해시·모델·`RUBRIC_VERSION`), `kappa.json`(`KappaReport`, `final_dimension_source`)을 쓴다. 호출 직전 상한 검사 실패(`BudgetExceededError`) 시 그 지점까지의 결과를 남기고 비정상 종료한다.
- `python -m govops.eval.grading_cli smoke` — **실행 전에 고정한 기대값**을 코드 상수로 둔다: Gemini 1회 호출(합성 고정 입력), 응답이 JSON으로 파싱되어 세 점수가 0~3 정수, `costs_grading.jsonl`에 정산 행이 정확히 1개 늘고 그 `cost_usd`가 0.01 USD 미만, 사용 모델 ID가 `GRADER_MODEL`. 각 항목을 `PASS`/`FAIL`로 출력하고 하나라도 FAIL이면 종료 코드 1. 이 스모크는 게이트와 무관하며 결과를 평가 산출물로 저장하지 않는다.
- Makefile 타깃(모두 `uv run --env-file .env python -m govops.eval.grading_cli ...`, 위에 "사용자 실행" 주석, `verify` 미호출): `eval-human-sheet`(`OBS=` 변수), `eval-human-register`(`CSV=` 변수), `eval-gemini-grade`(`OBS=` 변수), `smoke-gemini`. `.PHONY`·`tests/test_makefile.py` 반영(`live` 타깃은 `--env-file .env`를 쓴다는 기존 검사와 맞춘다).

### 7. 문서 동기화

- `/AGENTS.md`의 "명령어" 목록에 이 phase의 사용자 실행 타깃을 추가한다: `make eval-meta-extract`, `make eval-gen`, `make eval-cards`, `make eval-review-status`, `make eval-human-sheet`, `make eval-human-register`, `make eval-gemini-grade`, `make smoke-gemini`(각각 한 줄 설명, 기존 형식과 같게 `make <이름> # 설명(사용자 실행)`). **`CRITICAL:` 줄은 추가·삭제하지 않는다**(`tests/test_docs_consistency.py`가 개수 25를 검사한다).
- `/docs/ARCHITECTURE.md`: `eval/` 줄과 `data/` 트리에 `data/eval/`(`meta.json`·`tasks.json`·`review/confirmed.json`·`human/`)과 모듈 구성을 한 줄씩 반영하고, `docs/results/human-grading-registration.json`을 적는다.
- `/docs/EVAL.md`:
  - "평가셋" 절에 **M2/M3 경계**를 적는다: 부작용 실행 경로는 M3에서 생기므로 M2의 dev 실행은 읽기 task(`executable_in == "m2"`: T1 + 읽기형 T5, dev 7개)만 한다. 부작용 task(T2·T3·T4·T6과 `other_team_channel`)는 명세·채점기까지만 만들고 실행은 M3 이후다. 명세에 `executable_in` 필드가 있다.
  - "정확도" 절: κ는 **이차 가중 Cohen κ**이고, 사람 채점 표본은 **test 1회차 응답에서 유형별 층화 30개**(T1 5·T2 6·T3 6·T4 4·T5 5·T6 4, 시드 20261005)이며, 순서는 "test 1회차 응답 생성 → 사람 채점 → CSV 해시 커밋 → Gemini 채점"이다. κ < 0.6인 차원은 사람 채점 표본 30개의 사람 점수만으로 보고한다.
  - "사람이 할 일" 표의 사람 채점 행 시점을 위 순서에 맞게 고친다.
- `/docs/ADR.md`의 ADR-010 **본문**(결정 항목)에 위 κ 종류·표본 규칙·채점 순서를 반영한다. **ADR에는 "변경 기록" 줄을 새로 추가하지 않는다**(본문만 고친다).
- `tests/test_docs_consistency.py`에 위 새 make 타깃이 AGENTS.md 명령어에 있고 EVAL.md에 `executable_in`과 "이차 가중"이 있는지 검사를 추가한다.

### 테스트 (`tests/test_eval_accuracy.py`, `tests/test_eval_kappa.py`, `tests/test_eval_human.py`, `tests/test_eval_grading.py`, `tests/test_costs.py` 확장)

- 포함률: 정규화·부분 일치, `datetime` 변형, `derived_values`, `rubric_only` 제외, 대상 0개면 `rate is None`, `missing`에 값이 없다.
- Gemini 단가: 합계 계산(손계산 기대값), 음수·비정수 거부, `valid_until` 이후 호출 거부(시계 주입), 기존 openai 계산 불변.
- 채점기(가짜 `call` 주입): 프롬프트에 본문 센티널이 없고 fact·요청이 들어 있음, 프롬프트 해시 결정론, 정상 점수 파싱, 범위 밖·형식 오류 → `GradingError`, 호출 전 예약·호출 후 정산 행(성공·실패 모두), 상한 초과 시 `call`이 호출되지 않음, 오류·로그에 프롬프트·답변·키가 없음, `eval/grading.py` 외 파일에서 `google.genai`를 import하지 않음(계층 가드).
- κ: 알려진 값, `None` 처리, 폴백 판정(κ 0.59 → 사람 표본만, 0.6 → gemini).
- 사람 시트: 배분(30개, 유형별 개수), 시드 결정론(같은 입력 → 같은 선정), 후보 부족 → 명시적 오류, 시트에 Gemini 관련 열 없음, 행 순서가 유형순이 아님, `sample_rule_sha256` 안정성.
- **드라이런(합성 fixture, 네트워크 없음)**: `tmp_path`에 `git init`(사용자 이름·이메일을 로컬 설정) → 합성 관측 생성 → 시트 생성 → 테스트가 점수를 채운 CSV 작성 → `register_human_grading` → **커밋 전에는 `grade`가 코드 2로 거부되고 Gemini 호출·원장 접근이 0회** → 커밋 후 가짜 Gemini로 `grade` 성공(κ 보고서 생성) → CSV를 한 글자 바꾸면 다시 거부 → 등록 파일만 수정해도 거부. 상한을 낮춘 임시 원장으로 `BudgetExceededError`에서 안전하게 멈추는 경로도 확인한다.
- `smoke`는 가짜 `call`·임시 원장으로 PASS 경로와 각 FAIL 경로(범위 밖 점수, 비용 초과, 정산 행 없음)를 확인한다.
- 계층 가드 통과, `make verify`는 네트워크·키 없이 통과.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 채점이 게이트웨이를 거치지 않고 별도 원장(`purpose="grading"`)에 호출 직전 상한 검사·예약·정산으로 기록되는가?
   - 사람 CSV 해시 커밋 전에는 Gemini 클라이언트가 만들어지지도 않는가?
   - 테스트·`make verify`가 키·네트워크 없이 통과하는가? `google.genai`가 `eval/grading.py`에만 있는가?
   - AGENTS.md `CRITICAL:` 줄 수(25)가 그대로인가? ADR에 변경 기록 줄을 추가하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단(예: `google-genai`가 설치되어 있지 않아 SDK 시그니처를 확인할 수 없음 — 사용자가 `uv add google-genai` 후 재시작)

## 금지사항

- `make smoke-gemini`·`make eval-gemini-grade`를 실행하거나 실제 Gemini API를 호출하지 마라. 이유: 실제 API 호출은 사용자가 실행한다. 테스트는 가짜 `call`만 쓴다.
- `govops.gateway`를 import하거나 채점 호출을 게이트웨이로 보내지 마라. 이유: Guardrail이 채점 입력을 바꾸면 평가가 왜곡된다(ADR-007).
- 사람 채점 CSV에 Gemini 점수·모델 판정을 넣거나 사람 채점 전에 Gemini 채점을 돌릴 수 있는 우회 경로(게이트 우회 플래그, 환경 변수)를 만들지 마라. 이유: κ 절차는 사람이 먼저 채점하고 해시를 커밋한 뒤에만 Gemini를 돌린다.
- 후보가 부족할 때 표본 배분을 임의로 바꾸지 마라. 이유: 층화 규칙은 사전 등록 대상이다.
- 채점 프롬프트·답변 원문·API 키를 로그·예외·출력에 남기지 마라. 이유: 토큰·키 비노출 규칙, 평가 데이터 노출 방지.
- `uv add`·`pip install`로 패키지를 설치하지 마라. 이유: 설치는 사용자가 한다(이미 설치되어 있어야 한다).
- AGENTS.md의 `CRITICAL:` 줄을 추가·삭제하지 마라. ADR에 변경 기록 줄을 추가하지 마라. 이유: 문서 일관성 테스트와 프로젝트 문서 규칙.
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.
