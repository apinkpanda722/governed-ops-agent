# 프로젝트: governed-ops-agent

> 이 파일이 에이전트 규칙의 단일 원본이다. Codex는 AGENTS.md를, Claude Code는 이 파일을 import하는 CLAUDE.md를 읽는다.

가상 회사 "해온클라우드"의 운영 업무(티켓 조회·요약 → 코멘트·메시지·후속 티켓)를 사용자 신원으로 수행하는 LangGraph 단일 Tool-use Agent와, 그 앞의 거버넌스 게이트웨이(LiteLLM + Guardrail + 감사 로그). 프로젝트 A(`ko-docs-agentic-rag`)의 MCP 서버를 클라이언트로 쓴다. 목표·범위는 `docs/PRD.md`, 구조는 `docs/ARCHITECTURE.md`, 결정 근거는 `docs/ADR.md`, 지표는 `docs/EVAL.md`, 위협·완화·검증은 `docs/THREAT_MODEL.md`.

## 기술 스택
- Python 3.12(`pyproject.toml`의 최소 버전, `.python-version`의 실행 버전), uv 기반. 에이전트 서비스는 호스트에서 `uv run`으로 실행한다(ADR-004).
- 에이전트: LangGraph 단일 에이전트, `interrupt()` + Postgres checkpointer, `Command(resume=...)` 재개
- API: FastAPI (`POST /tasks`, `GET /tasks/{id}`, `POST /tasks/{id}/approval`, 진행 SSE)
- 도구 연결: MCP 클라이언트(streamable HTTP, A MCP live / 기록 재생용 가짜 A), mock-jira·mock-slack(FastAPI REST, compose)
- 게이트웨이: LiteLLM proxy(팀별 가상 키, `async_pre_call_hook`·`async_post_call_success_hook` Guardrail)
- LLM: 주 모델 OpenAI `gpt-6-luna`, fallback Anthropic `claude-sonnet-5-5`(호출 단위, ADR-019), Guardrail v2 분류기 Anthropic Haiku 계열(ID·가격 확인 필요), 채점 Gemini `gemini-3.8-flash`(게이트웨이 미경유)
- 관측: Langfuse v3 셀프호스팅(compose profile `obs`), 꺼져 있으면 로컬 JSONL trace
- 저장소: Postgres(compose, 인스턴스 하나에 DB 분리)
- 감사 로그: JSONL, SHA-256 해시체인 + Ed25519 서명
- 웹: Next.js(App Router, TypeScript) + BFF route handler, Tailwind, shadcn/ui, pnpm(Node·pnpm 버전 고정, 설치는 사용자)
- 테스트·린트: pytest, ruff / tsc, eslint, vitest, Playwright(e2e, replay 모드). `make verify`는 ruff check → ruff format --check → pytest → web typecheck(tsc) → eslint → vitest 순서로 실행한다. Playwright e2e는 M5 이후 추가한다.

## 계층 경계

패키지 이름은 `govops`(`src/govops/`)다(ADR-016). 경계는 계층 가드 테스트(`tests/test_architecture.py`)로 강제한다.

| 계층 | 위치 | 맡는 일 | import 금지 |
|---|---|---|---|
| API | `api/` | 세션 인증, task·승인 API, SSE | `broker`, `mocks`, LLM SDK(`openai`·`anthropic`·`google.genai`·`litellm`) |
| 에이전트 | `agent/` | 그래프·상태·노드, 실행 전 결정론 검사, 승인 정책, MAX_STEPS/MAX_COST | `broker`, `mocks`, `eval`, LLM SDK(모델 호출은 `gateway` 클라이언트만) |
| 도구 | `tools/` | 도구 계약(스키마·버전), A MCP·mock REST 클라이언트, outbox·표식 조회, 타임아웃·재시도·서킷 | `agent`, `api`, `eval`, `mocks`, `langgraph`, LLM SDK |
| 중개 | `broker/` | `credential_for(user, audience) -> bearer`, A 토큰 파일을 읽는 유일한 곳 | `agent`, `api`, `tools`, `gateway`, `obs`, `eval`, `langgraph` |
| 게이트웨이 | `gateway/` | LiteLLM 호출 클라이언트, LiteLLM 안에서 도는 Guardrail 훅 어댑터 | `agent`, `tools`, `broker`, `api` |
| Guardrail 판정 | `guardrails/` | PII 정규식·인젝션 패턴·팀별 정책 표(순수 Python) | `litellm`, `langgraph`, `fastapi`, 다른 `govops` 계층 전부 |
| 감사 | `audit/` | 감사 로그 쓰기(유일한 쓰기 지점)·검증 | `agent`, `tools`, `broker`, `api` |
| 관측 | `obs/` | trace span·전송 전 마스킹·JSONL/Langfuse 출력 | `broker` |
| 평가 | `eval/` | 평가셋·TCR·정확도·채점(Gemini 직접 호출) | `gateway`(채점은 게이트웨이를 거치지 않는다), `broker` |
| mock | `mocks/` | mock-jira·mock-slack 서비스와 장애 주입 | `govops`의 다른 계층 전부(외부 시스템 역할) |
| 기록 재생 | `replay/` | 가짜 A MCP 서버, LLM 응답 기록·재생 | `agent`, `api`, `broker` |
| 평가 실행기 | `evalrun/` | 사용자 실행 dev 평가·관측·record-through | `broker`, `mocks`, `audit`, `guardrails`, LLM SDK; 다른 계층은 `evalrun` import 금지 |
| 웹 | `web/` | 화면 4개, BFF route handler | 브라우저 코드에서 FastAPI 직접 호출 금지, 세션 토큰을 JS가 읽을 수 있는 곳(`NEXT_PUBLIC_*`, localStorage, 응답 본문)에 두지 않음 |

## 고정 제약
- CRITICAL: API 키는 `.env`에만 둔다. `.env`와 A의 `data/interim/mcp/tokens.json`은 사람·에이전트가 열거나 출력하지 않는다. 필요한 변수 이름은 `.env.example`에 적는다.
- CRITICAL: 토큰 값(A bearer, B 세션 토큰, LiteLLM 가상 키, API 키)을 화면·로그·문서·trace에 남기지 않는다.
- CRITICAL: 실제 API 호출·다운로드·설치는 사용자가 실행한다. 하네스 step과 테스트는 mock 또는 기록 재생만 쓴다. `make verify`는 API 키와 네트워크 없이 통과해야 한다.
- CRITICAL: `data/raw/`·`data/interim/`은 커밋하지 않는다(감사 서명 개인키도 `data/interim/`에 0600으로 둔다).
- CRITICAL: 실존 기관·사람 이름으로 합성 기록(티켓·메시지·코멘트)을 만들지 않는다. 실제 규정 원문은 재게시하지 않는다. 기록 재생 파일에는 합성 문서(`syn-*`)만 남기고 검사 테스트로 강제한다.
- CRITICAL: 커밋 전에 `make verify`를 실행한다. 긴 실행은 `run_in_background`로 띄운다.
- CRITICAL: A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)는 수정하지 않는다. A 결함은 A 저장소의 별도 이슈·PR로 다룬다.
- CRITICAL: 결과를 보기 전에 가설을 사전 등록한다. TCR·정확도·지연 목표는 dev 실행 뒤 test 실행 전에 `docs/results/`에 등록한다.

## 아키텍처 규칙
- CRITICAL: 도구 입력 스키마에 사용자·팀·직급 필드를 두지 않는다. 신원은 인증된 세션에서만 정하고, LangGraph 실행 context에는 사용자 ID만 둔다. 승인자 신원도 세션에서 정한다.
- CRITICAL: 자격 증명은 도구 클라이언트가 요청 직전에 `broker.credential_for(user, audience)`로 받아 요청 헤더에만 붙인다. 토큰은 state·checkpointer·trace·로그·LLM 컨텍스트·브라우저에 두지 않는다. 저장된 checkpoint·trace·로그 스캔 테스트로 강제한다.
- CRITICAL: 부작용 도구(메시지 발송, 티켓 코멘트, 티켓 생성)는 승인과 outbox를 거쳐서만 실행한다. 승인 노드에는 `interrupt()`만 두고 실행은 별도 노드에 둔다. 승인은 초안 내용 해시에 묶는다. 발송 신원은 요청자이고, 승인자는 본문만 수정할 수 있으며 수신처는 바꿀 수 없다.
- CRITICAL: 부작용 실행 직전에 결정론 검사(수신 채널 허용 목록, 발송문 금지 PII, 팀별 정책)를 에이전트 안에서 강제한다. 탐지 모델 판정에 의존하지 않는다.
- CRITICAL: 부작용 도구는 재시도 전에 항상 표식을 2단계로 조회(조회 → 대기 → 재조회)하고 둘 다 없을 때만 다시 보낸다. 바로 재시도는 연결 수립 실패와 429(`Retry-After` 준수)뿐이다. 표식은 본문이 아니라 메타데이터 필드에 두고, 조회는 채널 최근 이력·티켓 하위 목록으로 한다(검색 API 금지). 멱등 키 = task·도구·정규화 인자 해시.
- CRITICAL: 에이전트의 모델 호출은 `gateway` 클라이언트(LiteLLM)로만 한다. 채점(Gemini)은 게이트웨이를 거치지 않고 별도 비용 원장에 기록한다.
- CRITICAL: 모든 유료 API 호출은 호출 직전에 공급사별 누적액을 검사하고, 상한(OpenAI $9.13 주 모델, Anthropic $11.47 fallback·v2 분류기, Gemini $7.29 채점, B 전체 기간 총액)을 넘으면 호출하지 않는다. 원장에는 `purpose`(`agent_primary`·`agent_fallback`·`guardrail_v2`·`grading`)를 기록한다.
- CRITICAL: Guardrail 사전 검사의 오류·타임아웃은 차단(fail-closed), 사후 탐지의 오류는 통과 + 경보(fail-open)다. v2 분류기가 실패하면 v1 결과로 판정하고 경보하며, v1까지 실패하면 차단한다.
- CRITICAL: 감사 로그는 `audit/` 한 곳에서만 덧붙인다. 원문 대신 내용 해시와 mock ID만 기록한다(PII 금지). 실행 전 의도를 기록하고, 감사 로그 쓰기에 실패하면 부작용을 실행하지 않는다.
- CRITICAL: A 계약 스냅샷(A 커밋 해시와 함께 복사한 도구 4개 v1)은 수정하지 않는다. 시작 시 live `tools/list`와 비교해 다르면 중단한다.
- CRITICAL: 기록 재생 파일에 토큰·Authorization 헤더를 남기지 않는다. 기록 파일의 프롬프트 해시가 현재와 다르면 CI는 "재기록 필요"로 실패한다.
- CRITICAL: mock 관리 엔드포인트(초기화·장애 주입 설정·상태 조회)는 compose 내부 네트워크에만 연다.
- CRITICAL: 데모 로그인은 `DEMO_LOGIN` 플래그일 때만 열고 127.0.0.1에만 바인드한다.
- CRITICAL: checkpointer의 원문은 task 종료(완료·실패·거부·만료) 7일 뒤 삭제한다(ADR-015).
- 탐색 예산은 MAX_STEPS와 별도로 둔다. 새 정보 없는 도구 호출이 N회 연속이면 종료하고, 권한 판정은 에이전트에게 숨긴다(ADR-014).
- 읽기 권한 거부와 읽을 수 없는 티켓에 대한 쓰기 요청(코멘트·후속 티켓)은 "없음"과 구별되지 않는 응답(mock-jira 404)이다. 명시적 "권한 없음"(403)은 존재가 공개된 mock-slack 채널(채널 목록은 공개 정보)의 쓰기·이력 조회에만 쓴다.
- 모델 ID·설정은 평가 전에 freeze하고, 바꾸면 변경 기록을 남긴다.
- 보고 수치는 live 모드(실제 A) 실행에서만 낸다. replay 모드는 CI·외부 검토자용이다. live 실행마다 A git HEAD·계약 스냅샷 해시·인덱스 이름을 기록한다(기준 `d2eec9c`, 다르면 경고).

## 개발 프로세스
- CRITICAL: 새 기능 구현 시 반드시 테스트를 먼저 작성하고, 테스트가 통과하는 구현을 작성할 것 (TDD)
- CRITICAL: `make verify` 가 유일한 검증 진입점이다. 검증 항목을 추가할 때는 Makefile 의 verify 타깃에 넣는다.
- CRITICAL: 테스트는 LLM·외부 API·A MCP를 mock하거나 기록 재생으로 대신한다. 실제 API를 호출하는 명령은 하네스 step에서 실행하지 않는다.
- git 작업은 사용자가 요청할 때만 한다: 이슈 → `feature/*` 브랜치 → PR → 머지. main에 직접 커밋하지 않는다.
- 커밋 메시지는 conventional commits 형식을 따를 것 (feat:, fix:, docs:, refactor:)

## 명령어
make verify      # ruff check → ruff format --check → pytest → web typecheck(tsc) → eslint → vitest (Stop hook, execute.py, CI가 모두 이 명령을 부른다). API 키·네트워크 없이 통과한다.
make mock-tokens # 사용자별 mock-jira·mock-slack 토큰과 해시 매니페스트 생성(사용자 실행)
make seed-extract # A 합성 티켓·사용자 시드 읽기 전용 추출(사용자 실행)
make record-a # live A의 합성 응답 기록(사용자 실행)
make smoke-contract # live A 도구 계약 일치 확인(사용자 실행)
make smoke-identity # 두 사용자 신원별 티켓 가시성 확인(사용자 실행)
make smoke-visibility # 38명 × 표본 티켓 가시성 대조(사용자 실행)
make smoke-mock # mock 채널·권한·인증 확인(사용자 실행)
make smoke-host-header # live A 인증된 위조 Host 헤더 거절 실측(사용자 실행)

make costs-init # 분리 비용 원장 초기화(사용자 실행)
make agent-key # 에이전트 전용 LiteLLM 가상 키 발급(사용자 실행)
make agent-key-update # 기존 에이전트 키 허용 모델 갱신, .env 키 값 유지(사용자 실행)
make db-init # govops 앱 DB 멱등 초기화(사용자 실행)
make serve # 루프백 API 서비스 실행(사용자 실행)
make ask # 사용자 신원으로 요청·폴링 CLI(사용자 실행)
make smoke-checkpointer # Postgres 체크포인트 쓰기·읽기 왕복(사용자 실행)
make obs-up # Langfuse obs 스택·LiteLLM 콜백 기동(사용자 실행)
make obs-down # Langfuse 중지·기본 LiteLLM 설정 복원(사용자 실행)
make smoke-obs # live task·Langfuse 연결·마스킹 검증(사용자 실행)
make cost-report # 읽기 전용 비용 비율·계획 비용 재추정(사용자 실행)
make smoke-fallback # LiteLLM fallback·원장 정산·장애 주입 백엔드 확인(사용자 실행)
make smoke-agent # 두 사용자 live 조회·관찰·인용·비용 검증(사용자 실행)

make eval-meta-extract # A 합성 평가 메타데이터와 문서 제목(doc_titles.json) 읽기 전용 추출(사용자 실행)
make eval-gen # 평가 task 60개 결정론 생성(사용자 실행)
make eval-cards # 한국어 평문 검수 카드 HTML 한 장 생성(A_ROOT의 합성 본문 읽기 전용, 판정은 사람이 내려받은 confirmed.json만 인정, 사용자 실행)
make eval-review-status # 사람 확정본·명세 해시 검증(사용자 실행)
make eval-human-sheet # test 1회차 층화 표본 블라인드 채점 시트 생성(사용자 실행)
make eval-human-register # 사람 채점 CSV 해시 등록(사용자 실행)
make eval-gemini-grade # 사람 채점 해시 커밋 확인 후 Gemini 채점(사용자 실행)
make smoke-gemini # Gemini 채점·별도 비용 원장 스모크(사용자 실행)
make eval-run-dev # live task·record-through·채점(사용자 실행)

make test-outbox-pg # 테스트 전용 Postgres outbox 검증(사용자 실행)
make scope-index # A 커밋 접근 정책·비합성 문서 합친 접근 인덱스 생성(사용자 실행)
make smoke-effects # compose mock·토큰·시드 필요, 부작용 표식·멱등성 검증(사용자 실행)
