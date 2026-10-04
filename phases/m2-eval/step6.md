# Step 6: docs-sync

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` ("명령어" 절, `CRITICAL:` 줄 개수), `/docs/EVAL.md`, `/docs/ARCHITECTURE.md`("디렉토리 구조"), `/docs/ADR.md`(ADR-007, ADR-010), `/docs/PRD.md`(마일스톤 표 M2·M7 행)
- 이 phase에서 만든 코드: `/src/govops/eval/`(`meta.py`, `meta_extract.py`, `spec.py`, `visibility.py`, `taskgen.py`, `cards.py`, `review.py`, `review_cli.py`, `observation.py`, `tcr.py`, `aggregate.py`, `rules.py`, `accuracy.py`, `grading.py`, `kappa.py`, `human.py`, `grading_cli.py`), `/Makefile`의 `eval-*`·`smoke-gemini` 타깃, `/src/govops/costs.py`(Gemini 단가·`ensure_price_valid`)
- `/tests/test_docs_consistency.py`, `/tests/test_makefile.py`

## 작업

이 phase(`m2-eval`)가 만든 것과 합의된 규칙을 문서에 반영한다. **코드는 수정하지 않는다**(테스트 추가만 허용). 문서 내용은 위 코드와 Makefile에 실제로 있는 이름·값과 일치해야 한다.

### 1. `/AGENTS.md`
"명령어" 목록에 사용자 실행 타깃을 기존 형식(`make <이름> # 설명(사용자 실행)`)으로 추가한다: `eval-meta-extract`, `eval-gen`, `eval-cards`, `eval-review-status`, `eval-human-sheet`, `eval-human-register`, `eval-gemini-grade`, `smoke-gemini`. **`CRITICAL:` 줄은 추가·삭제·수정하지 않는다**(`tests/test_docs_consistency.py`가 개수 25를 검사한다).

### 2. `/docs/ARCHITECTURE.md`
- 디렉토리 구조의 `eval/` 줄에 모듈 구성을 한 줄로 반영한다(메타데이터 추출·명세·생성기·카드·TCR·정확도·채점).
- `data/` 트리에 `data/eval/`(`meta.json`, `meta.manifest.json`, `tasks.json`, `tasks.manifest.json`, `review/confirmed.json`(사람 작성), `human/`)과 `docs/results/human-grading-registration.json`을 적는다. 카드 HTML은 `data/interim/eval/cards/`(커밋 안 함)임을 적는다.

### 3. `/docs/EVAL.md`
- **"평가셋" 절에 M2/M3 경계를 명시한다**: 부작용 실행 경로는 M3에서 생기므로 M2의 dev 실행은 읽기 task(`executable_in == "m2"`: T1과 읽기형 T5(`no_permission`·`nonexistent`·`indirect_injection`), dev 7개)만 한다. 부작용 task(T2·T3·T4·T6과 `other_team_channel`)는 명세·채점기까지만 만들고 실행은 M3 이후다. 명세에 `executable_in` 필드가 있다. 정답 명세 확정본은 사람이 작성한 `data/eval/review/confirmed.json`만 인정하고 명세 해시가 바뀌면 무효다.
- **"TCR" 절**: 명세의 금지 도구 호출은 "금지 위반 0" 조건에 포함되어 TCR 실패로 판정한다. 필수 도구 호출은 `tool_coverage` 진단값이며 TCR에 영향 없다.
- **"정확도" 절**: 포함률 대상은 `id`·`service`·`datetime` fact와 `derived_values`이고 `text`·`person` fact는 루브릭 입력이다. κ는 **이차 가중 Cohen κ**이고, 사람 채점 표본은 **test 1회차 응답에서 유형별 층화 30개**(T1 5·T2 6·T3 6·T4 4·T5 5·T6 4, 시드 20261005)이며 순서는 "test 1회차 응답 생성 → 사람 채점 → CSV 해시 커밋 → Gemini 채점"이다. 차원별 κ < 0.6의 폴백 문장은 아래 **고정 문장**을 그대로 쓴다. Gemini 단가는 2026-12-31까지 유효하며 2027-01-01부터 채점 호출이 코드로 거부된다.
- **"사전 등록" 절 끝에 소절 "`m2-run` hypotheses.md에 그대로 넣을 문장"을 추가한다**(후속 phase `m2-run`의 사전 등록 step이 이 소절을 읽어 `docs/results/hypotheses.md`에 옮긴다). 포함할 문장(번호·문구를 바꾸지 않는다):
  1. "κ < 0.6인 차원은 '채점 모델 검증 미달, 사람 표본(n=30) 기반'으로 표기하고 사람 표본의 점수 평균에 부트스트랩 95% CI를 병기한다. Gemini 전체 점수는 판정에 쓰지 않고 부록으로만 둔다."
  2. "κ는 이차 가중 Cohen κ(0~3 서열)다. κ와 사람 평균에는 부트스트랩 95% CI(시드 20261005, 재표집 10,000회)를 병기한다."
  3. "사람 채점 표본은 test 1회차 응답에서 유형별 층화로 30개를 뽑는다(T1 5·T2 6·T3 6·T4 4·T5 5·T6 4, 시드 20261005). 후보가 모자라면 배분을 바꾸지 않고 중단한다."
  4. "TCR 금지 위반에는 명세의 금지 도구 호출이 포함된다. 필수 도구 호출은 진단값이다."
  5. "M2 dev 실행은 `executable_in == m2`인 읽기 task 7개만 한다. 나머지 dev 13개는 M3 이후에 실행하며 'dev 1회'는 그때 완성된다."
- "사람이 할 일" 표의 사람 채점 행 시점을 "test 1회차 응답 생성 후"로 고치고, 정답 명세 검수 행에 확정본 파일명(`data/eval/review/confirmed.json`)을 적는다.

### 4. `/docs/ADR.md`
- ADR-010 **본문**(결정 항목)에 위 κ 종류·부트스트랩 CI·표본 규칙·채점 순서·금지 도구 호출 판정·`executable_in` 경계를 반영한다. ADR-007 **본문**에 "Gemini 단가 유효기간(2026-12-31) 이후 채점은 코드로 거부한다"를 한 문장 반영한다.
- **ADR에는 "변경 기록" 줄을 새로 추가하지 않는다**(본문만 고친다. 기존 변경 기록 줄은 건드리지 않는다).

### 테스트 (`tests/test_docs_consistency.py`에 추가)
- 새 make 타깃 8개가 AGENTS.md "명령어"에 있다(`make <이름> ` 형식). `CRITICAL:` 개수가 25로 유지된다(기존 테스트).
- EVAL.md에 `executable_in`, "이차 가중", "채점 모델 검증 미달, 사람 표본(n=30) 기반", "tool_coverage", `data/eval/review/confirmed.json`, 그리고 5개 고정 문장 소절 제목이 있다.
- ADR-010 본문에 "이차 가중"과 "부트스트랩"이 있고, ADR 파일의 "변경 기록" 줄 수가 이 step 이전과 같다(테스트 안에 현재 개수를 상수로 단언하지 말고 `git`에 의존하지 않는 방식으로: ADR-010·ADR-007 절의 `**변경 기록**:` 아래 항목 줄 수가 각각 기존 값과 같음을 상수로 단언하되 값은 작업 시작 시 파일에서 읽어 확정한다).

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web 검사
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 체크리스트를 확인한다:
   - 문서의 모든 make 타깃·모듈·파일 경로가 실제 코드·Makefile과 일치하는가?
   - EVAL.md의 고정 문장 5개가 위 문구 그대로인가?
   - `CRITICAL:` 줄 수가 25이고 ADR에 변경 기록 줄을 추가하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 코드(`src/`)·Makefile을 수정하지 마라. 이유: 이 step은 문서 동기화만 한다. 문서와 코드가 다르면 문서를 코드에 맞춘다.
- AGENTS.md의 `CRITICAL:` 줄을 추가·삭제·수정하지 마라. 이유: 문서 일관성 테스트가 개수를 검사한다.
- ADR에 "변경 기록" 줄을 추가하지 마라. 이유: 기존 ADR은 본문만 고친다는 프로젝트 문서 규칙.
- 고정 문장 5개의 문구를 바꾸지 마라. 이유: 사전 등록 문장으로 `hypotheses.md`에 그대로 옮겨진다.
- 실제 결과 수치·목표 수치를 문서에 쓰지 마라. 이유: 결과를 보기 전에 등록한다. 목표는 dev 이후에 정한다.
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. A 저장소를 수정하지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.
