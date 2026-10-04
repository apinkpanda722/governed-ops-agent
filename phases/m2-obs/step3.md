# Step 3: cost-report

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` (비용 상한·원장 규칙), `/docs/ADR.md` (ADR-007: 예약·정산 행, 보수적 합산), `/docs/EVAL.md` ("실행 계획표", "비용 상한"), `/docs/PRD.md` ("예산")
- `/src/govops/costs.py` (`CostLedger`, 예약 행(`kind: "reservation"`, `reservation_id`, `estimate_usd`)과 정산 행(`reservation_id`, `cost_usd`, 토큰 수), 예약 도입 전 형식의 정산 행, `main()`의 `init` 서브커맨드, 분리 원장 `costs.jsonl`·`costs_grading.jsonl`), `/tests/test_costs.py`, `/tests/test_costs_reservation.py`
- `/data/manifest/costs.jsonl`(실제 원장의 형식 확인. 이 파일은 **수정하지 않는다**), `/Makefile`, `/tests/test_makefile.py`

## 작업

호출마다 **"추정 상한 대비 실제 비용 비율"**을 원장의 예약·정산 행에서 집계해 보고하는 명령 `make cost-report`를 만든다. 비율은 B 전체 비용을 재추정하고 예약 추정식(`estimate_call_usd`)이 얼마나 보수적인지 확인하는 근거다. 읽기 전용 명령이며 API를 호출하지 않는다. TDD로 테스트를 먼저 쓴다.

### 만들 것

1. **`src/govops/costs.py`에 보고 함수와 `report` 서브커맨드를 추가한다**(기존 `init` 옆, 기존 동작 불변).
   ```python
   @dataclass(frozen=True)
   class RatioStats: n: int; mean: float | None; p50: float | None; p95: float | None; max: float | None; over_one: int
   @dataclass(frozen=True)
   class PurposeReport: purpose: str; supplier: str; settled_calls: int; ratio: RatioStats; settled_usd: float
                        legacy_rows: int; unsettled_reservations: int; tasks: int; mean_usd_per_task: float | None
   def cost_report(root: Path | None = None) -> list[PurposeReport]
   def format_report(reports, *, plan: Mapping[str, int] | None = None) -> str
   ```
   - **비율 = 정산 행의 `cost_usd` ÷ 같은 `reservation_id` 예약 행의 `estimate_usd`.** `purpose`별로 집계한다(원장 두 파일을 모두 읽는다: `agent_primary`·`agent_fallback`·`guardrail_v2`는 `costs.jsonl`, `grading`은 `costs_grading.jsonl`). 추정액이 0인 행은 비율에서 제외하고 건수를 출력한다. 백분위는 최근접 순위(nearest-rank) 방식으로 정의를 주석에 적고 테스트로 고정한다. `over_one`은 비율이 1을 넘은 호출 수(추정 상한이 뚫린 호출 — 있으면 경고 줄을 출력한다).
   - 예약 행 없이 정산 행만 있는 **예약 도입 전 형식 행**(`legacy_rows`)은 비율에서 제외하고 건수만 센다. 정산 없는 예약 행(`unsettled_reservations`)도 센다(상한 계산에서 보수적으로 세는 항목임을 출력에 적는다). 예약 없는 정산이 아닌데 `reservation_id`가 어긋난 행은 오류로 취급하지 말고 별도 건수로 센다.
   - `tasks`: 정산 행의 서로 다른 `task_id` 수, `mean_usd_per_task`: task별 정산 비용 합의 평균(`task_id`가 없는 행은 제외하고 건수 출력).
   - **재추정 구간**: `plan`(실행 계획표의 task 실행 수. 코드 상수로 고정: dev 20, test 120(= 40×3), Guardrail 끔 40, Guardrail v2 60(test 40 + dev 20), 레드팀 240 → 합계 480. 장애 주입 표본은 미정이므로 제외한다고 출력에 적는다)를 받아, `agent_primary`의 task당 평균 비용 × 계획 실행 수로 **예상 총액**을 계산하고 공급사 상한(OpenAI $9.13)과 잔여액에 대해 비율(%)로 비교한다. 데이터가 없는 purpose(`agent_fallback`·`guardrail_v2`·`grading`)는 "데이터 없음"으로 표시한다. **이 구간에는 항상 "파일럿 전 참고용 — 읽기 task 표본이며 부작용·승인·레드팀 task의 비용은 다를 수 있다"를 표시하고, 추정에 쓴 정산 호출 수와 task 수를 함께 출력한다.**
   - 출력에는 날짜·원장 경로·행 수 합계를 적고 **어떤 토큰·키·원문도 출력하지 않는다**(원장에는 원래 없다). 원장 파일이 없으면 기존 `LedgerMissingError`와 같은 방식으로 명확한 오류를 낸다.
   - 원장은 **읽기만 한다**(파일을 쓰거나 잠금 모드로 열어 변경하지 않는다. `costs.py`의 기존 읽기 방식·공유 잠금 재사용).
2. **`python -m govops.costs report [--root PATH]`** 서브커맨드, 종료 코드 0(정상), 1(원장 없음·형식 오류).
3. **`Makefile`**: `cost-report` 타깃(`uv run python -m govops.costs report`, 위에 "원장 읽기 전용, 네트워크 불필요" 주석). `verify`가 호출하지 않는다. `.PHONY`·`tests/test_makefile.py` 반영(키가 필요 없으므로 `--env-file .env`는 쓰지 않는다. 기존 검사 중 모든 user-run 타깃이 `--env-file`을 요구하지 않는지 확인하고 `cost-report`는 예외로 명시한다).

### 테스트 (`tests/test_costs_report.py`)

- 임시 원장에 예약·정산 행을 손으로 써서 비율 기대값을 손계산과 대조한다(예: 추정 0.010·실제 0.002 → 0.2, 추정 0.010·실제 0.012 → 1.2로 `over_one` 1).
- 백분위(nearest-rank) 값이 알려진 입력에서 고정된다. 비율이 하나뿐인 경우, 비어 있는 경우(`None`).
- 예약 도입 전 정산 행은 `legacy_rows`로만 세고 비율에서 제외된다. 정산 없는 예약은 `unsettled_reservations`로 센다. 추정액 0 행 제외 건수 출력.
- `grading` purpose는 분리 원장에서 읽는다. purpose별 합계가 `CostLedger.total_usd`와 같다.
- 재추정: `mean_usd_per_task × 계획 실행 수`, 상한 대비 %, "파일럿 전 참고용" 문구와 호출 수·task 수가 출력에 있다. 데이터 없는 purpose는 "데이터 없음".
- 실제 `data/manifest/costs.jsonl`(저장소에 있는 파일)을 읽는 테스트는 **복사본으로만** 수행하고(`tmp_path`로 복사) 원본 mtime·해시가 변하지 않음을 확인한다. 출력에 토큰·키처럼 보이는 문자열이 없다.
- 기존 `init` 서브커맨드·원장 읽기/쓰기 테스트가 그대로 통과한다.
- 계층 가드 통과.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 비율이 같은 `reservation_id`의 예약·정산 행에서 계산되는가? 원장을 읽기만 하는가?
   - 재추정 구간에 "파일럿 전 참고용"과 집계에 쓴 호출 수·task 수가 함께 출력되는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `data/manifest/costs.jsonl`·`costs_grading.jsonl`을 수정하거나 행을 추가하지 마라. 이유: 원장은 호출 직전·직후 `CostLedger`만 쓴다. 보고는 읽기 전용이다.
- 보고 명령에서 API·네트워크를 호출하지 마라. 이유: 비용 보고는 원장 집계만이다.
- 재추정 결과를 상한 값이나 `SUPPLIER_CAPS_USD`에 반영하지 마라. 이유: 상한은 ADR-007의 코드 설정이며 재추정은 보고용이다.
- 재추정 구간에서 "파일럿 전 참고용" 표시나 호출 수·task 수 출력을 빼지 마라. 이유: 표본이 작은 값을 확정치로 오해하지 않게 한다.
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.
