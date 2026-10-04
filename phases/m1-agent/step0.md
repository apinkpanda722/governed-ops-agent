# Step 0: costs

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (아키텍처 규칙의 비용 상한·`purpose` 항목, 고정 제약의 "실제 API 호출은 사용자가 실행")
- `/docs/ARCHITECTURE.md` ("상태 관리"의 비용 원장 항목)
- `/docs/ADR.md` (ADR-007 전체: 공급사별 상한, 호출 직전 누적액 검사, `purpose` 필드, 채점 별도 원장, ADR-016의 비용 상한 항목)
- `/docs/EVAL.md` ("비용 상한" 절)
- `/src/govops/costs.py` (현재 docstring만 있는 골격), `/tests/test_repo_rules.py`(비용 상한이 환경 변수가 아니라 코드 설정이어야 한다는 검사), `/tests/test_architecture.py`, `/Makefile`
- 참고(읽기 전용, A 저장소): `/Users/taserlee/PycharmProjects/ko-docs-agentic-rag/src/kodocs/costs.py` — A의 원장·`Price`·`BudgetGuard` 구현. 같은 방식(호출 직전 누적액 검사, append-only JSONL 원장)을 B에 맞게 만든다. **A 파일을 수정하지 마라.**

## 작업

공급사별 비용 원장과 호출 직전 누적액 검사를 `src/govops/costs.py`에 구현한다. API 호출은 하지 않는다. TDD로 테스트를 먼저 쓴다.

### 설계 (확정)

- 상한은 환경 변수가 아니라 **코드 상수**다(ADR-007): `SUPPLIER_CAPS_USD = {"openai": 9.13, "anthropic": 11.47, "gemini": 7.29}` (B 전체 기간 총액, 월 단위가 아님).
- `purpose`는 4종: `agent_primary`·`agent_fallback`·`guardrail_v2`·`grading`. 공급사 매핑: `agent_primary`→openai, `agent_fallback`→anthropic, `guardrail_v2`→anthropic, `grading`→gemini.
- 원장 파일은 **커밋 대상**(비밀 없음)이고 경로는 `data/manifest/costs.jsonl`(주 모델·fallback·v2 분류기)과 `data/manifest/costs_grading.jsonl`(채점, 별도 원장)이다. `grading`은 채점 원장에만, 나머지 purpose는 주 원장에만 쓴다.
- 원장이 사라지면 상한 검사가 무력화되므로 **원장 파일이 없으면 검사·기록 모두 실패(fail-closed)**한다. 빈 원장의 최초 생성은 명시적 init 명령으로만 한다. init은 이미 있는 파일을 **절대 덮어쓰거나 비우지 않는다**.
- 모든 유료 호출은 호출 직전에 `누적액(공급사) + 추정액 > 상한`이면 호출하지 않는다(상한과 정확히 같은 값은 허용).

### 만들 것 (`src/govops/costs.py`)

```python
SUPPLIER_CAPS_USD: Mapping[str, float]
PURPOSES: tuple[str, ...]
PURPOSE_SUPPLIER: Mapping[str, str]

@dataclass(frozen=True)
class Price:   # service, unit, unit_price_usd, source_url, checked_on, billing_margin=1.0, valid_until=None
PRICES: Mapping[str, Price]    # gpt-6-luna 토큰 단가 4종: input·cached_input·cache_write·output

class LedgerMissingError(RuntimeError): ...
class BudgetExceededError(RuntimeError): ...   # 메시지에 공급사·누적액·추정액·상한만

def token_cost_usd(model: str, *, input_tokens: int, cached_input_tokens: int = 0, output_tokens: int) -> float
def estimate_call_usd(model: str, *, input_tokens_upper: int, max_output_tokens: int) -> float   # 입력 상한 토큰 수 × input 단가 + 최대 출력 × output 단가

class CostLedger:
    def __init__(self, root: Path | None = None)      # 기본 data/manifest (저장소 루트 기준)
    def record(self, *, purpose: str, model: str, task_id: str | None, input_tokens: int, output_tokens: int, cached_input_tokens: int = 0, cost_usd: float) -> None
    def total_usd(self, supplier: str) -> float
    def check_before_call(self, *, purpose: str, estimate_usd: float) -> None   # 상한 초과 시 BudgetExceededError, 원장 없으면 LedgerMissingError
def init_ledgers(root: Path | None = None) -> list[Path]   # 없는 파일만 빈 파일로 생성, 만든 경로 반환
def main(argv: list[str] | None = None) -> int             # python -m govops.costs init
```
- `gpt-6-luna` 단가(USD/100만 토큰): input 0.10, cached_input 0.01, cache_write 0.125, output 0.50. 출처 URL `https://developers.openai.com/api/docs/pricing`, `checked_on="2026-09-30"`(A `costs.py`의 확인일을 그대로 가져온다. 이 값은 M2 파일럿 전에 재확인한다). 단가 주석에 "A costs.py 기준, 재확인 필요"를 적는다. Anthropic·Gemini 단가는 쓰는 마일스톤에서 추가하므로 **지금 만들지 않는다**(상한 상수만 둔다).
- 원장 한 줄(JSONL) = 호출 하나: `ts`(UTC ISO), `purpose`, `supplier`, `model`, `task_id`, `input_tokens`, `cached_input_tokens`, `output_tokens`, `cost_usd`. **프롬프트·응답 원문, 사용자 ID, 토큰·키를 넣지 않는다.**
- 덧붙이기는 프로세스 간 안전해야 한다(`fcntl.flock` 등 파일 잠금 + 한 번에 한 줄 쓰기 + `fsync`). 잘못된 줄(JSON 오류·필드 누락·알 수 없는 purpose)을 만나면 합계를 추측하지 않고 오류를 낸다.
- `python -m govops.costs init`은 두 원장 파일을 만들고 만든 경로를 출력한다. 이미 있으면 건드리지 않고 "이미 있음"을 출력한다.
- `Makefile`에 `costs-init` 타깃 추가: `uv run python -m govops.costs init`. (`verify`와 `.PHONY: verify`는 유지하고 `tests/test_makefile.py`가 통과해야 한다.)

### 테스트 (`tests/test_costs.py`)

모든 원장은 `tmp_path`에 만든다. 저장소의 `data/manifest/`를 쓰거나 만들지 마라.
- 상한 상수 3개가 ADR-007 값과 같고 환경 변수에서 읽지 않는다(`tests/test_repo_rules.py`가 계속 통과).
- `token_cost_usd`·`estimate_call_usd`가 단가표대로 계산한다(캐시 입력 포함).
- 상한 직전 누적에서 추정액이 상한을 넘기면 `BudgetExceededError`, **정확히 상한과 같으면 통과**, 공급사별로 독립(openai가 가득 차도 anthropic 검사는 통과).
- 원장 파일이 없으면 `check_before_call`·`record` 모두 `LedgerMissingError`이고 파일이 생기지 않는다. `init_ledgers`가 두 파일을 만들고, 두 번째 실행은 내용을 보존하며(기존 행이 있는 원장으로 확인), 하나만 있을 때 없는 것만 만든다.
- `grading`은 채점 원장에만, 나머지는 주 원장에만 기록된다. 채점 원장의 행이 openai·anthropic 누적에 섞이지 않고, gemini 누적은 채점 원장에서만 센다.
- 잘못된 줄이 있으면 합계 계산이 오류를 낸다(0으로 취급하지 않는다).
- 동시 기록: 여러 스레드(또는 프로세스)가 동시에 `record`해도 줄이 깨지지 않고 합계가 맞다.
- 원장 행에 `task_id`·토큰 수·비용 외의 필드(프롬프트·키·사용자)가 없다.
- `main(["init"])`의 출력·종료 코드, 이미 있을 때 보존.
- 계층 가드(`tests/test_architecture.py`) 통과.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 상한이 코드 상수이고 호출 직전 검사로 강제되는가? 원장이 없으면 호출이 거부되는가?
   - 채점 원장이 분리되어 있고 `purpose` 4종이 기록되는가?
   - 원장에 비밀·원문이 없는가? AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 저장소의 `data/manifest/`에 원장 파일을 만들거나 `make costs-init`을 실행하지 마라. 이유: 빈 원장 생성·커밋은 phase 완료 후 별도로 한다. 테스트는 `tmp_path`만 쓴다.
- 상한을 환경 변수나 설정 파일에서 읽지 마라. 이유: ADR-007, 비용 상한은 코드 설정이다.
- 원장이 없을 때 자동으로 만들어 계속 진행하지 마라. 이유: 원장이 사라지면 상한 검사가 무력화된다(fail-closed).
- 실제 API를 호출하지 마라. 이유: 이 step은 계산·기록 로직뿐이다.
- A의 `costs.py`를 수정하지 마라. A 저장소 어떤 파일도 수정하지 마라.
- docker, 설치·네트워크 명령을 실행하지 마라. `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.
