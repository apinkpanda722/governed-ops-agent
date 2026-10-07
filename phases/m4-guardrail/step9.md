# Step 9: redteam-review-cards

## 읽어야 할 파일

- `/AGENTS.md`(고정 제약: 사람이 내려받은 확정 파일만 인정), `/docs/ADR.md`(ADR-010의 정답 명세 확정 절차), `/docs/EVAL.md`
- 선례(구조를 따른다): `/src/govops/eval/cards.py`, `/src/govops/eval/cards_assets.py`, `/src/govops/eval/cards_content.py`, `/src/govops/eval/cards_text.py`, `/src/govops/eval/review.py`·`review_cli.py`(`confirmed.json` 검증·명세 해시 무효화), `/Makefile`의 `eval-cards`·`eval-review-status` 타깃과 주석, `/tests/test_eval_cards.py`·`/tests/test_eval_review.py`
- 이전 step 산출물(읽기 전용): `/data/redteam/set.json`, `/data/redteam/manifest.json`, `/data/redteam/overlay/jira_tickets.json`, `/docs/results/hypotheses-m4-redteam.md`(검수 규칙 6항·판정 규칙), `/src/govops/eval/redteam.py`
- `/.claude/skills/failure-patterns/SKILL.md`(FP-042·043) — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/eval/redteam_cards.py`(신규), `Makefile`(`redteam-cards`·`redteam-review-status` 타깃 추가만), `tests/test_eval_redteam_cards.py`(신규), `tests/test_makefile.py`(타깃 확인 추가에 한함), `tests/test_redteam_boundary.py`(**`test_actual_source_and_manifest_boundary`의 파일 집합 단언 갱신에 한함**: 이 테스트는 `src/govops/eval/redteam*.py`가 정확히 2개(`len(files) == 2`)라고 단언한다. 이 step이 `redteam_cards.py`를 추가하므로 개수 단언을 **파일 이름 집합 단언**(`{"redteam.py", "redteam_templates.py", "redteam_cards.py"}`와 정확히 같음)으로 바꾼다. **새 파일에도 기존 검사를 그대로 적용한다**: `rules_v1` 문자열 없음, `govops.*` import는 `govops.guardrails.registry`(`rules_sha256`만)뿐. 개수를 `>= 2`나 범위로 느슨하게 하지 않는다. `redteam_cards.py`는 `set.json`·`manifest.json`을 **파일로 읽고** `govops`를 import하지 않는다(`eval.redteam`도 import하지 않는다)), `tests/test_repo_rules.py`(`test_git_ignore_rules` 표에 `data/interim/redteam_review/cards.html`이 무시됨(코드 0)을 확인하는 행 추가에 한함, 단언 약화 금지). 계층: `eval/`은 `gateway`·`broker`를 import할 수 없고, `eval/redteam*.py`는 `govops.guardrails.registry` 외의 `guardrails` 모듈을 import하지 않는다(경계 테스트가 이미 있다). `data/redteam/set.json`·`manifest.json`·`overlay/`는 읽기만 한다. TDD.

**목표**: 사람 1인이 레드팀 120건을 **어떻게 검수하는지 설명을 읽고, 칸마다 기준을 보면서, 예시를 참고해 판정하고, 결과를 기록하는 방법까지** 한 장의 HTML에서 따라 할 수 있게 한다. `eval-cards`(평가 정답 명세 검수)와 같은 방식이다.

### 1. 카드 생성 (`eval/redteam_cards.py`, `make redteam-cards`)

CLI `python -m govops.eval.redteam_cards [--out PATH]`(기본 출력 `data/interim/redteam_review/cards.html`, gitignore 대상인 `data/interim/` 아래. 오프라인·결정론, 키·네트워크 불필요). 출력은 외부 자산 없이 열리는 **HTML 한 장**이다(스크립트·스타일 인라인, 폰트는 시스템 폰트). 구성:

1. **머리말: 이 검수가 무엇인지**(평문 한국어, 전문 용어 풀이): 레드팀 120건이 무엇인지, 왜 사람이 보는지(문장이 정말 해당 공격이고 합성 데이터 규칙을 지켰는지 확인), 이 검수가 **Guardrail의 성능을 정하는 것이 아니라 세트의 타당성만 확인**한다는 점, 판정은 이 파일의 내려받기 결과로만 인정된다는 점. 4범주(직접 인젝션·간접 인젝션·신원 사칭·PII 유출 유도)와 4방어 층(입력 차단·도구 결과 격리·발송 전 검사·승인)을 짧게 설명한다.
2. **칸별 판정 기준 표**(카드마다 같은 칸): ① `타당성`(이 항목이 해당 범주의 공격이 맞는가: 적합 / 부적합) — 기준과 예시를 표로, ② `합성 규칙`(실존 기관·사람 이름·실제 규정 원문·실존할 수 있는 PII가 없는가: 위반 없음 / 위반 있음) — 위반 예시, ③ `기대 방어 층`(표시된 층이 이 항목에 합당한가: 합당 / 다른 층이 더 합당 — 그 층을 고름), ④ `메모`(선택, 자유 입력, 문장을 옮기지 말고 사유만). 카드의 최종 판정은 세 칸에서 자동 계산한다: 모두 통과면 `승인`, 하나라도 문제면 `제외`(제외 사유는 어느 칸인지 기록).
3. **예시 절**: 범주마다 "적합한 항목 / 부적합한 항목" 예를 **이 세트의 항목이 아니라 카드 생성기가 가진 고정 예시**(합성 문장)로 2개씩 보여 준다(세트 항목을 예시로 쓰면 편향을 만든다).
4. **기록 방법 절**: 판정 방법(칸을 고르면 자동 저장되지 않으며 마지막에 **내려받기** 버튼으로 `confirmed.json`을 내려받는다), 내려받은 파일을 `data/redteam/review/confirmed.json`에 두는 방법, 중간 저장(브라우저 저장소를 쓰지 않는다: 같은 페이지에서 `내려받기`를 여러 번 눌러도 된다, 이어서 하려면 내려받은 파일을 페이지의 `불러오기`로 읽는다), 검수 뒤 `make redteam-review-status`로 확인하는 방법. 화면에 진행률(판정 완료 n/120)을 보여 준다.
5. **카드 120장**: 항목 id·범주·변형·전달 경로·기대 방어 층, **공격 문장**(`user_input`이면 요청 문장, 간접이면 요청 문장과 오버레이 티켓의 본문 또는 코멘트 전체)을 그대로 보여 준다. 공격 문장은 `<pre>` 안에서 **HTML 이스케이프**한다(문장이 화면에서 실행·해석되지 않는다). 카드에는 Guardrail 판정·규칙·평가 정답을 넣지 않는다.
- `confirmed.json` 형식: `{"set_sha256": <manifest의 set_sha256>, "reviewer": "<자유 입력 라벨>", "items": {"RT-D-01": {"validity": "ok|bad", "synthetic": "ok|violation", "layer": "ok|<다른 층 이름>", "memo": "...", "verdict": "approved|excluded"}, ...}}`. 내려받기는 120건이 모두 판정됐을 때만 `완료`로 표시하고 미완료면 미완료 표시를 포함해 내려받을 수 있다.

### 2. 확정 검증 (`make redteam-review-status`)

`python -m govops.eval.redteam_cards --status [--confirmed PATH]`(기본 `data/redteam/review/confirmed.json`): 파일이 없으면 `미검수`, `set_sha256`이 현재 `data/redteam/manifest.json`의 값과 다르면 `무효(세트가 바뀜)`, 120건 중 판정이 빠진 항목이 있으면 `미완료(n/120)`, 모두 있으면 `완료: 승인 a건·제외 b건`과 제외 항목 id 목록을 출력한다. **판정은 사람이 내려받은 파일만 인정한다**(카드 생성기나 이 도구가 `confirmed.json`을 쓰지 않는다 — 테스트로 고정). 종료 코드는 `완료`일 때만 0이다.

### 3. 테스트

- 카드: 120장·범주별 장수(30·40·20·30)·모든 항목 id 포함, HTML 한 장(외부 URL·`<script src>`·`<link>` 없음), 공격 문장이 이스케이프됨(`<script>` 문자열이 든 합성 항목으로 확인), Guardrail 판정·규칙·`rules_sha256` 관련 문자열이 없음, 같은 입력은 같은 출력(결정론), 머리말·칸 기준·예시·기록 방법 절이 모두 있음, 예시는 세트 항목과 겹치지 않음.
- 확정 검증: 파일 없음·해시 불일치·미완료·완료 각 출력과 종료 코드, 카드 CLI가 `confirmed.json`을 만들거나 고치지 않음(작업 트리 비교), 잘못된 형식(알 수 없는 값)은 거부.
- 실제 `data/redteam/set.json`으로 카드 생성이 오류 없이 되는 스모크 테스트(오프라인).

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

- `data/redteam/review/confirmed.json`을 코드·테스트가 만들거나 고치지 마라. 이유: 검수 판정은 사람이 내려받은 파일만 인정한다(AGENTS.md CRITICAL, 평가 정답 명세 확정과 같은 규칙). 테스트는 임시 경로의 가짜 확정 파일만 쓴다.
- 카드에 Guardrail 규칙·판정·`rules_sha256`·평가 정답을 넣지 마라(FP-042). 이유: 검수자가 규칙에 맞춰 항목을 판단하면 세트의 타당성 검수가 오염된다.
- 공격 문장을 이스케이프 없이 HTML에 넣지 마라. 이유: 카드를 여는 브라우저가 문장 안의 마크업을 해석하면 검수 화면이 오염되고 보안 위험이다. 외부 URL·스크립트 로딩도 금지한다.
- 세트의 실제 항목을 예시로 쓰지 마라. 이유: 예시가 판정을 유도해 편향을 만든다. 예시는 생성기에 고정된 합성 문장이다.
- `set.json`·`manifest.json`·오버레이·사전 등록 문서를 수정하지 마라(읽기 전용). 이유: 검수 대상이 바뀌면 `set_sha256` 결합이 무효가 된다(FP-043).
