# Step 6: smoke-faults

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md`(ADR-018·019·020·021), `/docs/EVAL.md`, `/docs/THREAT_MODEL.md`("F1~F11" 표), `/docs/results/hypotheses.md`, `/docs/results/hypotheses-m3.md`, `/docs/results/2026-10-06-m3-dev2-live.md`, `/.claude/skills/failure-patterns/SKILL.md`
- `/src/govops/evalrun/mock_admin.py`(`faults_set`), `/src/govops/evalrun/faults.py`(preflight·시나리오·`MockAdmin` 사용), `/src/govops/gateway/smoke.py`(`smoke-fallback` 출력 형식: 응답 모델·원장만 출력, **소요 시간 없음**), `/src/govops/tools/smoke.py`·`/src/govops/api/smoke.py`(스모크 패턴), `/Makefile`(`smoke-effects`·`smoke-fallback`·`eval-faults`), `/tests/test_smoke_gateway.py`·`/tests/test_smoke_effects.py`(스모크 테스트 패턴), `/docs/results/hypotheses-m3-faults.md`(이전 step)
- **외부 계약 실측**: LiteLLM `down_alias`(닫힌 포트 배포 `agent-primary-down` + fallbacks) 호출은 `smoke-fallback`에서 PASS(`model_id=anthropic-claude-sonnet-5-5`, `attempted_fallbacks=1`, 0.024센트)이나 **소요 시간은 미실측**이다. 닫힌 포트라 주 모델 단계는 즉시 거절될 것으로 보이나 가정이다. mock 규칙은 `docker compose exec`로 거는데 사용자 환경의 실제 mock 컨테이너 상태는 코드 테스트로 알 수 없다.

## 작업

**수정할 파일**(이 목록이 전부다): 신규 `src/govops/evalrun/faults_smoke.py`, 신규 테스트 `tests/test_evalrun_faults_smoke.py`, 수정 `Makefile`(`smoke-faults` 타깃 추가만). 계층: `evalrun`은 `broker`·`mocks`·`audit`·`guardrails`·LLM SDK를 import할 수 없다(`tools`·`gateway`·`replay`는 가능). mock 토큰은 기존 `tools/smoke.py`와 같은 방식(환경 변수·기존 토큰 로더)으로 읽고 값은 출력하지 않는다.

**배경**: 사용자가 실행하는 `make smoke-faults`를 만든다. 154회 live 실행 전에 **실제 mock 컨테이너·LiteLLM에서 장애 부품이 작동하는지**와 `down_alias` 지연을 측정한다. 코드 테스트는 가짜 실행기로만 한다. TDD.

### `make smoke-faults` (사용자 실행, `govops.evalrun.faults_smoke` 같은 모듈)

단계마다 `INFO`/`PASS`/`FAIL` 한 줄을 출력한다(비밀 값은 출력하지 않는다). 순서:

1. **환경 점검**: preflight 재사용(`stale_mock_image`·`litellm_config_stale`·`service_down:<이름>`), 양쪽 mock의 **남은 장애 규칙**(`faults stats`가 비어 있지 않으면 `stale_faults` FAIL).
2. **규칙 걸기·풀기**: 살아 있는 mock에 `docker compose exec`로 `faults_set`(stdin)을 보내고 `faults stats`에서 규칙이 보이는지 확인한 뒤 `faults clear`로 풀고 비어 있는지 확인한다.
3. **F5 + F10 짧은 지연 합성 요청 1회**: mock-slack(또는 mock-jira)에 합성 요청 한 번을 보내 처리 후 응답 유실 + 짧은 반영 지연(1.0초)을 걸고, 클라이언트 재시도 정책이 **표식 조회 > 0, 재발송 0, 최종 효과 1건**으로 끝나는지 확인한다(합성 메시지·합성 ID만 사용, 실존 이름 금지). 끝에서 mock을 reset한다.
4. **`down_alias` 호출 소요 시간**: 게이트웨이 클라이언트로 아주 짧은 합성 프롬프트 1회를 `down_alias` 경로로 호출해 **응답 모델 ID·`attempted_fallbacks`·소요 시간(초)을 `INFO`로 출력**한다. 원장에는 공급사별 예약·정산이 기록된다(호출 전 양쪽 공급사 상한 검사, 상한 초과면 호출하지 않고 FAIL).
5. **마무리 안내**: 출력 마지막에 "비용 원장(`data/manifest/costs.jsonl`)이 변경되었으면 `chore:` 커밋으로 먼저 올린 뒤 `make eval-faults`를 실행하세요(FP-031)"와 실행 명령(`caffeinate -i make eval-faults`, 약 75~80분, `down_alias` 소요 시간 측정값으로 소요 예상을 다시 계산)을 출력한다.

`Makefile`에 `smoke-faults` 타깃을 추가한다(주석: "live LiteLLM·mock·docker compose 필요, 실제 호출 1회, 사용자 실행"). `eval-run-dev`·`eval-faults` 타깃과 `make verify`는 바꾸지 않는다.

### 테스트 (가짜 실행기·가짜 gateway만)

- 각 단계의 PASS/FAIL 분기(남은 규칙, 규칙이 stats에 없음, 표식 조회 0, 재발송 > 0, 상한 초과 시 호출 없음).
- 출력에 토큰·키·요청 헤더·응답 본문이 없다(캡처한 출력 검사).
- `down_alias` 소요 시간 출력 형식(`INFO duration_s=…`)과 마무리 안내 문구(원장 커밋·`caffeinate -i make eval-faults`)가 출력된다.

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

## 금지사항

- 위 "수정할 파일" 목록 밖의 파일을 수정하지 마라. 목록 밖 수정이 필요해 보이면 임의로 고치지 말고 `blocked`로 보고한다(FP-054). 이유: step 범위 누락은 멈춰서 설계에서 고친다.
- 스모크를 하네스 step의 테스트나 `make verify`에서 실행하지 마라. 이유: 실제 mock·LiteLLM·Docker 호출은 사용자가 실행한다. 코드 테스트는 가짜 실행기로만 한다.
- 스모크가 실존 이름·실제 메시지 본문을 만들지 마라. 합성 ID·합성 문구만 쓴다(AGENTS.md CRITICAL).
- 주입한 장애가 시험하려는 경로를 타지 않는 시나리오를 만들지 마라(FP-005). 이유: 장애가 방어 로직을 실행시키지 않으면 중복 0이 아무 일도 안 일어나서 저절로 나온다. 시나리오마다 그 경로가 실제로 실행됐다는 증거(조회 호출 수 > 0, 대기 발생, 재시도 횟수)를 단언한다. F10은 항상 F5와 결합한다.
- 외부 서비스(A MCP·LiteLLM·mock)의 응답 형태를 추측으로 만들어 그 추측만 검증하지 마라(FP-020). 이유: 틀린 추측은 틀린 테스트로 통과한다. 이 step이 의존하는 실측 결과(읽기 목록의 외부 계약 실측)를 픽스처로 쓰고, 실측 값이 없는 항목은 단정하지 말고 `blocked`로 보고한다.
- 기본값이 있는 환경 변수를 `os.environ.get(이름, 기본값)`으로 읽지 마라(FP-030). 이유: 빈 값이 기본값을 덮는다. `govops.env.env_or`를 쓴다.
- 실행 산출물(비용 원장 등 커밋 대상)을 테스트나 코드 경로에서 갱신하지 마라(FP-031). 이유: 작업 트리가 더러워지면 `execute.py`와 live 러너 가드가 시작하지 않는다. 가드 메시지는 더럽힌 파일을 보여 준다.
- git 이력·시간·I/O 지연에 의존하는 테스트를 로컬 통과만으로 끝내지 마라(FP-032). 이유: CI(얕은 복제·느린 디스크)에서 달라진다. 가상 시간 폴링은 `real_tick`처럼 실제로 조금 기다리고, 느린 I/O를 주입해 재현한 뒤 고친다.
- 이 step이 의존하는 외부 지점을 실측 없이 가정하지 마라(FP-050). 이유: 승인 뒤 live에서 결함이 드러나 수정이 늘었다. 읽기 목록의 실측 근거를 따르고 다른 동작을 관측하면 가정하지 말고 보고한다.
- 첫 live 실행에서 터질 것(낡은 mock 이미지·남은 장애 규칙·꺼진 서비스·LiteLLM 설정)을 가드 없이 두지 마라(FP-051). 이유: 실행 환경이 낡으면 결과가 가짜가 된다. 러너는 이유 코드를 출력하고 시작하지 않는다.
- 이 파일의 지시와 AGENTS.md 계층 경계가 충돌하거나 이 step의 작업·금지사항이 서로 모순되면 임의로 판단하지 말고 `blocked`로 보고하라(FP-054). 이유: 모순을 임의로 해소하면 계층 경계가 깨진다.
- 이 step이 필요로 해서 테스트를 바꿀 때는 변경 목록(파일·테스트 이름·이유)을 최종 보고에 적어라(FP-056). 이유: 완화된 단언이 다음 step에서 방치된다. 삭제는 그 테스트가 검증하던 대상 자체가 이 step에서 제거된 경우만 허용한다.
- `.claude/`(skills·commands)와 `docs/results/`의 사전 등록 파일(`hypotheses.md`·`hypotheses-m3.md`·`hypotheses-m3-grading.md`·`hypotheses-m3-prompt.md`·`n-registration-m3.md`·`m3-dev-supersedes.json`·`m3-dev2-supersedes.json`·`2026-10-06-m3-dev-live.md`·`2026-10-06-m3-dev2-live.md` 등)은 읽기만 하고 수정하지 않는다(이 step이 명시적으로 허용한 경우 제외). 이유: 읽기 목록의 파일을 지시 없이 고치지 않게 한다(FP-055).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라(읽기 전용으로 참고하는 것만 허용).
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트나 코드 실행을 하지 마라(로컬 루프백 서버와 합성 토큰은 허용). 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- `data/results/`의 실행 결과 디렉토리와 `data/replay/llm/`·`data/replay/a_run/`·`data/replay/golden/`의 기록 파일을 수정하지 마라(읽기만). 이유: 실행 기록과 골든 기록은 증거이며 live 러너가 만든다.
- 기존 테스트를 약화하지 마라(단언 삭제·`require_all=False`·슬라이스 비교·허용 오차 확대). 이름·시그니처 변경 때문에 필요한 호출·기대값 수정만 허용한다(FP-056). 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.
