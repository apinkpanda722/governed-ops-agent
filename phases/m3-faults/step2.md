# Step 2: chaos-a

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md`(ADR-018·019·020·021), `/docs/EVAL.md`, `/docs/THREAT_MODEL.md`("F1~F11" 표), `/docs/results/hypotheses.md`, `/docs/results/hypotheses-m3.md`, `/docs/results/2026-10-06-m3-dev2-live.md`, `/.claude/skills/failure-patterns/SKILL.md`
- `/src/govops/replay/fake_a.py`·`/src/govops/replay/a_replay.py`(가짜 A, **수정하지 않는다**), `/src/govops/replay/tokens.py`(합성 토큰), `/src/govops/tools/a_mcp.py`(이전 step 결과), `/src/govops/tools/resilience.py`, `/src/govops/eval/`(필수 fact 값·`required_facts` 구조: `spec`), `/tests/test_architecture.py`(`replay/` 경계: `agent`·`api`·`broker` import 금지), `/tests/conftest.py`
- **외부 계약 실측**: 이전 step(`a-client-status`)의 실측표(연결 거부·500·503·429·리셋·지연의 `kind`·`classify`). **원시 TCP 서버만으로 5xx·429(`Retry-After`)·리셋·지연·연결 거부를 모두 만들 수 있었다**(MCP 프로토콜 구현 불필요). 가짜 A에는 장애 주입 지점이 없다.

## 작업

**배경**: F11(A MCP 장애)는 A를 고칠 수 없어 실제 A에 주입할 수 없다. 장애를 주입하는 별도 **chaos A 서버**를 `replay/`에 둔다. 가짜 A는 건드리지 않는다. TDD.

### 1. `src/govops/replay/chaos_a.py`

- 원시 asyncio TCP 서버. 모드(`ChaosMode`): `refuse`(서버를 띄우지 않고 **닫힌 포트 주소**를 돌려준다), `http_500`, `http_503`, `http_429`(`Retry-After` 값 설정 가능), `reset`(요청을 읽은 뒤 연결 리셋), `delay`(지연 값 설정 가능; **Hard 타임아웃 10초 초과 값**을 쓸 수 있어야 한다). 컨텍스트 매니저로 `url`을 돌려준다(`127.0.0.1` 임의 포트, 루프백에만 바인드).
- **요청 헤더를 기록·출력·저장하지 않는다**(`Authorization` 포함). 서버가 받은 요청 수(건수)만 센다. 로그·예외 메시지에도 헤더를 넣지 않는다.
- F11 클라이언트는 **합성 토큰**(`replay/tokens.py`의 방식)만 쓴다. 이 모듈은 `KODOCS_MCP_TOKENS_FILE`·`broker`를 import·참조하지 않는다.

### 2. F11 판정 함수 (`replay/chaos_a.py` 또는 같은 패키지의 순수 함수 모듈)

`judge_f11(spec, observation) -> F11Verdict`

- 통과 조건 둘 다: ① **부작용 0건**(관측된 효과가 0개), ② **필수 fact 값 중 요청 문장에 이미 있는 값을 뺀 값이 최종 답변에 0건**(A 장애 중에는 모든 근거가 A 문서이므로 모델이 근거 없이 말하면 값이 답변에 나타난다). 요청 문장에 있는 값은 사용자가 이미 안 값이라 판정에서 뺀다(F11 판정 한계: 요청 문장에 근거 값이 거의 없는 task는 ②가 약해진다는 점을 docstring과 테스트 이름에 적는다).
- 판정 결과에는 두 조건을 따로 담는다(`side_effects`, `leaked_facts` 목록). 한 불리언만 돌려주지 않는다(FP-021).
- 환각은 모델이 확률적으로 만드는 결과다. 판정은 환각이 **있으면 F11 실패로 센다**. 규칙 위반으로 중단하지 않는다.

### 3. 테스트 (로컬 루프백, 합성 토큰)

- 모드별로 `AMcpClient`가 돌려주는 `kind`·`classify`가 이전 step 실측표와 같다(`refuse`→`CONNECT`, `http_500`/`http_503`→`UNCERTAIN`, `http_429`→`RATE_LIMITED` + `Retry-After` 보존, `reset`→`UNCERTAIN`, `delay`>timeout→`UNCERTAIN`). 읽기 재시도·서킷 열림 뒤에는 서버 요청 수가 늘지 않는다.
- **자격 증명 규칙**: ① chaos 서버를 쓰는 F11 경로(클라이언트 조립 포함)가 `KODOCS_MCP_TOKENS_FILE` 환경 변수를 읽지 않는다(환경에 일부러 설정해 두고 읽기 시도가 없음을 단언), ② 요청에 실린 합성 토큰이 서버의 로그·출력·반환값·예외 어디에도 나타나지 않는다(캡처한 출력·`caplog`에 토큰 문자열 부재).
- `judge_f11`: 부작용 1건이면 실패, 요청 문장에 있는 값은 답변에 있어도 통과, 요청에 없는 필수 fact 값이 답변에 있으면 실패와 그 값 목록, 둘 다 없으면 통과.
- 계층 가드(`tests/test_architecture.py`)가 계속 통과한다(`replay/`는 `agent`·`api`·`broker` import 금지).

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

- `replay/fake_a.py`·`replay/a_replay.py`(가짜 A)를 수정하지 마라. 이유: 기록 재생 CI의 기반이다. 장애 주입 지점이 필요하면 chaos 서버를 쓴다.
- F11 경로(chaos A 서버·클라이언트·하네스)에서 `KODOCS_MCP_TOKENS_FILE`과 실제 A 사용자 토큰을 읽거나 보내지 마라. 합성 토큰만 쓴다. chaos 서버는 요청 헤더(특히 `Authorization`)를 기록·출력·저장하지 않는다. 이유: 장애를 주입하는 서버는 외부 프로세스 역할이고 실제 자격 증명이 닿으면 안 된다(AGENTS.md CRITICAL: 토큰을 로그·trace에 남기지 않는다). 테스트로 강제한다.
- F11 판정에서 요청 문장에 이미 있는 값을 "누출"로 세지 마라. 이유: 사용자가 이미 아는 값이라 근거 없는 답변의 증거가 아니다. 반대로 요청에 없는 값은 하나라도 나타나면 실패로 센다.
- 주입한 장애가 시험하려는 경로를 타지 않는 시나리오를 만들지 마라(FP-005). 이유: 장애가 방어 로직을 실행시키지 않으면 중복 0이 아무 일도 안 일어나서 저절로 나온다. 시나리오마다 그 경로가 실제로 실행됐다는 증거(조회 호출 수 > 0, 대기 발생, 재시도 횟수)를 단언한다. F10은 항상 F5와 결합한다.
- 외부 서비스(A MCP·LiteLLM·mock)의 응답 형태를 추측으로 만들어 그 추측만 검증하지 마라(FP-020). 이유: 틀린 추측은 틀린 테스트로 통과한다. 이 step이 의존하는 실측 결과(읽기 목록의 외부 계약 실측)를 픽스처로 쓰고, 실측 값이 없는 항목은 단정하지 말고 `blocked`로 보고한다.
- 한 지표만으로 PASS를 판정하지 마라(FP-021). 이유: 검사 조건이 보수 규칙 때문에 우연히 만족되면 가짜 PASS다. 중복 0은 누락·최종 상태·POST 수와 짝으로 본다.
- git 이력·시간·I/O 지연에 의존하는 테스트를 로컬 통과만으로 끝내지 마라(FP-032). 이유: CI(얕은 복제·느린 디스크)에서 달라진다. 가상 시간 폴링은 `real_tick`처럼 실제로 조금 기다리고, 느린 I/O를 주입해 재현한 뒤 고친다.
- 0건 지표를 "아무것도 안 하기"로 통과시키지 마라(FP-040). 이유: 중복 0은 부작용이 실제로 시도·확정됐을 때만 의미가 있다. 누락·최종 상태·POST 수를 짝으로 단언한다.
- 이 step이 의존하는 외부 지점을 실측 없이 가정하지 마라(FP-050). 이유: 승인 뒤 live에서 결함이 드러나 수정이 늘었다. 읽기 목록의 실측 근거를 따르고 다른 동작을 관측하면 가정하지 말고 보고한다.
- 이 파일의 지시와 AGENTS.md 계층 경계가 충돌하거나 이 step의 작업·금지사항이 서로 모순되면 임의로 판단하지 말고 `blocked`로 보고하라(FP-054). 이유: 모순을 임의로 해소하면 계층 경계가 깨진다.
- 이 step이 필요로 해서 테스트를 바꿀 때는 변경 목록(파일·테스트 이름·이유)을 최종 보고에 적어라(FP-056). 이유: 완화된 단언이 다음 step에서 방치된다. 삭제는 그 테스트가 검증하던 대상 자체가 이 step에서 제거된 경우만 허용한다.
- `.claude/`(skills·commands)와 `docs/results/`의 사전 등록 파일(`hypotheses.md`·`hypotheses-m3.md`·`hypotheses-m3-grading.md`·`hypotheses-m3-prompt.md`·`n-registration-m3.md`·`m3-dev-supersedes.json`·`m3-dev2-supersedes.json`·`2026-10-06-m3-dev-live.md`·`2026-10-06-m3-dev2-live.md` 등)은 읽기만 하고 수정하지 않는다(이 step이 명시적으로 허용한 경우 제외). 이유: 읽기 목록의 파일을 지시 없이 고치지 않게 한다(FP-055).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라(읽기 전용으로 참고하는 것만 허용).
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트나 코드 실행을 하지 마라(로컬 루프백 서버와 합성 토큰은 허용). 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- `data/results/`의 실행 결과 디렉토리와 `data/replay/llm/`·`data/replay/a_run/`·`data/replay/golden/`의 기록 파일을 수정하지 마라(읽기만). 이유: 실행 기록과 골든 기록은 증거이며 live 러너가 만든다.
- 기존 테스트를 약화하지 마라(단언 삭제·`require_all=False`·슬라이스 비교·허용 오차 확대). 이름·시그니처 변경 때문에 필요한 호출·기대값 수정만 허용한다(FP-056). 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.
