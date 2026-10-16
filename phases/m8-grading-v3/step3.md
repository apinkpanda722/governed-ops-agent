# Step 3: injection-metrics

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`(ADR-015·019·026), `/docs/EVAL.md`
- `/docs/M8_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m8-grader.md`, `/docs/results/hypotheses-m8-citation.md`, `/docs/results/hypotheses-m8-hallucination.md`(사전 등록, 읽기만 한다), `/docs/HUMAN_GRADING_GUIDE_M8.md`
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/src/govops/grounding/grader.py`, `/src/govops/grounding/deliverable.py`, `/src/govops/grounding/evidence.py`(이전 step 산출물)
- `/src/govops/grounding/sentences.py`(이전 step 산출물), `/src/govops/eval/kappa.py`(`weighted_kappa`, `bootstrap_ci`), `/tests/test_eval_kappa.py`
- `/docs/results/hypotheses-m8-grader.md` 9·10절(합성 주입 규칙, 합격선)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/grounding/inject.py`, `src/govops/grounding/metrics.py`, `tests/test_grounding_inject.py`, `tests/test_grounding_metrics.py`. TDD(테스트를 먼저 쓰고 통과하는 구현을 쓴다).

열람 금지 경로: `data/interim/`, `data/eval/`, `data/redteam/`
수정 허용 경로: `src/govops/grounding/inject.py`, `src/govops/grounding/metrics.py`, `tests/test_grounding_inject.py`, `tests/test_grounding_metrics.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 해당 경로의 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드가 런타임에 그 경로를 다루는 것(예: 기본 출력 경로 `data/interim/m8-grounding/`, 평가 명세를 읽는 `load_specs`)은 대상이 아니다. 테스트가 `data/interim/`·`data/eval/`·`data/redteam/`의 실제 파일 **내용**을 읽는 것은 여전히 금지이며 테스트는 임시 디렉터리·합성 명세·합성 관측만 쓴다. 예외로 `data/results/`의 실행 디렉터리는 파일 이름과 JSON 키 이름을 확인하는 데 한해 열람할 수 있고, 응답 본문(`answer`·`effect_bodies`)이나 근거 본문을 출력·인용하지 않는다.

**배경**: 합성 환각 주입과 검증 지표 계산을 순수 함수로 만든다(LLM·DB 접근 없음). 정답은 구성에 의해 보장하고(hypotheses-m8-grader §9), 합격선 판정은 §10을 그대로 구현한다. 이 step의 함수는 채점기를 직접 부르지 않고 채점기 결과 값(`GradeResult`)을 받는다.

**배경**: 합성 환각 주입과 검증 지표 계산을 순수 함수로 만든다(LLM·DB 접근 없음). 일치 단위는 결정론 문장이다. 정답은 구성에 의해 보장하고(hypotheses-m8-grader §9), 합격선 판정은 §10을 그대로 구현한다. 이 step의 함수는 채점기를 직접 부르지 않고 채점기 결과(`RowAggregate`)를 받는다.

### 1. `inject.py` (문장 목록 위에서 주입)

```python
INJECTION_KINDS = ("fact", "wrong_support", "fabricated_id")
@dataclass(frozen=True)
class InjectedRow: row_key: str; kind: str; sentences: tuple[Sentence, ...]; target_sentence_id: str; target_doc_id: str | None
@dataclass(frozen=True)
class InjectionSkip: row_key: str; reason: str          # 예: no_candidate, no_key_string, split_count_changed
def assign_kinds(row_keys: Sequence[str], seed: int) -> dict[str, str]: ...
def inject(row_key, kind, sentences: Sequence[Sentence], evidence: Sequence[EvidenceItem], observed_ids: Sequence[str], seed: int) -> InjectedRow | InjectionSkip: ...
def injection_detected(injected: InjectedRow, agg: RowAggregate) -> bool: ...
```

- `assign_kinds`: 행 키를 정렬 → `random.Random(seed)`로 셔플 → 유형을 라운드로빈 배정(결정론).
- `fact`: 값 풀(합성 서비스명·날짜·수치 템플릿, **실존 기관·사람 이름 금지**)에서 시드로 고른 값으로 한국어 사실 문장 하나를 만들어 문장 목록 **끝에 새 번호**(`s{N+1}`)로 추가한다. 값 문자열이 근거 본문 전체에 **부분 문자열로도 없음**을 검사하고 있으면 다른 값을 고르며, 후보가 소진되면 `InjectionSkip("no_candidate")`. 새 문장은 `split_sentences`로 다시 나눴을 때 정확히 한 문장이어야 한다.
- `fabricated_id`: 인용이 있는 문장의 인용 하나를 `observed_ids`와 근거 본문에 없는 합성 문서 ID(`syn-<유형>-<번호>` 형식)로 바꾼다. 같은 번호를 유지하며, 교체한 문장 텍스트를 `build_sentences`로 다시 만들었을 때 문장 수와 번호가 그대로여야 한다(다르면 `InjectionSkip("split_count_changed")`). 인용이 없으면 skip.
- `wrong_support`: 인용이 붙은 문장에서 "핵심 문자열"(숫자·ID를 포함한 길이 4 이상 토큰 중 가장 긴 것)을 고르고, 같은 행의 **다른 관측 ID** 중 그 ID의 근거 본문에 핵심 문자열이 없는 것으로 인용을 바꾼다(같은 번호 유지). 핵심 문자열이 없거나 후보가 없으면 skip.
- `injection_detected`(**부분 문자열 매칭을 쓰지 않는다**): `fact`는 `agg.positive[target_sentence_id]`가 참이면 탐지, 번호가 없거나 `not_claim`/`supported`면 미탐지. `fabricated_id`·`wrong_support`는 `agg.citation_supports[(target_sentence_id, target_doc_id)]`가 `False`이면 탐지(키가 없으면 미탐지).

### 2. `metrics.py`

```python
MIN_POS_SENTENCES = 10; MIN_POS_ROWS = 5; KAPPA_MIN = 0.6; DETECTION_MIN = 0.90; FPR_MAX = 0.10
@dataclass(frozen=True)
class RowLabels: row_key: str; human: dict[str, str]; model: dict[str, str]; model_positive: dict[str, bool]   # sentence_id → 라벨
def sentence_kappa(rows: Sequence[RowLabels]) -> float | None: ...
def cluster_bootstrap_ci(rows: Sequence[RowLabels], *, seed=20261005, resamples=10000) -> tuple[float, float, int] | None: ...   # (하한, 상한, 건너뛴 퇴화 표본 수). 모든 재표집이 퇴화하면 None → 판정은 등록된 "판정 불가(κ 퇴화)"
def confusion_matrix(rows) -> dict[str, dict[str, int]]: ...        # 4×4, 행=사람 라벨, 열=채점기 라벨
def precision_recall(rows) -> dict: ...                               # 양성 기준, 사람 기준
def row_level_kappa(rows) -> float | None: ...                        # 보조 지표
def detection_and_fpr(injected: Sequence[tuple[InjectedRow, RowAggregate]], clean: Sequence[RowLabels]) -> dict: ...
def validation_verdict(*, rows, detection_rate, false_positive_rate) -> dict: ...
```

- `sentence_kappa`: 양성 = `unsupported`·`contradicted`, 음성 = `supported`·`not_claim`. **모든 행의 모든 문장**을 모아 `eval.kappa.weighted_kappa`(0/1 범주, 이진에서 Cohen κ와 같음)로 계산한다. 사람 문장 번호 집합과 채점기 번호 집합이 다르면 `ValueError`. 한쪽 라벨이 전부 같으면 `None`.
- `cluster_bootstrap_ci`: **행을 복원추출**(행 안의 문장은 함께 이동)해 κ를 다시 계산하는 95% 백분위 CI. 기본 시드 20261005, 10,000회, 퇴화 표본은 건너뛰고 그 횟수를 반환의 세 번째 값으로 돌려준다. 모든 재표집이 퇴화하면 `None`을 돌려주고, 이 경우 `validation_verdict`는 등록된 `"판정 불가(κ 퇴화)"`로 이어진다.
- `validation_verdict`는 hypotheses-m8-grader §10을 그대로 구현한다. 사람 양성 문장이 `MIN_POS_SENTENCES` 미만이거나 양성 문장이 있는 행이 `MIN_POS_ROWS` 미만이면 `verdict="판정 불가(양성 부족)"`, κ가 `None`이면 `"판정 불가(κ 퇴화)"`. 아니면 κ 점추정 ≥ 0.6·탐지율 ≥ 0.90·오탐률 ≤ 0.10을 **모두** 만족해야 `"채점기 v3 검증 통과"`, 아니면 `"채점기 v3 검증 미달"`. 반환에는 모든 값(κ, CI, 혼동 행렬, 정밀도·재현율, 일치율, 양성/음성 문장 수, 양성 행 수, 행 단위 κ, 탐지율, 오탐률, 분모, 각 기준의 met 여부)을 담는다. 기준 값은 모듈 상수에 두고 호출자가 바꿀 수 없다.
- `detection_and_fpr`: 탐지율 = `injection_detected` 참 / 주입 복사본 수(skip 건수 별도). **오탐률(문장 단위)** = 주입하지 않은 정상 행에서 사람 라벨이 `supported`인 문장 중 채점기가 양성으로 판정한 비율. 행 단위 오탐률(양성 문장이 없는 정상 행 중 채점기가 양성 문장을 낸 비율)을 보조로 함께 돌려준다. 분모 0이면 `None`과 사유 코드(빈 집합 공허 통과 금지, FP-069).

### 3. 테스트 (합성 데이터)

- `tests/test_grounding_inject.py`: 같은 시드 같은 결과, `assign_kinds` 균등 배정(유형별 개수 차이 ≤ 1), 세 유형 각각 **정답 보장**(주입 값이 근거 전체에 없음, 대체 ID가 관측 집합에 없음, 핵심 문자열이 대체 근거에 없음)을 시드 고정 무작위 500행(건수 명시)으로 검증, `fact`의 새 번호가 `s{N+1}`이고 기존 번호 불변, 인용 교체 후 문장 수·번호 불변과 `split_count_changed` skip, 후보 소진 → skip 사유, 인용 없는 행 skip, `injection_detected` 경계(대상 번호 누락·`not_claim`·`supported`는 미탐지, 부분 문자열이 같아도 번호가 다르면 미탐지).
- `tests/test_grounding_metrics.py`: `sentence_kappa` 손계산 값(완전 일치 1.0, 완전 불일치, 우연 수준), 한쪽 라벨이 전부 같으면 `None`, 번호 집합 불일치 → `ValueError`, 클러스터 부트스트랩이 같은 시드에서 결정론이고 반환이 `(하한, 상한, 건너뛴 횟수)` 세 값이며 모든 재표집이 퇴화하면 `None`이고 행 단위로 재표집함(문장 단위 재표집과 결과가 다른 합성 사례), 혼동 행렬 합 = 문장 수, 정밀도·재현율 손계산, 판정 경계 모든 조합(κ 0.599/0.6, 탐지율 0.899/0.90, 오탐률 0.10/0.101, 양성 문장 9/10, 양성 행 4/5)에서 `verdict` 문자열이 정확히 맞음, **각 기준이 따로 실패하는 케이스**(한 기준만 만족해 통과하지 않음, FP-021), `판정 불가` 두 종류, 분모 0 사유 코드.

**사용자 실행**: 없음.

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

- 합격선·판정 불가 값(`0.6`·`0.90`·`0.10`·`10`·`5`)을 함수 인자나 환경 변수로 바꿀 수 있게 하지 마라. 이유: 데이터를 보기 전에 등록한 값이며 결과를 보고 바꾸면 사전 등록이 아니다(FP-043).
- 탐지 판정에 부분 문자열 매칭을 쓰지 마라. 이유: 주입 문장은 새 번호를 받고 탐지는 그 번호의 판정으로만 본다(hypotheses-m8-grader §9).
- 주입 값을 근거 본문과 대조하는 검사 없이 문장을 삽입하지 마라. 이유: 우연히 근거에 있는 문장은 정답이 틀려 탐지율이 왜곡된다.
- 실존 기관·사람 이름이나 실제 규정 문구를 주입 값 풀에 넣지 마라. 이유: 합성 기록만 쓴다(AGENTS.md CRITICAL).
- 빈 집합에 `all(...)`·`== 0`으로 통과를 판정하지 마라. 이유: 검사 대상이 없을 때 공허하게 통과한다(FP-069). `eval/kappa.py`는 수정하지 말고 호출만 한다(동결).

## 소비자 점검(시뮬레이션 통과)

신규 파일만 더하므로 기존 단언을 깨는 소비자는 찾지 못했다. `tests/test_eval_kappa.py`는 `eval/kappa.py`를 수정하지 않아 불변이다.
