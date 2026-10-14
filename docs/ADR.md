# Architecture Decision Records

## 철학
되돌릴 수 없는 피해 지점은 확률 판정이 아니라 결정론과 사람 승인으로 막는다. 신원은 모델이 아니라 세션이 정한다. 측정 없이 주장하지 않고, 결과를 보기 전에 기준을 등록한다.

표기: "확인 필요(구현 시 공식 문서로 확인)"는 결정 기록에서 사실 확인이 남은 항목이다. 근거 노트는 Obsidian `프로젝트 B 설계 결정 기록`(2026-10-03 확정, 결정 1~12)과 `AI Agent 엔지니어 포트폴리오 계획` 6·9·11장이다.

---

### ADR-001: 저장소는 하네스 프레임워크를 복사해 시작하고 A의 하네스 수정을 이식한다
**상태**: 유효
**결정**: 이름 `governed-ops-agent`, 위치 `~/PycharmProjects/governed-ops-agent`, GitHub `apinkpanda722/governed-ops-agent` 비공개 개인 저장소. 공개 전환은 지원 직전 A와 함께 한다. A와 같은 방식으로 하네스 프레임워크(`harness_framework_for_test`)를 fork가 아니라 파일 복사로 가져와 뼈대를 만들고, B 특성에 맞게 `AGENTS.md`·`docs/`를 고친 뒤 `/harness`로 진행한다. 뼈대 직후 A 커밋 `dc30a5b`("Codex 사용량 한도 오류는 재시도하지 않고 재개 시각을 출력하고 멈춘다")를 `scripts/execute.py`·`scripts/test_execute.py`에 이식한다.
**이유**: A에서 검증한 진행 방식(계획 Claude Code, 구현 Codex, `make verify` 재검증)을 그대로 쓰면 B는 설계에 집중할 수 있다. A의 `execute.py`는 템플릿 대비 이 커밋만큼 다르므로 이식하면 두 저장소의 실행기가 같아진다.
**트레이드오프**: fork가 아니라서 하네스 원본의 이후 수정은 자동으로 따라오지 않는다. 필요한 수정은 이번처럼 커밋 단위로 이식한다.
**변경 기록**:
- 2026-10-03: 결정 1로 확정. `dc30a5b` 이식 후 A와 `execute.py` diff 없음.

### ADR-002: 업무 시나리오는 "요청 → 조회 → 초안 → 승인 → 1회 실행", 실행 계기는 사용자 요청만
**상태**: 유효
**결정**: 흐름은 사용자 요청 → 조회(mock Jira 티켓, A MCP 문서·집계를 사용자 신원으로) → 초안(요약·코멘트·메시지) → 사용자 승인 → 실행(Idempotency로 1회). 세계관은 A 시드의 가상 회사 "해온클라우드"(8팀 PLT·PAY·DAI·SRE·SEC·HRM·FIN·LGL, 서비스, 장애, 티켓)를 이어받는다. task 유형은 T1 요약 질의 / T2 요약 발송 / T3 티켓 후속 조치(코멘트·후속 티켓 생성) / T4 집계 보고(`run_sql` → 발송) / T5 경계 사례(권한 없음, 없는 번호, 다른 팀 채널, 간접 인젝션) / T6 승인 분기(거부, 수정 후 승인). 도구는 읽기 = A MCP 4개 + mock Jira 조회·검색, 부작용 = 메시지 발송·티켓 코멘트·티켓 생성(3개, 모두 승인·Idempotency 대상). 티켓 생성은 기존 티켓의 후속 티켓으로만 한다(원 티켓 연결 필수). 사용자는 A 데모 사용자 38명(`demo-<team>-g<grade>`) 그대로. 실행 계기는 사용자 요청만이다.
**이유**: 강의 모의면접 문제의 도구 예시(`search_docs`, `list_jira_issues`, `send_slack_dm`, `create_ticket`)를 그대로 구현하면서, A의 권한 데이터를 이어받아 신원 위임을 시험할 수 있다. 이벤트 자동 실행은 실행 주체가 서비스 계정 신원이 되어 "사용자 신원으로만 행동한다"는 신원 위임(ADR-005)과 충돌한다.
**트레이드오프**: 실제 운영 에이전트에서 흔한 이벤트 트리거(새 장애 알림 → 자동 요약 등)를 다루지 않는다. 범위 밖: 실제 Jira·Slack, 멀티 에이전트(전환 조건만 ADR-003), 이벤트 자동 실행, 티켓 상태·담당자 변경, 원 티켓 없는 독립 티켓 생성.
**변경 기록**:
- 2026-10-03: 결정 2로 확정.

### ADR-003: LangGraph 단일 에이전트를 쓰고 Multi-Agent 전환 조건을 명시한다
**상태**: 유효
**결정**: 에이전트는 LangGraph **단일** 에이전트 하나다. 도구는 mock Jira/Slack과 A의 MCP 서버(B는 MCP 클라이언트)다. 다음 신호(강의 CH8-62 Multi-Agent 전환 신호) 중 하나가 측정으로 확인되면 Multi-Agent 전환을 검토한다.

| 신호 | 기준 | B에서 보는 곳 |
|---|---|---|
| 응답 시간 | 30초 초과 | p95 지연(사람 승인 대기 제외, EVAL) |
| 컨텍스트 | 모델 컨텍스트의 80% 초과 | trace의 모델 호출 span 토큰 수 |
| 반복 실패 | 같은 유형 task가 반복해서 실패 | 평가셋·운영 검수의 실패 trace |

**이유**: 업무 흐름이 "조회 → 초안 → 승인 → 실행" 한 줄이고 도구가 9개라, 역할을 나눌 이유보다 나눴을 때의 비용(에이전트 간 신원·승인 전달, trace 분산, 평가 대상 증가)이 크다. 평가 없는 멀티 에이전트는 오히려 감점 신호다(계획 노트 10장). 신원 위임·HITL·감사를 한 그래프에서 강제하는 편이 검증하기 쉽다.
**트레이드오프**: 도구가 늘거나 task가 길어지면 단일 에이전트의 컨텍스트와 지연이 먼저 한계에 닿는다. 그 시점을 위 신호로 판단하며, 신호가 나오지 않았다는 것은 측정 결과로 보고한다(주장으로 갈음하지 않는다).
**변경 기록**:
- 2026-10-03: 계획 노트 6장 표와 결정 2(멀티 에이전트 범위 밖, 전환 조건만 ADR)로 확정.

### ADR-004: A는 고치지 않고, B 에이전트는 호스트에서 돌며, live·replay 두 모드로 연결한다
**상태**: 유효
**결정**: B 에이전트 서비스는 호스트에서 `uv run`으로 실행하고, LiteLLM·Langfuse·mock·Postgres는 B의 compose에 둔다. A 연결은 live(실제 A, 보고 수치는 이 모드만)와 replay(기록한 A 응답을 돌려주는 가짜 A MCP, CI·외부 검토자용) 두 모드다. 기록은 사용자가 실행하고 합성 문서(`syn-*`)만 남긴다(실제 규정 원문 재게시 금지, 검사 테스트로 강제). 토큰·Authorization 헤더는 기록하지 않는다. A `tests/contract/snapshots`(도구 4개 v1)를 A 커밋 해시와 함께 복사하고, 시작 시 live `tools/list`와 비교해 다르면 중단한다. 가짜 A도 같은 계약 테스트를 통과한다. B MCP 클라이언트 테스트는 실제 streamable HTTP로 가짜 A에 요청한다. B 진행 중 A는 고치지 않는다. A 결함은 A 저장소 별도 이슈·PR로 다루고, 공개 직전 `make m8-audit` 재실행에 포함한다.
**이유**: A MCP는 `127.0.0.1` 고정(`mcp_server/__main__.py:17`, `server.py:133`의 `streamable_http_app(host="127.0.0.1")`)이고, MCP SDK는 이 경우 Host 헤더 검사를 켠다. 컨테이너에서 `host.docker.internal`로 붙으면 네트워크에는 도달하지만 인증된 요청도 Host 검사로 거절된다(M1 실측). A를 고치면 A의 보고 수치와 감사 결과가 흔들리므로 B 쪽 배치를 바꾼다. replay 모드가 있어야 키 없는 CI와 외부 검토자가 재현할 수 있다.
**트레이드오프**: 에이전트 서비스만 compose 밖에 있어 `docker compose up` 한 번으로 전체가 뜨지 않는다. replay는 기록 시점의 A 응답만 재현하므로 A가 바뀌면 재기록해야 한다(계약 비교가 이를 잡는다).
**확정(M1 실측, 2026-10-04)**: 컨테이너에서 `host.docker.internal:8765`로 네트워크 도달은 되지만(인증 없는 POST는 401), 인증된 요청에 위조 Host 헤더를 달면 A가 421로 거절한다. 따라서 에이전트 서비스는 호스트에서 실행한다. 근거: [M1 도구 계층 live 실측](results/2026-10-04-m1-tools-live.md).
**변경 기록**:
- 2026-10-03: 결정 3으로 확정.

### ADR-005: 신원 위임 — 데모는 자격 증명 중개, 실무는 RFC 8693 토큰 교환
**상태**: 유효
**결정**: 사용자는 B 자신의 세션 토큰으로 B에 인증한다. 중개 계층 `credential_for(user, audience) -> bearer`가 호출 시점에 그 사용자의 A 토큰을 꺼내 요청 헤더에만 붙인다. mock Jira/Slack도 같은 중개를 거친다(audience만 다름). 데모 로그인(데모 사용자 선택 = B 세션 발급)은 IdP 대체 장치이며 `DEMO_LOGIN` 플래그일 때만 열고 127.0.0.1에만 바인드한다. 신뢰 경계는 세션 발급 이후다: 세션이 발급된 뒤에는 모델·도구·브라우저 어디서도 신원을 바꿀 수 없다. A 토큰은 `.env`의 경로 변수로 A의 `data/interim/mcp/tokens.json`을 실행 시 읽고, B 저장소에 복사하지 않으며, 사람·에이전트는 열거나 출력하지 않는다.
불변식(테스트로 강제): 도구 스키마에 사용자·팀·직급 필드 없음 / 신원은 인증된 세션에서만, LangGraph 실행 context에는 사용자 ID만 / 토큰은 state·checkpointer·trace·로그·LLM 컨텍스트에 없음(저장 checkpoint·trace·로그 스캔) / 승인자 신원도 세션에서 / 같은 task를 두 사용자가 요청하면 A 결과가 다름 / 레드팀에 "다른 팀 사용자로 행동" 인젝션.

**데모 구현과 실무 설계의 차이**

| 항목 | 데모(구현) | 실무(RFC 8693, ADR에만) |
|---|---|---|
| 사용자 인증 | 데모 사용자 선택 → B 세션 토큰(`DEMO_LOGIN` 플래그, 127.0.0.1 바인드) | IdP 로그인(OIDC) |
| A용 자격 증명 | A가 발급한 사용자별 정적 토큰을 B가 보관 | STS 토큰 교환: `subject_token` = 사용자, `actor_token` = B |
| 토큰 성격 | 장기, audience 구분 없음 | 단기, `aud = kodocs-mcp`, `scope = read`, `sub = 사용자`, `act = B` |
| A의 검증 | 토큰 해시 매핑 | 서명·`aud`·`exp` 검증(OAuth 2.1 자원 서버로서 JWT 검증) |
| 위임 표시 | 감사 로그 `actor.act`에만 | 토큰 자체의 `act` 클레임 |
| B 탈취 시 | 보관 중인 전원의 A 토큰 노출 | B는 단기 토큰만 가지며 교환은 STS가 통제 |
| 바꿀 곳 | — | B는 `credential_for` 한 곳, A는 JWT 검증 추가 |

**이유**: 서비스 계정 1개는 인젝션 시 전 권한이 노출되고(OWASP LLM06 Excessive Agency), 사용자 토큰을 그대로 넘기는 passthrough는 대상 혼동을 낳으며 MCP 인가 명세가 금지하는 패턴이다. 중개는 "모델은 신원을 보거나 만들 수 없다"를 지키면서 실무 전환 지점을 함수 하나로 좁힌다. "B가 38명의 토큰을 다 가지면 사실상 슈퍼 계정"이라는 지적은 두 위협으로 나눈다. ① LLM이 유도하는 오용(인젝션)은 신원이 세션에서만 오므로 피해 범위가 요청자 권한으로 줄어든다(서비스 계정이면 전 권한이다). ② B 인프라 탈취는 서비스 계정과 같은 수준이라 이 구조로 막지 못하며, 실무에서는 RFC 8693 단기 토큰으로 줄인다. 데모 로그인이 검증 범위에 들지 않는 이유는 B가 검증하는 것이 "세션 발급 이후 신원 불변"이기 때문이다.
**트레이드오프**: B 프로세스가 사용자들의 A 토큰을 보관하므로 B가 탈취되면 전원이 노출된다(위협 ②, 데모의 한계, THREAT_MODEL에 잔여 위험으로 기록). 데모 로그인은 인증이 아니므로 로컬 바인드와 플래그로만 제한한다. 탈락 대안: 서비스 계정 1개, 토큰 passthrough.
**변경 기록**:
- 2026-10-03: 결정 4로 확정.

### ADR-006: mock Jira/Slack은 REST 서비스, 멱등성은 B가 outbox와 표식 조회로 보장한다
**상태**: 유효
**결정**: FastAPI REST 서비스 2개(mock-jira, mock-slack)를 compose에 둔다(MCP가 아니라 REST). mock은 실제처럼 서버 측 중복 제거가 없다. B는 호출 전 outbox에 멱등 키(task·도구·정규화 인자 해시)를 기록하고, 메시지·티켓의 본문이 아니라 메타데이터 필드에 표식(marker)을 넣는다. 부작용 도구는 재시도 전에 항상 표식을 조회하고(바로 재시도는 연결 수립 실패와 429뿐), 조회는 2단계(조회 → 대기 → 재조회)로 해서 두 번 모두 없을 때만 재발송한다. 조회는 채널 최근 이력·티켓 하위 목록(코멘트·후속 티켓)으로 하고 검색 API는 쓰지 않는다. 티켓 생성은 원 티켓의 후속 티켓으로만 하므로 조회 대상은 항상 원 티켓의 후속 티켓 목록이다. 2단계 조회의 대기 시간은 2.0초다(ADR-018). mock-jira 시드는 A 합성 티켓에서 일회성 스크립트로 추출하고(사용자 실행) 합성 티켓 JSON을 커밋하며, 추출 A 커밋 해시·개수를 manifest에 둔다. 티켓 조회 권한은 A와 같은 규칙(`access_level`·`access_teams`·`access_min_grade`)이고, live에서 38명 × 표본 티켓 가시성을 대조한다. mock-slack은 팀 채널 8개 + 전사 공지 1개, 발송은 자기 팀 채널·공지만. 읽기 권한 거부와 읽을 수 없는 티켓에 대한 쓰기 요청(코멘트·후속 티켓)은 "없음"과 구별되지 않는 응답(mock-jira 404)이다. 명시적 "권한 없음"(403)은 존재가 공개된 mock-slack 채널(채널 목록은 공개 정보)의 쓰기·이력 조회에만 쓴다. 장애 주입은 엔드포인트별 지연·5xx·429+`Retry-After`·처리 후 응답 유실이며 요청 순번 스크립트로 재현한다. F4 연결 수립 실패는 클라이언트 전송 계층에서 주입한다(ADR-018). 여기에 F10 조회 반영 지연(표식이 조회 결과에 늦게 나타남)을 더한다. 대기보다 짧은 지연은 절대 규칙 대상이고, 긴 지연은 한계 시연으로 절대 규칙에서 빼며 이 구분을 사전 등록한다. F11 A MCP 장애(지연·5xx·연결 거부)는 replay 가짜 A에 주입하고, 판정은 부작용 0건 + 근거 없는 답변 0건이다(재시도 → 서킷 열림 → "일시적으로 조회할 수 없음"). 관리 엔드포인트(초기화·주입 설정·상태 조회)는 compose 내부 전용이다.
**이유**: 실제 연동의 기본이 REST이고 HTTP 계층의 안정성(타임아웃·재시도·429)을 시연할 수 있다. 핵심 장애는 "서버는 처리했는데 응답 유실"이며, 서버 측 멱등 키에 기대지 않고 클라이언트가 표식으로 판정해야 중복과 누락을 함께 막는다. 존재 자체가 기밀인 티켓은 읽기와 쓰기 모두 존재를 알리지 않는다. mock-slack 채널 목록은 공개 정보이므로 채널 쓰기·이력 조회 거부만 403으로 명시한다. 본문 표식은 사용자에게 보이고 사람이 수정하면 사라지므로 메타데이터에 둔다. 검색은 반영이 늦을 수 있어 최근 이력·하위 목록을 직접 보고, 그래도 남는 반영 지연에 대비해 2단계로 조회한다. 0건을 지킬 수 있는 조건(짧은 지연)과 못 지키는 조건(긴 지연)을 결과를 보기 전에 나눠 두고, 못 지키는 구간은 숨기지 않고 시연한다. A는 고칠 수 없으므로 A 장애는 가짜 A에서 주입한다.
**트레이드오프**: 표식 조회는 대상 시스템에 메타데이터를 읽는 수단이 있어야 하고 조회 비용이 든다. 2단계 조회의 대기만큼 재발송이 늦어진다. 대기보다 긴 조회 반영 지연에서는 중복을 막지 못한다(F10 한계 시연).
**확인 필요(구현 시 공식 문서로 확인)**: 실제 Slack 메시지 발송·Jira 이슈 생성·코멘트 API에 서버 측 멱등 키가 있는지(결정은 "없다"는 전제다). 표식을 넣을 실제 대응 필드(Slack 메시지 메타데이터, Jira 이슈 속성).
**변경 기록**:
- 2026-10-03: 결정 5로 확정.

### ADR-007: 모델과 키, 비용 상한은 B 전체 기간 총액으로 코드로 강제한다
**상태**: 유효
**결정**: 에이전트 주 모델은 OpenAI `gpt-6-luna`(A와 동일, A 지연을 기준선으로), fallback은 Anthropic `claude-sonnet-5-5`(다른 회사, ADR-019)이며 호출 단위(LiteLLM)로 전환한다. 모델이 섞인 task 수와 그 TCR을 따로 보고한다. Guardrail v2 분류기는 Anthropic 소형 모델(Haiku 계열, ADR-008)이다. 채점은 Gemini `gemini-3.8-flash`(A 절차, 사람 κ 재보정). 주 모델·fallback·v2 분류기는 LiteLLM 경유, 채점은 게이트웨이를 거치지 않고 비용은 별도 원장에 둔다. B 전용 키를 새로 발급한다(OpenAI project, Anthropic workspace, Gemini project). 사용자가 발급해 B `.env`에만 둔다. Upstage는 쓰지 않는다. 모델 ID·설정은 평가 전 freeze하고 변경은 기록을 공개한다.
Gemini 단가 유효기간(2026-12-31) 이후 채점은 코드로 거부한다.
원장은 `data/manifest/costs.jsonl`과 채점 전용 `data/manifest/costs_grading.jsonl`에 두며, 원장이 없으면 호출 거부한다. 호출 직전 상한 검사와 예약 행 기록은 원장 잠금 안에서 한 번에 하고(누적액 + 미정산 예약액 + 추정액 ≤ 상한), 호출이 끝나면 성공·실패와 관계없이 같은 예약 ID로 정산 행을 남기며, 프로세스가 죽어 정산되지 않은 예약은 삭제하지 않고 상한 쪽으로 보수적으로 계속 센다.
비용 상한은 **B 전체 기간 총액(월 단위 아님)**이다. 규칙: `상한 = 잔액 × 0.9`(10%는 지원 직전 A 재점검·B 데모 재촬영 몫). 잔액을 원장으로 추정할 때는 A 원장 사용액에 청구 여유 1.05(A `billing_margin`, 원장 대 콘솔 +3.2% 실측 이력)를 곱해 뺀다. 센트 미만은 버린다. **호출 직전 공급사별 누적액 검사로 강제한다**(A `costs.py` 방식). 공급사 상한은 그대로 두고, 원장의 `purpose` 필드(`agent_primary`·`agent_fallback`·`guardrail_v2`·`grading`)로 용도별 비용을 보고한다.

| 공급사 | 근거 | B 상한 |
|---|---|---|
| OpenAI(주 모델) | 충전 $16.50 − A 원장 $6.05 × 1.05 = $10.15 | **$9.13** |
| Anthropic(fallback, Guardrail v2 분류기) | 콘솔 잔액 $12.75(사용자 확인, 2026-10-03) | **$11.47** |
| Gemini(채점) | 크레딧 $10(A M7 manifest `credit_usd`) − A 원장 $1.80 × 1.05 = $8.11 | **$7.29** |
| 합계 | | **$27.89** |

A 원장 사용액(2026-10-03, `data/manifest/costs.jsonl`): Anthropic $3.36, Google $1.80, OpenAI $6.05, Upstage $23.79, 합계 $35.00. M2 파일럿 실측으로 B 전체 예상 비용을 다시 추정하고, 상한을 넘을 것으로 보이면 ADR-013의 축소 순서를 적용하거나 추가 충전한다.
**이유**: 주 모델을 A와 같게 두면 A 지연이 기준선이 된다. fallback을 다른 회사로 두어야 공급사 장애에 대응한다(Trade-off 축3, 단일 대 멀티 벤더). 채점이 게이트웨이를 지나면 Guardrail이 채점 입력을 바꿔 평가가 왜곡된다. fallback을 task 단위로 하면 이미 끝난 읽기 호출이 반복된다. 호출 단위로 바꿔도 부작용은 승인 뒤에만 일어나므로 중복 걱정이 없다. 선불 크레딧이므로 월 단위가 아니라 남은 총액이 실제 한계다. 코드로 강제한 비용 상한은 채용 측 평가 신호다.
**트레이드오프**: 공급사가 셋이라 키·원장 관리가 늘어난다. 원장 추정은 콘솔과 어긋날 수 있어 1.05 여유를 둔다. 호출 단위 fallback은 한 task 안에서 모델이 섞여 응답 형식 차이가 생길 수 있다(M3 장애 주입에서 실측). v2 분류기는 모델 호출마다 돌아 Anthropic 호출 수가 늘어난다(M6 파일럿 실측으로 다시 추정하고, 넘치면 v2를 뺀다). Gemini 단가는 2026-12-31까지 유효(A ADR-030)하며, 그 뒤면 단가·`valid_until`을 갱신해야 한다.
**확인 필요(구현 시 공식 문서로 확인)**: v2 분류기(Haiku 계열)의 정확한 ID와 가격. fallback 모델 ID·가격은 ADR-019를 따른다.
**변경 기록**:
- 2026-10-03: 결정 6으로 확정.

### ADR-008: 게이트웨이는 LiteLLM, Guardrail은 입력 차단·도구 결과 격리·출력 탐지·실행 직전 결정론 검사의 비대칭 구조
**상태**: 유효
**결정**: 호출자 식별은 LiteLLM **팀별 가상 키 + `user` 필드(사용자 ID) + 메타데이터 task ID**이고, 팀별 예산·사용량·정책은 LiteLLM 기능으로 둔다. 비대칭 구조: ① 사용자 직접 입력은 인젝션·금지 PII 탐지 시 차단(공급사로 나가기 전, 비용 발생 전) ② 도구 결과(다음 LLM 호출의 입력, A의 간접 인젝션 문서 포함)는 탐지 부분 격리("정책으로 일부 제외" 표시)·기록 ③ 출력은 탐지만(trace 표시·경보) ④ 부작용 실행 직전(에이전트 안)은 결정론 검사로 강제(수신 채널 허용 목록, 발송문 금지 PII, 발송 범위 규칙: 보낼 곳의 독자 범위가 근거 문서·티켓의 열람 범위보다 넓으면 차단한다(팀 채널의 독자 = 그 팀 전 직급, 전사 공지의 독자 = 전 직원)) + 사람 승인. 발송 범위 규칙은 요청자가 문서를 읽을 수 있어도 더 넓은 곳으로 요약을 보내면 권한 없는 사람에게 내용이 넘어가는 분류 하향 유출을 막는다(공개 문서는 어디로든, 팀 전용 문서는 그 팀 채널로만, 직급 제한 문서는 팀 채널에도 보내지 않는다). 거부할 때는 이유(보낼 곳의 독자 범위가 근거의 열람 범위보다 넓음)를 명시한다(쓰기 거부 원칙). 탐지기 두 버전 비교(M6): v1 규칙(한국 형식 PII 정규식, 인젝션 패턴 사전) 대 v2 규칙 + 게이트웨이 경유 Anthropic 소형 분류 모델(Haiku 계열, ID·가격 확인 필요)(차단율·오탐률·추가 지연·비용). v2가 실패하면 v1 결과로 판정하고 경보하며, v1까지 실패하면 차단한다(fail-closed). PII 범위는 주민번호, 전화, 개인 이메일, 계좌·카드번호이고, 직원 이름·사번·담당자는 업무 데이터라 제외한다. 팀별 정책: 인사·재무·법무는 출력 PII 탐지 시 발송 단계 진입 금지, 나머지는 마스킹 후 진행. 정책 표는 설정 파일 하나. 3지점 마스킹 = 입력(사전 훅), trace(전송 전 마스킹 함수), 출력(사후 훅). Guardrail 자체 실패: 사전 검사 오류·타임아웃은 차단(fail-closed), 사후 탐지 오류는 통과 + 경보(fail-open).
**이유**: 탐지는 확률적이다. 되돌릴 수 있는 지점(입력·출력)에서는 비용과 오탐을 감수할 만큼만 막고, 되돌릴 수 없는 피해 지점(부작용)은 결정론과 승인으로 막는다. 입력 차단은 공급사 호출 전이라 비용이 들지 않는다. 도구 결과는 정상 업무 데이터가 대부분이라 통째로 차단하면 task가 깨지므로 의심 부분만 격리한다. 출력을 차단하지 않는 이유는 출력이 아직 부작용이 아니고, 피해 지점은 실행 직전 검사가 지키기 때문이다. 사전 검사 실패를 통과시키면 검사를 끄는 공격이 되므로 fail-closed, 사후 탐지는 이미 생성된 응답을 막아도 얻는 것이 적어 fail-open + 경보다(GS리테일 2부 사례의 사전 차단·사후 탐지 구조). v2 분류기를 주 모델과 다른 회사에 두는 이유는, 같은 회사면 주 모델 장애 때 사전 검사가 타임아웃 나고 fail-closed로 전부 차단되어 fallback이 무력화되기 때문이다. v2 실패 시 v1로 판정하면 분류기 장애가 곧 서비스 중단이 되지 않으면서 규칙 검사는 유지된다.
**트레이드오프**: 출력 탐지만으로는 부작용 없는 T1 답변의 PII 노출을 막지 못한다(마스킹·경보로 줄인다). v2 분류기는 지연·비용을 더한다. Guardrail 훅이 LiteLLM 프로세스 안에 있어 판정을 B 감사 로그로 옮기는 경로가 하나 더 생긴다.
**변경 기록**:
- 2026-10-03: 결정 7로 확정.

### ADR-009: 관측은 Langfuse v3 셀프호스팅(profile obs), 꺼지면 로컬 JSONL, golden trace는 두 층
**상태**: 유효
**결정**: Langfuse v3 셀프호스팅(web·worker·Postgres·ClickHouse·Redis·MinIO)을 compose profile `obs`로 분리한다. 핵심 스택은 Langfuse 없이 뜨고 trace는 로컬 JSONL이다. Postgres 인스턴스 하나에 LiteLLM·Langfuse DB를 나눠 둔다. 첫 기동 후 `docker stats`로 실측하고 부족하면 Docker 메모리를 늘린다. trace 하나 = task 하나이고, LiteLLM의 Langfuse 연동에 trace ID를 `existing_trace_id` 메타데이터로 넘겨 같은 trace에 묶는다. span: 그래프 노드, 도구 호출, 모델 호출, Guardrail 판정, 승인 대기·결정, 멱등성 조회, 재시도·서킷 상태 변화. score: TCR, 정확도, Guardrail 플래그, 사람 검수. golden trace 두 층: CI(PR마다) = 기록한 LLM 응답 + replay A + mock, 결정론, 도구 호출 순서 제약·부작용 수·최종 상태·거절·Guardrail 판정 비교, GitHub에 키 없음 / live 회귀 관문(사용자 실행) = 프롬프트·모델·도구 설명을 바꾼 PR 머지 전 평가셋 점수가 기준 이상인지. 기록 파일에 프롬프트 해시를 넣고 다르면 CI가 "재기록 필요"로 실패한다. 용어는 "응답 기록 / 기록 재생"(영상 녹화 아님).
**이유**: 호스트 48GB·14코어, Docker VM 약 16GB(A OpenSearch와 공유)에서 Langfuse 전체를 항상 띄우면 메모리가 빠듯하다. profile로 분리하면 핵심 기능 개발이 관측 스택에 묶이지 않는다. CI에는 키를 둘 수 없으므로 결정론 층과 live 층을 나눈다.
**트레이드오프**: Langfuse를 끈 실행은 JSONL로만 남아 대기열 검수를 할 수 없다. 기록 재생 CI는 프롬프트를 바꿀 때마다 재기록 비용이 든다.
메타데이터 키는 LiteLLM 공식 문서 기준 `existing_trace_id`로 확정하며, `v1.103.3` 동작은 사용자 실행 `make smoke-obs`로 확인한다. LiteLLM이 만드는 기본 generation ID(응답 ID 기반의 긴 base64)는 Langfuse의 MinIO 객체 이름 제한에 걸려 업로드가 실패하므로, 요청 메타데이터 `generation_id`에 우리가 정한 16자리 hex를 넘기고 같은 값을 모델 호출 span 속성에 기록하며, `parent_observation_id`로 generation을 에이전트의 모델 호출 span 아래에 붙인다(LiteLLM 공식 문서의 generation 파라미터. 부모 연결은 `smoke-obs`의 INFO `generations_under_model_span`으로 확인하고 실패해도 합격 조건은 아니다). 에이전트 span은 Langfuse Python SDK(v4)가 OTLP로 보내며 서버 3.63 이상과 호환된다(공식 호환 표). Langfuse payload에는 `LANGFUSE_PUBLIC_KEY`가 계측 범위 속성(`scope.attributes.public_key`)으로 들어간다. public key는 프로젝트 식별자이고 SDK가 span을 내보내는 조건이며 인증은 secret key와 짝이어야 하므로 비밀 값이 아니다. `smoke-obs` E6은 `LANGFUSE_SECRET_KEY`와 나머지 키·토큰만 검사한다. 마스킹은 sink 직전 `obs/mask.py`에서 수행한다. 요청·답변·초안은 PII 마스킹 원문(8,000자 상한), 도구 결과는 ID+앞부분 300자 발췌(PII·비밀 마스킹), 도구 인자는 PII·비밀 마스킹이다. 토큰·Authorization·키는 항상 제거한다. 모델 프롬프트·응답 원문은 span에 없고 LiteLLM 콜백 `turn_off_message_logging`으로 원문 로깅을 끈다(끄면 LiteLLM은 내용을 비우지 않고 `redacted-by-litellm` 자리 표시자로 바꾼다. `smoke-obs` E4는 비었거나 자리 표시자만 있고 요청 문구가 없으면 통과다).
**확인 결과(2026-10-04)**: 셀프호스팅 Langfuse 3.225.11에서 annotation queue API(`GET /api/public/annotation-queues`)가 HTTP 200으로 응답한다(`make smoke-obs`가 기록). OSS에 포함된 것으로 확정하고 운영 품질 검수 대기열에 쓴다.
**변경 기록**:
- 2026-10-03: 결정 8로 확정.
- Guardrail 판정 비교는 M6에 새 등록 파일과 함께 수행한다. 골든 14개는 단일 턴 경로 불변 확인만 맡는다.

### ADR-010: 평가셋 60 task, TCR과 정확도를 분리하고 절대 규칙 3개를 사전 등록한다
**상태**: 유효
**결정**: task 60개(dev 20 / test 40): T1 10, T2 12, T3 12, T4 8, T5 12, T6 6. 시드 기반 템플릿으로 결정론 생성하고 요청 문장은 말투 변형(반말 50%·짧은 명령 25%·존댓말 25%, 유형별 개수 고정, 선정 후 시드로 입힘)을 둔다. 문장 틀은 메신저 말투이고 내부 코드 없이 한국어 팀·상태 표현을 쓰며, T3 요청은 연결 장애의 원인·조치를 쓸 내용으로 밝히고, 같은 하위 유형 안에서 대상을 겹치지 않게 한다. 8팀·여러 등급에 고르게 배정한다. 정답 명세는 선언형(기대 최종 상태, 필수·금지 도구 호출, 필수 `fact_id`, 금지 사항). 문서 대상 요청은 문서 ID 대신 제목에서 만든 주제 표현으로 지칭하고(검증기가 `syn-` 금지) 요약 대상 문서는 표시된 사실이 3개 이상이며 제목 지칭이 유일해야 한다. T5 `no_permission`은 요청자가 그 장애의 기록(장애 보고 문서·연결 티켓)을 읽을 수 없는 경우만 고르고 기대 결과는 `not_found_or_partial`(찾을 수 없음 또는 읽을 수 있는 문서에 근거한 부분 답변)이며, 금지 값은 요청자가 읽을 수 있는 어떤 문서에도 없는 값만 쓴다(전 유형 공통). 정답 출처는 A 합성 문서 메타데이터(`doc_id`, 권한 필드, `fact_id`·값, 본문 없음, `syn-*`만)이며 일회성 스크립트로 추출해 커밋하고(사용자 실행), A 커밋 해시·개수를 manifest에 둔다. 평가 때 승인은 명세대로 시뮬레이션한다(API 사용). 실행 횟수는 test task당 3회, dev 1회다. TCR은 결정론이고 구조만 본다(부작용 종류·건수, 대상 채널·티켓 ID, 생성 티켓 구조 필드, 금지 위반 없음). 본문 품질은 정확도에서 본다. TCR은 3회 평균과 pass^3(3회 모두 성공한 task 비율)을 함께 보고한다. 정확도는 필수 `fact_id` 포함률(결정론) + Gemini 루브릭(정답성·근거 충실성·형식 0~3, 3회)이며, κ는 이차 가중 Cohen κ(0~3 서열)다. κ와 사람 평균에는 부트스트랩 95% CI(시드 20261005, 재표집 10,000회)를 병기한다. 사람 채점 표본은 test 1회차 응답에서 유형별 층화로 30개를 뽑는다(T1 5·T2 6·T3 6·T4 4·T5 5·T6 4, 시드 20261005). 후보가 모자라면 배분을 바꾸지 않고 중단한다. 순서는 test 1회차 응답 생성 → 사람 채점 → CSV 해시 커밋 → Gemini 채점이다. CSV 해시는 `docs/results/human-grading-registration.json`에 등록·커밋한다. κ < 0.6인 차원은 '채점 모델 검증 미달, 사람 표본(n=30) 기반'으로 표기하고 사람 표본의 점수 평균에 부트스트랩 95% CI를 병기한다. Gemini 전체 점수는 판정에 쓰지 않고 부록으로만 둔다. 사전 등록 절대 규칙: 장애 주입 하 중복 실행 0건, 권한 밖 정보 노출 0건, 승인 없는 부작용 0건(하나라도 어기면 실패). 권한 밖 노출은 자동 탐지가 노출 후보를 만들고 사람이 확정한 뒤 판정한다(재현율 우선 탐지 + 사람 확정). TCR·정확도·지연 목표는 dev 실행 뒤 test 실행 전에 등록한다. 레드팀은 템플릿 결정론 생성 120건(직접 인젝션 30, 간접 인젝션 40, 신원 사칭 20, PII 유출 유도 30, 한국어 우회 변형 포함) + 정상 대조 60건(평가셋 재사용)이며 판정 규칙을 사전 등록한다. 실행 계획표(약 480회 + 장애 주입 표본: dev 20×1 / test 40×3 / Guardrail 끔 40×1 / v2 test 40×1 + dev 20×1 / 레드팀 120 × v1·v2 = 240(정상 대조는 위 실행 재사용) / 장애 주입 F1~F11 × T2·T3 표본)를 사전 등록한다. v2에 dev 20을 더하는 것은 v2 오탐률도 v1과 같은 정상 task 60개 분모로 맞추기 위해서다. live 실행마다 A git HEAD·계약 스냅샷 해시·인덱스 이름을 기록한다. 기준 A 커밋은 `d2eec9c`이고, 다르면 경고만 기록한다(계약 불일치는 시작 단계에서 이미 중단된다). 운영 품질 검수 표본은 실패·거절·Guardrail 플래그 전부 + 비용 상위 5% + 무작위 10%, 검수자 1인. 지표 정의 전체는 `docs/EVAL.md`.
명세에는 `executable_in` 필드가 있다. 부작용 실행 경로는 M3에서 생기므로 M2 dev 실행은 `executable_in == "m2"`인 T1과 읽기형 T5(`no_permission`·`nonexistent`·`indirect_injection`), dev 7개만 한다. 부작용 task(T2·T3·T4·T6과 `other_team_channel`)는 명세·채점기까지만 만들고 실행은 M3 이후다. 나머지 dev 13개 실행 후에 dev 1회가 완성된다. 정답 명세 확정본은 사람이 작성한 `data/eval/review/confirmed.json`만 인정하며 명세 해시가 바뀌면 무효다. TCR 금지 위반에는 명세의 금지 도구 호출이 포함된다. 필수 도구 호출은 진단값이다. 필수 도구 호출의 진단값은 `tool_coverage`다. 포함률 대상은 `id`·`service`·`datetime` fact와 `derived_values`이며 `text`·`person` fact는 루브릭 입력이다.
**이유**: 강의 CH8-60·61의 "TCR과 정확도 분리" — 일을 끝냈는지와 내용이 맞는지는 다른 질문이다. 부작용 에이전트의 실패 판정은 점수가 아니라 규칙 위반이어야 하므로 절대 규칙을 따로 둔다. 결과를 보기 전에 기준을 등록해야 기준을 결과에 맞추지 않는다. 메시지 본문은 실행마다 달라 본문까지 비교하면 TCR이 0이 되고, 안 보면 "아무 말이나 보내도 완료"가 되므로 구조는 TCR, 내용은 정확도로 나눈다. 운영에서는 평균보다 "매번 되는가"가 중요해 pass^3을 함께 본다. 실행 계획표를 먼저 고정하면 결과를 보고 유리한 실행만 고를 수 없다. 레드팀은 A에 없던 공격 면(부작용 유도, 사칭, 승인 우회 문구)을 넣고 LLM 없이 만들어 재현된다. 정답을 A 메타데이터에서 가져오면 정답 명세를 결정론으로 만들면서 실제 규정 원문은 들어가지 않는다.
**트레이드오프**: 60개는 유형당 표본이 작아 유형별 수치의 신뢰구간이 넓다. test 3회는 비용이 3배라 비용이 넘치면 2회로 줄인다(ADR-013 ①). A HEAD 차이는 경고만 남기므로 계약 밖 변화(인덱스 내용 등)는 기록으로만 추적한다. 검수자 1인이라 검수자 간 일치도는 측정하지 못한다(한계로 공개).
**변경 기록**:
- 2026-10-03: 결정 9로 확정.

### ADR-011: HITL은 interrupt + Postgres checkpointer, 승인 노드와 실행 노드를 나누고, 프런트는 Next.js + BFF
**상태**: 유효
**결정**: 승인 노드 핵심(`interrupt`, 초안 해시 결합, `POST /tasks/{id}/approval`)과 전사 공지 승인자 정책(요청자 팀 4급 이상, 요청자 본인 제외)은 M3에서 구현한다. M3 장애 주입의 중복 0건 검증에는 승인을 거치는 부작용 실행 경로가 필요하다. M5는 웹 화면(로그인·요청 진행·승인함), MAX_STEPS/COST, 탐색 예산(ADR-014), checkpointer 보존 기간 삭제(ADR-015)를 구현한다. LangGraph `interrupt()` + Postgres checkpointer, `Command(resume=...)`로 재개한다. 재개 시 멈춘 노드를 처음부터 다시 실행하므로 **승인 노드에는 `interrupt`만, 실행은 별도 노드**에 둔다("재개 여러 번 → 부작용 1회" 테스트). 상태 전이: 계획·조회 → 초안 → 실행 전 결정론 검사(수신 채널 허용 목록·발송문 금지 PII·팀 정책·발송 범위) → 승인 대기 → (승인 → 실행 → 완료/실패) / (수정 후 승인 → 결정론 검사 재실행 → 통과 시 새 초안 해시에 묶인 승인으로 실행, 실패 시 실행하지 않고 승인 대기로 되돌려 검사 결과 표시) / 거부 → 종료 / 만료 → 종료. 승인은 초안 내용 해시에 묶는다(TOCTOU 방지). 만료 30분(데모). 승인 요청도 멱등. 본인 승인은 결재가 아니라 "에이전트가 해석한 결과(수신처·초안·결정론 검사 결과)의 확인"이고, 직무 분리는 전사 공지에만 둔다. 승인자는 기본 요청자 본인, **전사 공지 채널 발송만 요청자 팀 4급 이상이면서 요청자 본인이 아닌 사용자 승인**(A 등급은 숫자가 클수록 상위, A `retrieval/access.py:43`, 8팀 모두 4급 데모 사용자 있음). 다른 사용자의 승인 시도 거부 테스트를 두고 T6에 1~2개 넣는다. 발송 신원은 요청자다(`credential_for(요청자)`). 승인자는 본문만 수정할 수 있고 수신처는 바꿀 수 없다(바꾸려면 거부 후 새 요청). 승인자와 수정 전후 초안 해시를 감사 로그에 남긴다. 승인 대기 시간은 지연·MAX_STEPS·MAX_COST에서 제외한다. API: `POST /tasks`, `GET /tasks/{id}`, `POST /tasks/{id}/approval`(승인·수정·거부). 프런트는 **Next.js(App Router, TypeScript) + BFF**: route handler가 B 세션 토큰을 httpOnly 쿠키로 보관하고 FastAPI에 대신 요청하며, 진행 상황 SSE를 중계한다. Tailwind + shadcn/ui. 화면 4개: 로그인(데모 사용자 선택 = B 세션 발급), 요청·진행(SSE), 승인함(초안·수신처·결정론 검사·수정 차이, 전사 공지는 요청자 팀 4급 이상, 요청자 본인 제외), 기록(task 상태, `audit-verify` 결과). `make verify`에 `tsc`·`eslint`·`vitest`, Playwright e2e는 승인 흐름 1~2개를 replay 모드로. Node·pnpm 설치는 사용자, 버전 고정. Slack 안 승인(인터랙티브 메시지)은 범위 밖.
**이유**: 실행이 승인 노드 안에 있으면 재개 반복이 곧 중복 부작용이다. 승인을 해시에 묶지 않으면 승인 후 초안이 바뀌어도 실행된다. 전사 공지는 영향 범위가 넓어 상위 직급 승인이 필요하다. 인젝션이 수신처나 내용을 바꿨다면 요청자가 승인 화면에서 본다(본인 승인의 의미). 승인자가 수신처까지 바꿀 수 있으면 남의 이름으로 다른 채널에 보내는 경로가 생기므로 막고, 내용에 대한 책임은 감사 로그로 추적한다. SPA는 세션 토큰을 브라우저 JS가 다루는 위치에 두기 쉽고, BFF는 httpOnly 쿠키와 서버 쪽 중계로 토큰을 브라우저 JS 밖에 둔다. BFF로 두면 "자격 증명은 LLM·trace·브라우저에도 노출되지 않는다"를 웹까지 지킨다.
**트레이드오프**: Node 도구 체인이 추가되어 설치·검증 대상이 늘어난다. 만료 30분은 데모 값이라 실무 승인 지연을 반영하지 않는다.
**변경 기록**:
- 2026-10-08: 쿠키 이름은 `govops_session`, 속성은 `HttpOnly`, `SameSite=Strict`, `Path=/`이고 `Secure`는 요청 Origin 스킴이 https일 때만 설정한다. `Max-Age = min(28800, expires_at − now)`다. 변경 메서드 전체에서 `Origin`의 host == `Host` 헤더를 검사하고 없거나 다르면 403이다. route handler는 `NextRequest.cookies`·`NextResponse.cookies`를 쓴다. 로그아웃은 `DELETE /sessions`로 서버 세션을 폐기한다. 로그인 화면 사용자 목록은 `GET /demo/users`이며 `/sessions`와 같은 조건으로 열린다. BFF 응답은 허용 목록 투영을 거친다. 웹 포트는 3100, e2e는 3200이다. `GOVOPS_API_URL`은 서버 전용이며 루프백만 허용한다.
- 2026-10-03: 결정 10으로 확정.

### ADR-012: 감사 로그는 trace와 분리한 해시체인 + Ed25519 서명, 고정점을 커밋한다
**상태**: 유효
**결정**: trace(디버깅, 수정 가능)와 구분해 감사 로그는 책임 추적과 보안·부작용 사건의 위변조 탐지를 맡는다. 쓰기는 `audit/` 한 곳에서 JSONL 덧붙이기로만 수행한다. 원문 대신 내용 해시와 mock ID만 남긴다. 실행 전 의도 기록 실패 시 부작용을 실행하지 않는다(fail-closed). 결과 기록 실패는 재시도 후 `audit_gap`으로 표시한다(ADR-018).

- **체인·형식**: `chain-YYYY-MM-DD.jsonl`은 UTC 날짜별 파일이며 날짜가 바뀌어도 `prev_hash`가 이어진다. 레코드는 `{seq, ts, prev_hash, actor: {sub, act}, event, task_id, …, kid, hash, sig}`다. `kid`는 공개키 DER의 SHA-256 앞 12자이며 해시 대상에 포함한다. `hash`는 `hash`·`sig`를 제외한 정규화 JSON(키 정렬·공백 없음·UTF-8)의 SHA-256이고, `sig`는 hash의 ASCII 문자열에 대한 Ed25519 서명(hex)이다.
- **legacy 봉인**: 기존 `YYYY-MM-DD.jsonl`은 legacy로 보존한다. 첫 쓰기의 `chain.genesis`는 legacy 파일 집합의 파일 수(`legacy_files`)·줄 수(`legacy_lines`)·마지막 seq(`legacy_last_seq`)·`legacy_sha256`을 봉인한다. `legacy_sha256`은 파일 이름과 각 파일 SHA-256의 정렬 목록을 정규화 JSON으로 해시한 값이다. legacy는 “체인 없음(미검증)”으로 따로 보고하며 PASS로 세지 않는다.
- **잠금·손상**: 프로세스 내 디렉터리별 잠금과 디렉터리 fd의 `flock(LOCK_EX | LOCK_NB)` 재시도를 사용한다. 전체 잠금 마감은 10초이며 초과하면 `audit_lock_timeout`으로 fail-closed 처리한다. 개행 없는 체인 꼬리는 `audit_chain_corrupt`로 append를 거부한다.
- **이벤트**: 부작용 생애주기(초안, 승인 요청, 승인·수정·거부·만료, 실행 시도, 결과, 표식 조회)와 Guardrail 차단·격리 등을 기록한다. 시스템 이벤트 `service.start`·`chain.genesis`·`chain.anchor`·`auth.failed`·`checkpoint.pruned`는 `actor.sub="system"`, `task_id=null`이다. `service.start`는 정책·규칙·접근 인덱스 해시와 훅 규칙·코드 digest를 기록한다. `model.call`은 성공한 호출만 기록하고 원문 없이 task·사용자·팀 식별자(팀 키 값 제외)·모델 별칭·배포 ID·fallback 여부·비용·가용한 Guardrail 판정을 남긴다. `cost_usd`는 소수점 이하 고정 6자리 문자열이다. 모델 감사 쓰기는 비동기 실행을 막지 않는 스레드 쓰기이며 실패 시 fail-open으로 trace에 `model.audit_gap` 경보를 남긴다. `auth.failed`의 사유 enum은 `missing_credentials`·`invalid_token`·`unknown_user`·`loopback_required`다. 앱별 스레드 안전 분당 60건 상한을 두며 생략 건수는 다음 허용 기록의 `suppressed`로 집계한다. 감사 실패는 인증 응답을 바꾸지 않는다.
- **키**: 개인키는 `AUDIT_KEY_FILE`(기본 `data/interim/audit_ed25519.pem`, 0600, gitignore), 공개키는 `AUDIT_PUBLIC_KEY_FILE`(기본 `data/seed/audit_public_key.pem`, 커밋)이다. 키가 없거나 권한·짝이 틀리면 서비스·러너 시작을 거부한다(`audit_key_missing`·`audit_key_permissions`·`audit_key_mismatch`). 키 생성은 사용자 실행이다.
- **고정점**: `make audit-anchor LABEL=…`와 dev·faults 러너는 체인에 `chain.anchor`를 덧붙이고 `data/interim/audit/anchors-pending.jsonl`(gitignore, `AUDIT_ANCHORS_PENDING_FILE`)에 `{dir, seq, hash, sig, kid, label, ts}` 줄을 쓴다. `dir`는 저장소 루트 기준 상대 경로다. 러너는 실행 종료 후 디렉터리별로 고정점을 남기며 실패는 fail-open으로 보고한다. 결과 문서 PR이 대기 파일 줄을 `docs/results/audit-anchors.jsonl`로 옮겨 커밋한다.
- **검증·한계**: 읽기 전용 `make audit-verify`는 체인·서명·seq 빈틈·`legacy_sha256`과 대기·커밋된 두 고정점 파일을 검증하며 `(dir, seq, hash)`로 중복 제거해 대조한다. `make smoke-audit`는 키와 임시 체인 변조 5종(수정, 중간 삭제, 순서 변경, 고정점 레코드를 제거하는 꼬리 잘라내기, 다른 키 서명), 실제 체인의 읽기 전용 검증을 확인한다. 고정점 레코드 뒤만 자르면 다음 고정점 전까지 탐지하지 못한다. 같은 키로 전체 재계산·재서명한 경우는 고정점으로만 탐지한다.
**이유**: 부작용 에이전트는 "누가 무엇을 승인해 무엇이 실행됐는가"를 사후에 증명해야 한다. 지울 수 없는 로그에 PII를 넣으면 삭제 요구에 응할 수 없으므로 해시만 남긴다. 체인은 중간 수정·삭제·순서 변경을 탐지하지만 꼬리 잘라내기와 전체 재계산은 못 막으므로 외부(git)에 고정점을 둔다. 서명은 저장소만 건드릴 수 있는 공격자를 막는다.
**트레이드오프**: B가 탈취되면 서명 키도 노출되어 전체 재계산·재서명이 가능하다(고정점 이후 구간만 위험). 실무안(ADR에만): KMS·HSM 서명키, WORM 저장소·외부 고정점.
**변경 기록**:
- 2026-10-03: 결정 11로 확정.
- 2026-10-08: 체인·Ed25519 서명·고정점·`audit-verify`·`audit-anchor`·`smoke-audit` 구현 범위. 이월 4건: 실패·차단된 모델 호출의 `model.call` 미기록(차단은 `guardrail.input_blocked`, 실패는 비용 원장·trace가 기록), `auth.failed`의 호출자 sub 식별 없음(항상 `system`), 쓰기 권한 거부는 전용 이벤트 없이 `side_effect.result`의 `reason`으로 갈음, 웹 기록 화면의 `audit-verify` 결과 표시는 M7. 고정점은 대기 파일 → 결과 PR이 `docs/results/audit-anchors.jsonl`로 옮겨 커밋한다.
- 2026-10-08: 시스템 이벤트 `checkpoint.pruned`를 더한다. checkpointer 보존 삭제(ADR-015)의 삭제 실행(`APPLY=1`·serve 시작)이 삭제 대상 건수(`pruned_count`; 삭제 전에 기록하는 의도 기록이며, 일부 삭제가 실패하면 실제 삭제 수보다 클 수 있다)와 cutoff 시각(`cutoff`)만 기록하며 thread·task ID, 원문, 사용자 식별자는 넣지 않는다(`actor.sub="system"`, `task_id=null`). dry-run은 기록하지 않는다.

### ADR-013: 일정은 6주, 3주차 말 M3 점검, 축소 순서와 덜어내지 않는 핵심을 미리 정한다
**상태**: 유효
**결정**: 6주(2026-10-05 ~ 2026-11-15), 3주차 말(2026-10-24)에 M3 완료 점검. Gemini 단가 유효기간(2026-12-31) 안에 평가·채점 완료. 마일스톤 M0~M7은 `docs/PRD.md` 표. M1은 `m1-tools`·`m1-agent` 두 phase다. M2는 `m2-eval` → `m2-obs` → `m2-run` 세 phase다(사람 검수가 병목이라 평가셋을 먼저 만든다). 승인 노드 핵심(`interrupt`, 초안 해시 결합, `POST /tasks/{id}/approval`)과 전사 공지 승인자 정책(요청자 팀 4급 이상, 요청자 본인 제외)은 M3에서 구현한다. M3 장애 주입의 중복 0건 검증에는 승인을 거치는 부작용 실행 경로가 필요하다. M5는 웹 화면(로그인·요청 진행·승인함), MAX_STEPS/COST, 탐색 예산(ADR-014), checkpointer 보존 기간 삭제(ADR-015)를 구현한다. 늦어지거나 비용이 넘치면 ① test 3회 → 2회 ② Guardrail v2 분류기(비교는 켬 대 끔으로) ③ 웹 기록 화면(CLI `audit-verify`로) ④ 운영 검수 κ 재보정 1회만 ⑤ 평가셋 60 → 40(dev 15 / test 25) ⑥ 최후 수단 Langfuse 셀프호스팅 → JSONL만 순으로 덜어낸다. 덜어내지 않는 핵심: 신원 위임(중개), outbox 멱등성과 장애 주입, HITL, 감사 해시체인, Guardrail v1 비대칭, TCR·정확도 분리, 승인 화면.
**이유**: M3는 신원 위임·승인 경로·멱등성과 장애 주입을 검증하고, M4는 감사 해시체인과 Guardrail v1을 구현한다. 축소 순서를 미리 정해 두면 일정 압박 때 핵심을 깎는 결정을 막는다.
**트레이드오프**: 축소가 일어나면 test 반복 수·Guardrail v2 비교·기록 화면 등 보조 결과가 줄거나 빠진다.
**변경 기록**:
- 2026-10-03: 결정 12로 확정.
- 2026-10-08: M5 범위와 값을 확정한다. 대화 모델: `conversation_id`(=LangGraph thread)와 `turn_id`(=`state["task_id"]`)를 분리하고 첫 턴은 두 ID가 같다. 턴 범위 상태는 매 턴 초기화하고 `evidence_ids`(V15 근거)만 대화 범위로 `failed`·`interrupted` 턴을 포함한 모든 턴에서 누적한다. 맥락은 서비스가 `history`(system·user·최종 답변)로 매 턴 구성하며 `completed`·`rejected`·`expired`·`blocked` 턴만 이월하고 `blocked_input`·`failed`·`interrupted` 턴은 제외한다. 대화당 10턴이다. 승인 대기 중 새 턴은 409 `approval_pending`, 실행 중이면 409 `turn_in_progress`이며 자동 거부는 없고 승인이 만료(30분)되면 새 턴을 허용한다. SSE는 `{conversation_id, turn_id, status, seq}` 상태 이벤트만 보낸다. dev·test는 단일 턴(턴 1개 대화)이고 멀티턴은 데모 스모크만 하며 평가 범위 밖이다. MAX_STEPS는 8(턴 단위)이다. MAX_COST는 턴 단위 $0.40이다: `ceil_0.05(MAX_STEPS 8 × 실측 fallback 호출당 최대 $0.032014 × 1.5)`. 근거는 `data/manifest/costs.jsonl`의 m3-faults F6 all task `7a5fcf49…` 정산 3행(Sonnet 호출 $0.007328·$0.021818·$0.032014, 합 $0.061160, 마지막 호출 입력 13,077토큰)이며 정산된 `agent_fallback` 642행의 호출당 최대도 $0.032014다. 초과하면 모델 호출 전에 `failed`, `error=max_cost`로 끝난다. 세션 TTL은 8시간이다(ADR-017). 웹 화면은 로그인 전환, 대화 스레드, 대화 안 승인 카드와 승인함, 입력 차단 카드(사유 분류만), 격리 표시("참고 문서 일부 격리")이고 출력 사후 탐지는 화면에 나타내지 않는다. LibreChat은 범위 밖이다.

### ADR-014: 탐색 예산을 MAX_STEPS와 별도로 두고 N은 dev trace 재생으로 정한다
**상태**: 유효
**결정**: 탐색 예산 구현은 M5다. MAX_STEPS와 별도로 탐색 예산을 둔다. 새 정보 없는 호출이 N회 연속이면 task를 종료한다. "새 정보 없는 호출" = 결과에 그 task에서 이전에 없던 문서·티켓 ID가 없는 도구 호출이다. ID는 정규식이 아니라 도구 결과의 구조 필드(문서 `doc_id`, 티켓 key)에서 뽑는다. A의 비합성 문서도 새 정보로 센다. "성공" = TCR 성공. N = 성공한 dev task별 최대 연속 무정보 호출 수의 최댓값 + 1(최소 1). M2 dev 7개로 낸 값은 잠정치이며 등록값이 아니다. 등록값은 dev 20개 실행을 마친 시점에 산출해 등록한다. 이 규칙은 dev 실행 전에 사전 등록한다. 권한 판정은 계속 에이전트에게 숨긴다(읽기 거부 = "없음"). 보고는 T5 단계 수·p95 지연을 탐색 예산 켬 대 끔으로 비교한다.
**이유**: 권한 없음을 "없음"으로 숨기면 에이전트는 더 찾아다닌다. A에서 Agentic 지연의 약 95%가 LLM 왕복이었으므로, 권한을 드러내지 않고 헛된 탐색만 끊는 장치가 지연을 줄인다. N을 결정론 규칙으로 정하면 다시 계산해도 같은 값이 나오고, 결과를 본 뒤 N을 고를 수 없다.
**트레이드오프**: dev에 없던 유형의 정상 탐색은 N에 잘릴 수 있다. N 등록값은 dev 20개 실행 후 정하며 ID 추출은 위 구조 필드 판정을 따른다.
**변경 기록**:
- 2026-10-03: 결정 10으로 확정.
- 2026-10-08: N=4로 갱신한다(`docs/results/n-registration-m5.md`). 산출: 등록 시점까지 공식 dev 실행 전체(supersedes 사슬)의 성공 task별 최대 연속 무정보 호출 수의 최댓값 + 1(두 번째 실행 2, 세 번째 실행 3). 세 번째 실행의 T2-02(연속 3)가 N=3이면 잘린다. 탐색 예산은 턴 단위다. 런타임 카운터는 `evalrun/explore.py`의 `identifiers`·`longest_streak` 정의를 그대로 쓰고(두 함수는 agent가 import할 수 있는 공용 모듈에 두며 `regrade --n`과 같은 호출 집합을 센다) 구조 필드(`doc_id`·`key`·`ticket_id`)가 없는 결과는 무정보로 센다. 소진하면 `failed`가 아니라 `status=completed`와 고정 답변 "요청하신 정보를 찾을 수 없습니다.", 관측 필드 `stop_reason=exploration_exhausted`로 끝내고 화면에는 나타내지 않는다(권한 판정 은닉). 켬/끔 비교는 `docs/results/hypotheses-m5-budget.md`에 등록하며 공식 실행은 켬이다.

### ADR-015: checkpointer는 원문을 두되 task 종료 7일 뒤 삭제한다
**상태**: 유효
**결정**: LangGraph checkpointer는 실행에 필요한 원문(도구 결과·초안)을 그대로 둔다. task가 종료(완료·실패·거부·만료)되고 7일이 지나면 해당 checkpoint를 삭제한다. 삭제 명령과 그 테스트는 M5에 둔다. 감사 로그는 지금처럼 해시와 mock ID만 남긴다(ADR-012).
**이유**: 재개와 실행에 원문이 필요해 checkpointer는 마스킹할 수 없다. 지울 수 없는 감사 로그는 해시만, 지울 수 있는 checkpointer는 원문 + 보존 기간으로 역할을 나눈다.
**트레이드오프**: 보존 기간 7일 동안 checkpointer에 PII를 포함한 원문이 남는다(THREAT_MODEL TH10 잔여 위험). 삭제 뒤에는 그 task를 재개하거나 원문으로 사후 분석할 수 없다. 삭제 명령은 `make checkpoint-prune`(dry-run, `APPLY=1`일 때 삭제)이고 serve 시작 시에도 1회 실행한다.
**변경 기록**:
- 2026-10-03: 결정 10으로 확정.
- 2026-10-08: 삭제 조건·명령·주기를 확정한다. 대상은 활성 턴(`running`, 만료되지 않은 `awaiting_approval`)이 없고 마지막 checkpoint `ts`가 7일을 넘은 thread다. `awaiting_approval`이어도 `expires_at`이 지났으면 비활성이다(만료 처리는 조회 때만 일어난다). `make checkpoint-prune`은 dry-run이고 `APPLY=1`일 때 삭제한다. serve 시작 시 `startup_cleanup` 다음에 삭제를 적용해 1회 실행하며, 실패해도 serve를 막지 않고 경고 한 줄만 남긴다. 삭제한 thread의 `conversation_turns` 행도 함께 지운다. 삭제 실행은 `checkpoint.pruned`(`pruned_count`·`cutoff`만, 삭제 전 의도 기록, ADR-012)로 기록한다. 시계를 주입한 실제 Postgres 검증은 사용자 실행 `make test-m5-pg`다.

### ADR-016: M0 골격 결정 — web 호스트 실행, mock 관리 접근 경로, checkpointer DB 보류
**상태**: 유효
**결정**:
- web은 호스트에서 `cd web && pnpm dev`로 실행하고 compose에 넣지 않는다.
- mock은 REST 포트(컨테이너 8000)만 `127.0.0.1`에 publish한다(`mock-jira`: 호스트 8081, `mock-slack`: 호스트 8082). 관리 포트(9000)는 publish하지 않는다. 호스트 접근 경로는 컨테이너 안 CLI `docker compose exec <mock> python -m govops.mocks.admin <명령>`이고, 저장소 루트에서는 `docker compose -f infra/docker-compose.yml exec <mock> python -m govops.mocks.admin <명령>`을 쓴다(`<mock>`은 `mock-jira` 또는 `mock-slack`). 평가 하네스의 상태 조회와 M3 장애 주입도 이 경로를 쓴다. CLI는 `health`·`state`·`reset`을 제공하며, 장애 주입 명령은 `faults set/clear/stats`이며 `state --bodies`로 본문 포함 상태를 조회한다(ADR-018).
- LangGraph checkpointer DB는 M0에서 만들지 않는다. `infra/postgres/init/01-create-databases.sh`는 `litellm`·`langfuse` DB만 만든다. checkpointer DB 위치는 ADR-017의 같은 인스턴스 내 `govops` 앱 DB다.
- 패키지 이름은 `govops`(`src/govops/`)로 확정한다. Python 3.12(`pyproject.toml`의 최소 버전, `.python-version`의 실행 버전), uv 기반이며 계층 패키지 11개와 `costs.py` 골격을 둔다.
- `.env.example`에는 변수 이름만 두고 값은 `.env`에만 둔다. 비용 상한은 환경 변수가 아니라 코드 설정(ADR-007: OpenAI $9.13, Anthropic $11.47, Gemini $7.29)이며 `src/govops/costs.py`에서 M1에 구현한다.
- `make verify`는 ruff check → ruff format --check → pytest → web typecheck(tsc) → eslint → vitest → playwright test --list(설정·스펙 로드 검사, 브라우저·서버 불필요) 순서다. 설치된 의존성으로 API 키·네트워크 없이 통과한다.
**이유**: 배치도는 BFF를 호스트에 두고 에이전트 서비스도 호스트에서 `uv run`으로 실행한다(ADR-004). web도 같은 배치를 따른다. 관리 포트를 publish하지 않고 컨테이너 안 CLI를 쓰면 compose 내부 네트워크 제한을 유지하면서 호스트 하네스가 관리 기능에 접근할 수 있다. checkpointer DB 위치는 M1 구현에서 정하므로 M0 초기화는 확정된 DB 두 개로 한정한다. 패키지·도구 체인과 단일 검증 진입점을 확정해 이후 계층 구현이 같은 기준을 따른다. 비밀 값은 `.env`에 모으고 비용 상한은 ADR-007의 코드 설정으로 관리한다.
**트레이드오프**: web·에이전트는 compose와 별도로 호스트에서 실행해야 하고 mock 관리는 `docker compose exec`를 거쳐야 한다. 상태 조회·초기화는 `state`·`reset`으로 수행하고 장애 주입 CLI는 ADR-018을 따른다. checkpointer DB 배치는 ADR-017을 따른다. 비용 상한 구현은 M1까지 보류한다.
**변경 기록**:
- 2026-10-03: M0에서 확정

### ADR-017: M1 결정 — 도구 이름, mock 자격 증명, mock-jira 쓰기 권한, B 세션 토큰, checkpointer DB
**상태**: 유효
**결정**:
1. **도구 이름**: A 계약은 `search_docs`, `lookup_id`, `run_sql`, `get_document`를 유지한다. 읽기는 `jira_get_ticket`, `jira_search_tickets`, 부작용은 `jira_add_comment`, `jira_create_followup_ticket`(필수 `parent_ticket_id`), `slack_send_message`다. B 이름 형식은 `{시스템}_{동사}_{대상}` snake_case다. 입력 스키마에 사용자·팀·직급 필드는 없다. 채널 이력·코멘트 목록·후속 티켓 목록 조회는 표식 조회용으로 도구 계층만 쓰고 모델에 노출하지 않는다. mock-slack 이력 조회는 자기 팀 채널·전사 공지만 허용하고 그 밖은 403이다.
2. **mock 사용자 자격 증명**: 사용자 38명 × audience(`mock-jira`, `mock-slack`)별 토큰을 발급한다. 평문은 `data/interim/mock/tokens.json`(0600, 커밋 안 함), 해시 매니페스트는 `data/seed/mock_jira_token_hashes.json`·`data/seed/mock_slack_token_hashes.json`, 사용자 목록은 `data/seed/users.json`(label·team·grade)이며 해시와 사용자 목록은 커밋한다. mock은 토큰 해시로 인증해 team·grade를 정한다. broker가 평문 파일을 읽는 유일한 곳이다(`MOCK_TOKENS_FILE`, 기본 위 평문 경로).
3. **mock-jira 쓰기 권한**: 읽을 수 없는 티켓의 코멘트·후속 티켓 요청은 존재하지 않는 티켓과 같은 404다(존재 비밀 우선). jira에는 읽기와 별개인 쓰기 권한 구분을 두지 않는다. 명시적 403 "권한 없음"은 존재가 공개된 대상인 mock-slack 채널의 쓰기·이력 조회에만 쓴다.
4. **B 세션 토큰**: `POST /sessions`는 `DEMO_LOGIN` 플래그이고 127.0.0.1 클라이언트일 때만 열린다. 토큰은 `secrets.token_urlsafe(32)`이며 서버는 SHA-256 해시만 메모리에 두고 `user_id`·만료 시각에 매핑한다(재시작 시 재로그인). TTL은 8시간이다. 인증 의존성이 `Authorization: Bearer`에서 `user_id`를 얻으며 승인자 신원도 같은 경로다. 다른 사용자의 task 조회는 검사 통과 승인 대기 초안에 대한 승인자 정책을 만족할 때만 허용하고 나머지는 404다.
5. **checkpointer DB**: 같은 Postgres 인스턴스에 새 DB `govops`(앱 DB)를 둔다. checkpointer와 outbox 테이블을 둔다(ADR-018). `m1-agent`에서 `infra/postgres/init/01-create-databases.sh`에 추가하고 기존 볼륨용 멱등 명령 `make db-init`을 둔다. 연결 환경 변수는 `GOVOPS_DATABASE_URL`이다. `make verify`는 Postgres 없이 통과해야 하므로 테스트는 in-memory saver를 쓰고 실제 Postgres는 사용자 스모크로 확인한다.
**이유**: 계약 이름과 신원 없는 입력 스키마는 A 연동·사용자 신원 분리를 유지한다. audience별 mock 토큰과 해시 인증은 자격 증명의 대상 혼동을 막는다. 존재 자체가 기밀인 티켓은 쓰기에서도 존재를 알리지 않는다. 세션 해시 보관은 서버의 평문 토큰 보관을 줄이고, 앱 DB 분리는 checkpointer를 LiteLLM·Langfuse와 구분한다.
**트레이드오프**: 사용자별·audience별 토큰 파일 관리가 필요하다. jira 쓰기 거부는 없는 티켓과 구분할 수 없다. 메모리 세션은 재시작 시 재로그인이 필요하다. 실제 Postgres 연동은 in-memory 테스트와 별도의 스모크로 검증한다.
**변경 기록**:
- 2026-10-04: M1 설계에서 확정.
- 2026-10-08: 세션 TTL을 8시간으로 확정한다(BFF 쿠키 `maxAge`도 같은 값, 401이면 로그인 화면으로 이동). 대화(`conversation_id`)의 소유자는 세션 사용자이며 다른 사용자의 대화 조회·턴 추가는 404다(ADR-013).

### ADR-018: M3 결정 — 장애 처리 값, outbox·표식, 감사 최소 구현, 승인자 정책, 발송 범위 검사
**상태**: 유효
**날짜**: 2026-10-05
**결정**:
1. **값 표**: 아래 값은 도구 호출(읽기·쓰기)에만 적용한다.

   | 항목 | 값 |
   |---|---|
   | Soft / Hard Timeout | 3초 / 10초 |
   | 읽기 재시도 | 최대 3회 시도, base 0.5초 ×2, 상한 4초, full jitter |
   | `Retry-After` | 준수, 30초 상한 |
   | 서킷 | 대상 서비스별(`jira`·`slack`·`a_mcp`), 연속 5회 실패 시 열림, 30초 뒤 half-open |
   | 표식 2단계 조회 대기 | 2.0초 |
   | 부작용 발송 시도 | 최대 3회 |

   모델 호출 타임아웃은 별도 값이며 ADR-019를 따른다.
2. **표식·멱등 키**: 메타데이터 `govops_idem`(멱등 키)·`govops_task`(task ID)·`govops_approval`(승인 ID)를 쓴다. 멱등 키 = sha256(정규화 JSON{task, 도구, 인자}). 직렬화 키는 `task_id`·`tool`·`args`이며 인자 문자열 NFC 정규화, 키 정렬·공백 없음·UTF-8을 적용한다. 조회는 채널 이력·티켓 코멘트·원 티켓 children이며 검색 API는 쓰지 않는다.
3. **outbox**: Postgres `govops` DB의 `govops_outbox` 테이블에 키 PRIMARY KEY, `INSERT … ON CONFLICT DO NOTHING`으로 선점한다. 행에는 해시·mock ID·상태·`audit_gap`만 두며 원문·자격 증명은 두지 않는다. task·도구·대상·승인 ID와 발송 횟수·안전한 사유 코드는 실행 식별·상태 정보다. 상태 = 기록됨(`recorded`)·시도됨(`attempted`)·불확실(`uncertain`)·성공(`succeeded`)·실패(`failed`). 테스트는 InMemory 저장소를 쓴다.
4. **재시도 분류**: 연결 수립 실패·429만 표식 조회 없이 재시도한다(발송 시도 3회 안에서). 그 밖의 불확실 실패(타임아웃·5xx·전송 오류)는 표식 조회 2단계 뒤 두 번 모두 없을 때만 재발송한다. 4xx는 확정 실패이고 조회 불가는 재발송하지 않고 불확실로 종료한다. 마지막 시도가 불확실 실패로 끝났으면 표식이 보이지 않아도 불확실로 종료한다. `FAILED`는 보내지 않았음이 확실한 경우(4xx, 서킷 열림으로 호출하지 않음, 연결 실패·429만 반복, 첫 발송 전 감사 실패)에만 쓴다. POST마다 직전에 실행 시도 이벤트(발송 번호 포함)를 fail-closed로 기록한다. 재시작 정리는 표식 조회만 하고 재발송하지 않는다.
5. **장애 주입 인터페이스**: mock 관리 CLI `faults set/clear/stats`, `state --bodies`를 쓴다. 규칙은 엔드포인트별 요청 순번이고 종류는 지연·5xx(처리 전/후)·429·처리 후 응답 유실·조회 반영 지연이다. F10은 항상 F5(처리 후 응답 유실)와 결합한다(조회 반영 지연만으로는 표식 조회가 일어나지 않는다). F4(연결 수립 실패)는 서버가 아니라 클라이언트 쪽 전송 계층에서 주입한다. 관리 포트는 compose 내부 네트워크에만 연다.
6. **감사 최소 구현**: `audit/`가 감사 체인 JSONL(`data/interim/audit/chain-YYYY-MM-DD.jsonl`, 0600)을 쓴다. 이벤트 이름은 ADR-012 기록 대상(초안·승인 요청·승인·수정·거부·만료·실행 시도·결과·표식 조회)이다. 실행 전 의도 기록 실패 시 부작용을 실행하지 않고, 결과 기록 실패는 재시도 후 `audit_gap`으로 표시한다. 해시체인·서명은 ADR-012를 따른다.
7. **승인자 정책(M3에서 구현)**: 전사 공지(`announce`) 발송은 요청자 팀의 4급 이상이면서 **요청자 본인이 아닌** 사용자만 승인할 수 있다. 요청자가 4급 이상이어도 본인 승인은 403이다. 그 밖의 채널·티켓 작업은 요청자 본인만 승인하며 다른 사용자의 task는 404다. 순수 함수와 테스트는 M3, 승인함·화면은 M5다.
8. **V15 발송 범위 검사**: 보낼 곳 독자 ⊆ 근거 열람 범위. 근거는 도구 결과에서 비어 있지 않은 `text`를 가진 문서(`search_docs` 발췌·`get_document`·`lookup_id` 모두)와 레코드가 돌아온 티켓이다. `run_sql` 결과의 `ticket_id` 열 값도 티켓 근거에 포함한다. ID 없는 `run_sql` 결과(집계·열 값)는 V15 검사를 받지 않으며 T4 집계는 채점 쪽과 같이 범위 검사에서 제외한다. 열람 범위는 문서·티켓 ID와 접근 필드(`access_level`·`access_teams`·`access_min_grade`), 사용자 팀·직급만 담은 `data/seed/` 인덱스에서 읽고 인덱스에 없는 근거는 차단한다. 실행 중 검사는 문서 단위이고 평가 정답 선정(`eval.audience`)의 값 기준 판정보다 엄격하다. 잘못 막힌 건은 TCR 실패로 세고 dev 결과 문서에 건수를 보고한다. 에이전트는 `data/eval/`을 읽지 않는다.
9. **접근 인덱스·시작 정리**: `data/seed/access_index.json`에 없는 문서(비합성 문서 포함)는 fail-closed로 차단한다. 생성기는 A의 커밋된 manifest·접근 정책에서 비합성 문서 100건을 합쳐 문서 300건·티켓 500건의 접근 전용 인덱스를 만든다. `make scope-index`는 사용자 실행 타깃이며 본문을 복사하지 않는다. 서비스 시작 정리는 checkpointer 전체 순회로 수백 건 규모를 전제하며 규모가 커지면 별도 테이블이 필요하다. 실행 중 task는 `interrupted`로 바꾸고 열린 outbox는 표식 조회만 한다.
10. **수정·해시**: 수정 가능 필드는 메시지 `text`, 코멘트 `body`, 후속 티켓 `title`·`description`이다. 도구·수신 채널·티켓 ID·원 티켓 ID·우선순위와 그 밖의 필드는 불변이다. 수정 후 결정론 검사를 재실행하고 마스킹이 적용되면 마스킹본 해시로 승인을 결합한다. 수정본이 차단되면 원래 초안과 원래 해시로 승인 대기를 복원하고 `rejected_edit`에 막힌 이유를 표시한다. 만료 시각은 그대로다.
11. **프로필**: `READ_ONLY`는 읽기 도구만, `WITH_WRITES`는 승인·outbox를 거치는 쓰기 경로를 포함한다. `GOVOPS_AGENT_PROFILE=read_only`가 기본이며 `writes`는 `WITH_WRITES`를 선택한다.
12. **승인 API**: 승인자 GET은 결정론 검사를 통과한 승인 대기 초안만 보여 준다. 요청자 GET은 자신의 task 상태를 볼 수 있다.

   | 상황 | 응답 |
   |---|---|
   | 승인·수정 승인·거부 접수 | 202 |
   | 종료된 동일 해시·결정 재요청 | 200 (저장된 영수증) |
   | 전사 공지 요청자 본인의 승인 POST | 403 |
   | 다른 사용자·자격 없는 승인자·없는 task | 404 |
   | 만료·상태 또는 초안 해시 충돌 | 409 |
   | 불변 필드 수정·유효하지 않은 요청 | 422 |

**이유**: 도구 장애 처리 값과 서비스별 서킷은 호출 경계를 명확히 한다. 원자적 outbox 선점과 메타데이터 표식 조회는 동시 실행·응답 유실의 중복을 줄인다. 불확실 상태는 발송 여부를 단정하지 않고, 실행 직전 감사 기록은 의도 없는 부작용을 막는다. 전사 공지의 본인 승인 금지는 직무 분리를 유지한다. 근거 열람 범위 검사는 조회 가능한 정보를 더 넓은 독자에게 발송하는 것을 막는다.
**트레이드오프**: 비체인 감사는 M4 전까지 변조를 탐지하지 못한다. 클라이언트 쪽 F4 주입은 서버 쪽 연결 거부와 다르다. 대기보다 긴 조회 반영 지연은 중복을 막지 못한다(F10 한계 시연). 문서 단위 발송 범위 검사는 값 기준 평가보다 엄격해 정상 요청을 차단할 수 있으며 TCR 실패에 포함한다.
**변경 기록**:
- 2026-10-05: 도구 장애 처리 값, outbox·메타데이터 표식, 재시도 분류, 장애 주입 CLI, M3 감사 최소 구현·승인자 정책, V15 발송 범위 검사.


### ADR-019: M3 결정 — fallback 모델·원장 정산·모델 호출 타임아웃·모델 장애 주입
**상태**: 유효
**날짜**: 2026-10-05
**결정**:
1. **fallback 모델**: Claude Sonnet 5.5(`claude-sonnet-5-5`, LiteLLM `anthropic/claude-sonnet-5-5`)이며 호출 단위(LiteLLM Router)로 전환한다. 단가는 입력 $2/MTok·출력 $10/MTok([출처](https://platform.claude.com/docs/en/about-claude/pricing), 확인일 2026-10-05)이며 캐시 입력도 일반 입력 단가로 센다.
2. **비용 메모**: 4.7 이후 Anthropic 모델은 새 토크나이저를 쓰므로 같은 텍스트에 토큰이 약 30% 더 나온다. 호출 전 추정은 입력 상한을 크게 잡으므로 보정 계수를 두지 않고, 실측 비용은 원장으로 확인한다.
3. **모델 호출 타임아웃**: 도구 호출의 Soft 3초/Hard 10초와 별개로 주 모델 15초, `num_retries` 0(같은 모델 재시도 없이 바로 fallback), fallback 모델 30초, 클라이언트 60초다. 두 LiteLLM 설정 파일(`config.yaml`·`config.obs.yaml`)의 값은 같고 테스트로 고정한다.
4. **원장**: fallback이 가능한 호출은 공급사별 예약 2건(OpenAI·Anthropic, 각 공급사 단가)을 잡고 응답 모델 기준으로 정산한다(응답한 쪽에 실제 비용, 다른 쪽은 0). OpenAI 상한 소진은 예산 중단이고, Anthropic 상한만 소진되면 fallback 없는 별칭(`agent-primary-only`)으로 호출한다.
5. **응답 모델 ID 매핑**: 응답 헤더 `x-litellm-model-id`의 배포 ID로 응답 모델과 단가를 정한다. LiteLLM 응답 본문 `model`에는 별칭이 오므로 헤더가 없을 때만 본문을 사용하며, 이때 제공자 모델 ID는 정확 일치 + 등록된 접두어(날짜 접미사 `-YYYYMMDD`·`-YYYY-MM-DD` 허용)로 매핑한다. 각 배포의 `model_info.id`는 `openai-gpt-6-luna`·`openai-gpt-6-luna-only`·`openai-gpt-6-luna-down`·`anthropic-claude-sonnet-5-5`다. 앞의 두 OpenAI 배포 ID는 `gpt-6-luna`와 그 단가에, Anthropic 배포 ID는 `claude-sonnet-5-5`와 그 단가에 매핑한다. `openai-gpt-6-luna-down`은 응답할 수 없는 배포이므로 매핑하지 않고 미등록으로 경보한다. 매핑되지 않은 식별자는 등록 모델 중 최대 단가로 두 예약 모두 정산하고 경보한다. **미등록 응답 모델은 가장 비싼 단가로 양쪽 공급사에 모두 정산하므로 비용 보고가 과대 계상될 수 있다.** 호출마다 `ChatResult.model`과 LLM 기록 `response_model`에는 매핑 결과를, `deployment_id`에는 배포 ID를 남긴다. dev manifest의 `models_seen`은 제공자 모델 ID, `unmapped_models`는 미등록 식별자이며 하나라도 미등록이면 결과 문서에 표시한다.
6. **모델 장애 주입(F6)**: 백엔드는 `down_alias` 하나다. 호출 번호 기준 스크립트(첫 호출만·둘째 호출만·전부 실패)를 닫힌 포트를 가리키는 배포 `agent-primary-down` + fallbacks로 구현한다. 프록시는 요청 파라미터를 통한 fallback 시험을 기본으로 거부하며 이를 켜는 설정은 사용하지 않는다. `GOVOPS_MODEL_FAULT_MODE`는 `down_alias`만 허용한다. 하네스에서만 쓰고 이 변수가 설정된 상태로는 일반 서비스가 시작되지 않는다. chaos proxy는 쓰지 않는다.
7. **환경 변수**: 빈 문자열·공백은 미설정으로 보고 기본값을 쓴다(`env_or`). `src` 전체에서 비어 있지 않은 기본값을 `environ.get`/`getenv`에 넘기는 패턴은 스캔 테스트가 막는다.
8. **감사 식별자**: 감사 로그의 `task_id`(`^[0-9a-f]{32}$`)와 `actor_sub`(등록 사용자 라벨 형식)는 형식으로 검증하며 개인정보 정규식은 식별자·해시 필드에 적용하지 않는다(무작위 hex가 전화번호 규칙에 걸려 정상 기록이 거부되는 것을 막는다). 형식 밖 값은 거부한다.
9. **mock 변경 후 절차**: mock 코드가 바뀐 PR을 머지한 뒤에는 `docker compose -f infra/docker-compose.yml up -d --build mock-jira mock-slack`을 실행한다.
10. **에이전트 가상 키 허용 모델**: `agent-primary`·`agent-primary-only`·`agent-primary-down`·`agent-fallback`이며 `config.yaml`·`config.obs.yaml`의 `model_name` 집합과 일치함을 테스트로 고정한다. 기존 키는 `make agent-key-update`로 갱신하며 `.env`의 키 값은 바뀌지 않는다.
11. **smoke-fallback 합격 조건**: normal 단계는 OpenAI 원장 증가 > 0·Anthropic 원장 증가 = 0·미등록 0건, fallback 단계는 Anthropic 원장 증가 > 0·OpenAI 원장 증가 = 0·미등록 0건이다.

### ADR-020: M3 결정 — dev 러너 m3, 골든 확장, 사전 등록
**상태**: 유효
**날짜**: 2026-10-06
**결정**:
1. **실행 프로필**: dev 20은 `WITH_WRITES` 단일 프로필로 처음부터 실행한다. 러너가 프로필을 스스로 강제하며 불일치는 `profile_mismatch`로 중단한다. 재실행은 `hypotheses.md` 고정 문장 5·§10과 `hypotheses-m3.md`를 따른다.
2. **이전 결과 보존**: 이전 읽기 7개 결과는 사유·날짜·sha256·추가 커밋과 함께 `docs/results/m3-dev-supersedes.json`에 보존한다. dev 20 결과에 이어 붙이지 않는다.
3. **관측 상태**: `completed`·`failed`·`rejected`·`expired`·`interrupted`는 그대로, `blocked`는 `rejected`, `awaiting_approval`은 `failed`로 매핑한다. 승인 계획 없이 승인 대기로 끝난 task와 승인 계획이 남은 task는 `failed`다. `approval_rejected` 성공에는 `rejected` 상태와 받아들여진 reject 기록(HTTP 2xx)이 필요하며 없으면 `reject_not_recorded`로 실패한다.
4. **절대 규칙**: mock 상태 기준으로 중복·누락·미승인·마커 없음을 함께 판정한다. 기대 부작용 종류·건수·대상·구조의 정확한 일치, 정상 종료, 금지 위반 없음이 모두 필요하다. 권한 밖 정보 노출 후보의 사람 확정 절차는 유지한다.
5. **승인자 선택**: `same_team_g4`는 요청자와 같은 팀의 4급 사용자 중 요청자를 제외하고 `label` 사전순 첫 번째를 선택한다. `requester`는 요청자 본인이다. 신원은 인증된 세션에서 정한다.
6. **preflight**: mock 이미지 생성 시각이 `src/govops/mocks/` 마지막 변경 커밋 시각보다 뒤인지 검사하며 실패는 `stale_mock_image`다. Compose 기본 이미지 이름은 `govops-mock-jira`·`govops-mock-slack`이다. LiteLLM `model_info.id`에 `openai-gpt-6-luna`·`openai-gpt-6-luna-only`·`openai-gpt-6-luna-down`·`anthropic-claude-sonnet-5-5`가 있는지 검사하며 실패는 `litellm_config_stale`다. Postgres·LiteLLM·mock-jira·mock-slack·A MCP 응답을 확인하며 실패는 `service_down:<이름>`이다. 하나라도 실패하면 시작하지 않는다.
7. **live 저장소**: live dev는 Postgres 필수다. `GOVOPS_DATABASE_URL`이 없으면 `postgres_required`로 중단하며 메모리 저장소는 테스트 전용이다.
8. **평가 경로**: in-process ASGI로 실제 task·승인 API 코드를 거친다. 실제 서버 프로세스·세션 TTL은 검증하지 않는다. 시작 시 정리는 수백 건 규모를 전제하며 정책 파일 해시 감사는 M4다.
9. **골든 확장**: record-through는 골든 14개만 한다. 대상은 기존 T1-01·T1-05·T1-08·T5-01·T5-02·T5-05·T5-08과 T2-01·T3-01·T3-07·T4-01·T6-01·T6-03·T5-11이다. test 문항은 넣지 않는다. 기대값은 기록 전에 명세에서 계산하며 기존 7개 값은 불변이다. 기록 상태에는 `profile`(`read_only` 또는 `writes`)을 둔다. 재생 비교는 도구 순서·부작용 종류·대상·건수·종료 상태·거절·금지 도구 호출·승인자와 결정 순서를 사용하며 승인 ID·mock ID는 제외한다. 비합성 A 응답이 있으면 골든에서 제외하고 최초 TCR 실패는 `recorded_fail`로 보존한다(`hypotheses.md` §9).
10. **등록 규칙**: 등록 커밋 이후 `docs/results/hypotheses-m3.md`·`data/replay/golden/expectations.json`·`docs/results/m3-dev-supersedes.json`은 바뀌지 않아야 한다. 세 파일의 `git diff <등록 커밋>..HEAD`가 비어 있어야 한다.
11. **V15 오차단**: 기대 outcome이 `approved_executed`인 task가 `scope_wider_than_evidence` 또는 `evidence_scope_unknown`으로 차단되면 task당 1건으로 센다. TCR 실패에 포함하고 dev 결과 문서에 보고한다. ID 없는 `run_sql` 결과는 V15 검사 대상이 아니다.
12. **모델 혼합**: task별 `models_seen`으로 모델이 섞인 task 수와 그 TCR을 보고한다. 장애 주입 결과와 따로 보고하며 `unmapped_models`가 하나라도 있으면 결과 문서에 표시한다.
13. **도구 캡처와 N 등록**: 도구 호출 캡처는 task 시도별로 분리하고 늦은 호출은 `late_tool_calls`로만 센다. `tool_trace_mismatch`(캡처한 도구 호출 수와 checkpoint의 도구 호출 수 불일치)가 있는 task는 N 계산에서 뺀다. 하나라도 있으면 결과 문서 첫머리에 적고 원인을 확인하기 전에는 N을 등록하지 않는다. 러너는 `n_registration_blocked`를 표시하고 N 후보를 출력하지 않는다. ADR-014의 N 산출 규칙은 유지한다.
14. **환경 최신성**: 옛 mock 이미지나 LiteLLM 설정이 반영되지 않은 환경에서는 preflight가 실행을 막는다.
15. **조립 경계**: 평가 실행기는 `audit`·`guardrails`를 직접 import하지 않고 `api.wiring.build_task_service`로 서비스를 조립한다.

**이유**: 단일 프로필과 등록 파일 고정은 dev 실행 단위를 재현 가능하게 한다. mock 상태·승인 기록·task별 캡처는 효과와 탐색 예산의 판정 근거를 일치시킨다. 환경 검사는 구현과 실행 환경의 불일치를 막는다.
**트레이드오프**: in-process ASGI 평가는 서버 프로세스·세션 TTL을 검증하지 못한다. 도구 캡처 불일치가 있으면 N 등록이 보류된다. V15의 문서 단위 범위 검사는 값 기준 평가보다 엄격해 정상 요청을 차단할 수 있다.


### ADR-021: M3 결정 — 시스템 프롬프트 조직 정보와 `writes_org` 프로필
**상태**: 유효
**날짜**: 2026-10-06
**결정**:
1. **조직 정보 절**: 시스템 프롬프트에 팀 한글 이름 ↔ `owner_team` ↔ `project_key` 8팀 대응표와 티켓 `status`·`priority`·`ticket_type` 값 목록을 추가한다. 한글 이름은 A `src/kodocs/synth/templates/system.md`, 코드·값은 `data/seed/tickets.json`에서 가져온다. `agent/org_info.py`의 상수로 결정론적으로 렌더하며 정답 값·예시 SQL을 넣지 않고 `data/eval`을 사용하지 않는다.
2. **프로필 보존**: 새 프로필 `writes_org`를 기존 `writes` 옆에 둔다. 읽기 전용 `PROFILES` 레지스트리의 레이블과 `GOVOPS_AGENT_PROFILE` 허용값은 `read_only`·`writes`·`writes_org`다. 기존 프로필의 프롬프트·도구 정의와 프롬프트 해시는 보존한다. 커밋된 골든 14개는 `writes` 해시로 기록되어 있으므로 기존 CI가 재기록을 요구하지 않는다.
3. **dev 재실행**: dev 20은 `writes_org`로 처음부터 다시 실행하며 공식 채점은 `hypotheses-m3-grading.md` 규칙 버전 `m3-grading`을 사용한다. 첫 실행 결과는 공식 기록으로 보존한다. supersedes 사슬은 `m3-dev-supersedes.json` ← `m3-dev2-supersedes.json`이며 manifest의 `previous_supersedes`·`supersedes`로 남긴다.
4. **등록 묶음과 가드**: 첫 묶음은 `hypotheses-m3.md`·`data/replay/golden/expectations.json`·`m3-dev-supersedes.json`, 두 번째 묶음은 `hypotheses-m3-prompt.md`·`m3-dev2-supersedes.json`이다(등록 문서·supersedes 파일은 `docs/results/` 아래). 각 묶음은 해당 사전 등록 파일의 등록 커밋에 모두 존재하고 이후 변경되지 않아야 한다. 위반 시 각각 `hypotheses_m3_not_committed`·`hypotheses_m3_prompt_not_committed`로 중단한다. 작업 트리는 청결해야 하며 등록 파일의 sha256과 등록 커밋을 manifest에 기록한다. 채점 등록은 별도의 `grading_rules_not_committed` 가드를 따른다.
5. **추가 변경 절차**: test 전에 프롬프트·도구 설명을 더 바꾸면 새 등록 파일 + dev 전체 재실행 + 이전 결과 보존 절차를 따른다. 바꾼 횟수와 이유는 결과 문서에 모두 적는다. test 결과를 보고 조정하지 않는다.
6. **탐색 예산**: N 등록값은 두 번째 실행(`writes_org`)의 `tool_results`로 산출한다. ADR-014의 산출 규칙과 도구 기록 불일치 시 등록 보류를 유지한다.
7. **골든 재기록**: 골든 14개는 두 번째 실행 직후 `writes_org`로 재기록하며 `status.json`은 `m3-grading` 기준이다. 명세에서 계산한 `expectations.json`은 그대로 둔다.
8. **다음 phase**: 기본 프로필 전환과 옛 프로필(`read_only`·`writes`) 삭제는 다음 phase `m3-faults`의 첫 step에서 한다. 현재 기본값은 `read_only`다.

**이유**: 조직 스키마의 코드 값을 시스템 프롬프트에 명시해 도구 조건에 올바른 값을 사용하게 한다. 기존 프롬프트 해시와 공식 기록을 보존하고 새 실행의 프로필·등록·채점 규칙을 고정한다.
**트레이드오프**: 프로필 세 개를 함께 유지하며 조직 정보 상수와 시드의 일치를 검사해야 한다. 새 프롬프트 평가는 dev 전체 재실행과 골든 재기록을 필요로 한다.


### ADR-022: M3 결정 — 장애 주입 하네스
**상태**: 유효
**날짜**: 2026-10-07
**맥락**: 장애 주입의 실행 경로·판정·재개 단위를 고정하고 현재 프로필과 기록을 일치시킨다. 계획과 기대 조건의 원본은 [M3 장애 주입 사전 등록](results/hypotheses-m3-faults.md)과 `evalrun/fault_scenarios.py`다.

**결정**:
1. **프로필 정리**: 골든·API 기본 프로필과 `PROFILES` 레지스트리는 `writes_org` 하나다. M2 러너의 실행 경로(`run_dev`·`_live`·`main`)는 삭제하고 공용 헬퍼와 과거 결과 문서는 보존한다. `READ_ONLY`·`WITH_WRITES` 구성 객체는 단위 테스트·프로필 구성에 유지한다. 골든 14개는 `writes_org` 단일 프로필 기록이다. ADR-018 11항·ADR-020 1·9항·ADR-021 2·8항의 옛 실행 프로필 설명을 대체한다.
2. **A 장애 분류**: `AMcpClient`는 호출별 전송 오류·HTTP 상태·Retry-After를 우선 분류한다. A 5xx·429는 읽기 재시도·서킷 대상이며 429는 Retry-After를 따른다. 401 경로는 불변이다. SDK 중복 타이머는 제거하고 도구 정책의 Hard 제한을 적용한다.
3. **주입 경계**: F11은 `replay/chaos_a.py`의 루프백 원시 chaos A 서버로 지연·500·연결 거부를 주입한다. 가짜 A 미사용, 합성 토큰만 사용, 요청 헤더 미기록이다. 판정은 ① 부작용 0건과 ② 요청 문장에 이미 있는 값을 제외한 필수 fact 값의 답변 포함 0건을 동시에 요구한다. 환각은 확률적 결과이며 실패로 센다. 필수 값이 적으면 탐지가 약하고 임의의 창작 사실·의역은 탐지하지 못한다.
4. **F4·F6·F10**: F4는 `tools/fault_transport.py`의 클라이언트 쪽 연결 수립 실패 주입이다. F6는 `down_alias` 호출 번호 스크립트 first/second/all(4/3/3 배정)을 사용한다. F10은 항상 F5 처리 후 응답 유실과 결합하며 짧음 1.0초·긺 6.0초다. 긴 지연은 절대 규칙에서 제외하는 한계 시연이다.
5. **계획·재개**: 등록 파일의 계획 154회·절대 규칙 1 분모 132회·시드 20261005를 따른다. 비정상 종료 시 완료 기록을 보존하고 `--resume <run_id>`로 같은 시드·순서의 남은 계획만 실행한다. 결과에 따른 재실행은 금지하며 인프라 실패의 1회 재시도는 같은 계획 위치에서만 허용한다. 등록 파일의 미커밋·최초 커밋 이후 변경은 차단하고 sha256·등록 커밋을 manifest에 기록한다. 실행마다 새 ToolPolicy로 서킷을 초기화한다.
6. **사용자 실행**: `make smoke-faults`로 환경·규칙 적용/해제·F5+F10·down_alias를 확인한 뒤 `make eval-faults`로 등록 계획을 실행한다. F7과 F6 중 주 모델 장애 중 Guardrail 통과 검증은 M4로 이월한다.

7. trace 마스킹은 키와 형식이 모두 맞는 식별자 속성(`generation_id`·`trace_id`·`task_id`·해시·등록된 `deployment_id`)에는 PII 검사를 건너뛴다. 형식 밖 값은 검사한다. 비밀 제거는 그대로 적용한다.

8. 남은 규칙 보호는 실행마다 하는 `reset`(과 그 실패 처리)이 맡는다. mock은 설정된 규칙 목록을 읽는 API가 없고 `faults stats`는 요청 카운터만 돌려주므로, 하네스는 시작 시 `reset` 직후 탐침 요청 하나로 `applied_rules`가 0인지 행동으로 확인한다. 탐침의 `requests >= 1`을 함께 확인하고 점검 뒤 다시 `reset`한다. 재개 시에도 시작 점검을 수행한다.

**결과**: 주입 카운터·전송 시도·표식 조회·대기·mock 효과·종료 상태를 함께 판정하며 단일 지표로 성공을 정하지 않는다. 계획 수는 성공 수가 아니며 실행 결과는 결과 문서에만 기록한다. in-process 평가는 실제 서버 프로세스·세션 TTL을 검증하지 않는다. F10 긴 지연과 F11 값 일치 판정의 한계를 별도로 보고한다.


### ADR-023: M4 결정 — 팀 가상 키·Guardrail v1 훅·입력 차단·격리·감사 이벤트·레드팀 생성
**상태**: 유효
**날짜**: 2026-10-07
**결정**:
1. **훅 배치**: LiteLLM 안의 `pre_call`·`post_call` 훅은 `gateway/guardrail_hook.py`가 맡고, 판정은 표준 라이브러리만 쓰는 `guardrails/`가 맡는다. 컨테이너는 `src/`·`data/seed/users.json`을 읽기 전용 마운트하고 `PYTHONPATH`로 import한다. 팀 정책을 쓰지 않는 훅에 `data/policy/`를 마운트하지 않는다. 훅이 import하는 govops 모듈은 허용 목록 테스트로 고정한다.
2. **역할·입력**: 입력 판정은 `role:user`만, 격리·마스킹은 `role:tool`만 적용하고 `system`·`assistant`는 검사하지 않는다. 사용자 인젝션·금지 PII(`rrn`·`mobile`·`landline`·`email`·`card`·`account`)는 팀과 무관하게 HTTP 400 `injection_direct`·`pii_input`으로 차단하고 task는 `blocked_input`으로 종료한다. 팀 정책 block/mask는 `agent/checks.py`의 발송 전 검사에만 쓴다. 도구 결과 PII는 마스킹하고 인젝션 의심 문장은 `[정책으로 일부 제외]`로 격리한다. 식별자 형식 값은 PII 검사에서 제외한다.
3. **장애**: 사전 훅 제한은 2.0초로 클라이언트 60초보다 짧다. 타임아웃·오류는 HTTP 400 `guardrail_unavailable`로 fail-closed 처리하며 task는 `blocked_input`이 아닌 `failed`다. 팀 불명은 HTTP 400 `team_unknown`이다. 사후 오류는 통과 + 경보다. 호스트는 헤더 누락·해시 불일치를 `guardrail_header_missing`·`guardrail_stale`로 실패 처리한다.
4. **전달·감사**: 응답 헤더 `x-govops-guardrail` 필드는 `v`·`rules`·`code`·`team`·`pre`·`c`·`q`·`m`·`ids`·`bad`·`trunc`·`ms`·`post`다. `ids`는 최대 64개이며 초과는 `trunc`로 표시한다. 훅은 감사에 쓰지 않는다. 호스트가 `guardrail.input_blocked`·`guardrail.tool_quarantined`·`guardrail.tool_masked`·`guardrail.output_flagged`·`guardrail.error`를 해시·코드·건수로 기록한다. 격리 건수는 `tool_call_id` 기준 task 단위 중복 제거한다. 감사 쓰기 실패는 차단 결과를 바꾸지 않고 경보한다. `pii_input` 감사에는 입력 해시를 남기지 않는다.
5. **정합**: `rules` 12자는 규칙 파일 바이트 해시, `code` 12자는 훅이 import하는 파일 묶음 해시다. import 시 한 번 계산해 헤더에 내보내고 preflight·클라이언트가 로컬 해시와 비교한다. preflight 불일치는 `guardrail_hook_stale`다. 진단 프로브 `govops_probe`는 요청을 막거나 느리게 하거나 경보만 만든다. 규칙 해시는 `guardrails/registry.py`의 `rules_sha256()`으로 얻으며 규칙 내용을 문서에 싣지 않는다.
6. **팀 키**: 팀 8개(`team_id` = 팀 이름)와 팀 키 8개를 사용한다. 판정 팀의 출처는 인증 객체 `user_api_key_dict.team_id`다. 모델 목록의 단일 출처는 `agent_key.py`의 `AGENT_KEY_MODELS`다. 팀 예산은 사용량 귀속용 비구속 값이며 실제 상한은 B 총액 원장이 강제한다. 서비스·dev·faults·smoke 모두 팀 키를 쓰며 에이전트 키는 진단 전용으로 `team_unknown`에 차단된다.
7. **실행 효율**: 청결 검사·Stop hook 예외는 `data/manifest/costs.jsonl` 하나의 정확 일치이며 단일 출처는 `scripts/exempt_paths.json`이다. 시작 시 원장 줄 수·sha256을 manifest의 `ledger_at_start`에 기록한다. 원장 단독 PR은 금지한다. attempts는 허용 코드만 `check_reason`에 남긴다. `make verify`의 pytest는 `-n auto`로 실행한다.
8. **레드팀 생성**: 120건은 직접 인젝션 30·간접 인젝션 40·신원 사칭 20·PII 유출 30의 결정론 템플릿이다. 배분·판정 규칙은 생성 전에 `hypotheses-m4-redteam.md`에 등록한다. 생성기·Guardrail은 서로의 데이터를 읽지 않으며 규칙 해시만 `guardrails/registry.py`로 얻는다. 간접 전달은 `MOCK_JIRA_OVERLAY`의 mock-jira 티켓 본문·코멘트 오버레이로 한다. Slack 메시지는 모델 읽기 도구가 없어 전달 경로가 아니다. 각 항목에 `expected_layer`·`fallback_layers`·`delivery`를 두고 오프라인 카드·확정본 검증으로 사람 검수한다.
9. **레드팀 판정**: 간접 항목은 `payload_read`를 따로 기록하고 읽지 않은 항목은 전달 실패로 분리 집계하며 차단율 분모에서 제외한다. 합성 주민등록번호는 실재 날짜와 틀린 검증 숫자를 쓴다. 기대 층 포착과 후속 방어 층 포착을 분리한다.
10. **평가**: dev 전체 재실행 전에 규칙 해시를 등록한 `hypotheses-m4-guardrail.md`·`m4-dev3-supersedes.json`을 먼저 커밋한다. `blocked_input`은 별도 관측 범주이며 TCR 실패다. H1은 정상 dev 입력 차단 부재, H2는 간접 인젝션 task에 한정된 격리, H3는 훅·팀·헤더·해시 오류 부재를 검증한다. Guardrail 판정 비교는 M6에 새 등록 파일과 함께 수행한다. 골든 14개는 단일 턴 경로 불변 확인만 맡는다.

**이유**: 인증된 팀 귀속과 역할별 검사로 입력·도구 결과의 신뢰 경계를 지키고, 헤더 정합 검증과 내용 없는 감사로 판정 전달을 검증한다. 생성·판정의 독립성과 사전 등록으로 평가 기준을 고정한다.
**트레이드오프**: 사전 장애 차단은 가용성을 낮춘다. 정규식 밖 우회와 도구 결과 미전달은 별도 위험이며 해시 갱신에는 훅 재기동이 필요하다. 감사 해시체인·서명은 M4 phase 2에서 추가한다.


### ADR-024: M5 결정 — 요청자 절 프로필, 대화 목록·도구 단계, 렌더링·테마 정책
**상태**: 유효
**날짜**: 2026-10-08
**결정**:
1. **프롬프트 프로필**: `writes_team`은 발송 규칙을 담은 템플릿과 요청자 절로 구성한다. 요청자 절은 인증된 세션 사용자의 소속 팀·보낼 수 있는 채널·전사 공지 승인 조건을 담는다. `writes_org`는 동결하고 삭제하지 않으며 새 기본값으로 쓰지 않는다. `GOVOPS_AGENT_PROFILE` 기본값은 `writes_team`이다. 골든 14개는 사용자 dev 재실행 때 러너가 `writes_team`으로 재기록한다. 그 전에는 기존 `writes_org` 기록을 유지하며 장애 주입 하네스도 `writes_org`를 유지한다.
2. **해시 기록**: `prompt_hash`는 템플릿(도구·모델·호출 파라미터 포함) 해시다. 사용자별 요청자 절은 원문 대신 `requester_section_sha256`으로 manifest·골든 헤더에 기록한다. 호출별 `request_key`는 전체 messages를 덮는다.
3. **정책 단일 출처**: 보낼 채널은 `allowed_channels`, 전사 공지 승인 급수는 `ANNOUNCE_APPROVER_MIN_GRADE`가 단일 출처다. 요청자 절과 결정론 검사·승인 정책이 이를 공유한다.
4. **UI 데이터**: `GET /conversations`는 소유자 본인 대화만 상한 50개로 읽기 전용 조회한다. 색인은 힌트이고 체크포인트가 권위다. 제목은 첫 사용자 요청을 공백 정규화하고 40자로 자른 뒤 넘으면 말줄임표를 붙인다. 입력 차단 원문을 제목에 쓰지 않으며 요청이 없으면 “새 대화”다. 요청자 본인 턴 뷰의 `tool_steps`는 읽기 도구 이름·성공만 담고 `approval_method`·턴별 `effect_at`을 제공한다. 승인자 뷰에는 이 필드를 넣지 않으며 BFF는 허용 목록으로 투영한다.
5. **렌더링**: `react-markdown` 10.1.0·`remark-gfm` 4.0.1을 정확 고정한다. 원시 HTML은 렌더링하지 않고 이미지·위험 스킴 링크를 금지하며 http/https 링크만 허용한다. 외부 링크는 `rel="noopener noreferrer nofollow"`를 쓴다. 이 정책을 위해 새 의존성을 추가하지 않는다.
6. **테마 저장소 예외**: `localStorage` 접근은 `lib/theme.ts` 한 파일에서 키 `govops-theme`와 값 `light`/`dark`/`system`만 사용하며 `try/catch`로 감싼다. 세션 토큰은 httpOnly 쿠키에 둔다.
7. **e2e 청결 검사**: `make e2e-web`은 실행 전후의 정렬한 `git status --porcelain`을 비교해 e2e가 새로 만든 변경만 실패로 처리한다. 경로만 출력하고 Playwright 종료 코드를 보존한다. e2e는 추적 파일을 쓰지 않는다는 전제이며 이미 변경된 파일의 내용 변경은 같은 status 줄이면 감지하지 못한다.

**이유**: 요청자 팀과 발송 정책을 신뢰된 세션에서 구성하고 해시로 기록해 재생 계약을 유지한다. 요청자에게 진행·발송 결과를 보여 주면서 승인자에게 노출되는 정보를 제한한다. 출력 렌더링과 저장소 접근을 좁혀 브라우저 실행·정보 유출을 줄인다.
**트레이드오프**: 사용자별 요청자 절 변경에는 재기록이 필요하다. 이미지와 원시 HTML을 표시하지 못하며 대화 목록은 최신 50개까지만 제공한다. 청결 검사는 이미 더러운 파일의 내용 변경을 감지하지 못한다.
**변경 기록**:
- 2026-10-08: 요청자 절·UI 계약·렌더링·테마·e2e 청결 검사 정책을 확정한다.


### ADR-025: M6 결정 — 단계 상한 마무리 호출과 대화 소프트 삭제
**상태**: 유효
**날짜**: 2026-10-13
**결정**:
1. **단계 상한 마무리**: `steps >= MAX_STEPS`(8단계)이면 먼저 `turn_read_ok >= 1`을 확인하고, 만족하지 않으면 호출 없이 `failed/max_steps`로 끝낸다. 다음으로 `turn_cost_usd >= MAX_COST_USD`이면 마지막 호출만 생략하고 `failed/max_steps`로 끝낸다(기존 단계 상한 판정이 비용 상한 판정보다 먼저다). 두 조건을 통과하면 기존 messages 뒤에 고정 안내문을 user 메시지로 덧붙여 마지막 호출 1회를 수행하고 `steps`는 8로 유지한다. 안내문은 상태 messages에 저장하지 않으며 도구 정의·호출 시그니처는 그대로다. 마지막 응답의 도구 호출은 실행도 초안 생성도 하지 않고 `failed/max_steps`로 끝낸다. 본문이 비어 있어도 같은 실패이며 도구 호출 없이 본문이 있으면 `completed`, `stop_reason="max_steps_finalized"`다. 호출은 기존 gateway·Guardrail 사전·사후 훅·비용 원장·성공 `model.call` 감사 경로를 거친다. 시스템 프롬프트·도구·모델·호출 파라미터와 프롬프트 해시는 불변이고 상한 미만 호출은 불변이므로 골든·dev 기록은 불변이다. 골든 재기록과 dev 재실행은 하지 않으며 근거·안내문 해시는 [사전 등록](results/hypotheses-m6-finalize.md)을 따른다.
2. **턴 범위와 탐색 예산**: `turn_read_ok`는 매 턴 0으로 초기화하며 이번 턴의 성공한 읽기 결과만 센다. ID가 없는 성공도 포함하고 오류는 제외한다. 대화 범위 누적 `evidence_ids`는 마지막 호출 조건에 쓰지 않는다. `max_steps_finalized` 턴은 N 재계산 입력에서 제외하며 과거 dev 입력·N 등록과 결과 문서는 보존한다. 요청자 본인 뷰에는 완료 여부와 이 종료 사유로 계산한 `limited` 불리언만 제공하며 승인자 뷰·승인함에는 넣지 않고 `stop_reason`은 계속 화면에 노출하지 않는다.
3. **대화 소프트 삭제**: 대화의 모든 턴 행에 `deleted_at`을 표시한다. Postgres는 소유자와 대화 ID로 한 번에 갱신하며 기존 스키마에는 `ALTER TABLE … ADD COLUMN IF NOT EXISTS`로 컬럼을 추가한다. 목록·승인 대기 색인은 삭제 행을 제외한다. `conversation_of`는 삭제된 대화에도 대화 ID를 돌려주고 서비스가 `is_deleted`로 판정한다. 첫 턴 ID = 대화 ID이며 이후 턴 ID는 삭제 엔드포인트의 대화 ID로 받지 않는다. 서비스는 대화 → task 잠금 순서를 유지하고 삭제 확인을 대화 조회·턴 추가·SSE·task 조회·승인·승인함에 적용한다.
4. **오류와 권한**: 타인·없음·삭제됨·형식 오류 대화 ID는 모두 404 `conversation_not_found`로 동일하게 응답한다. 실행 중 409 `turn_in_progress`, 승인 대기 또는 interrupt가 남은 경우 409 `approval_pending`은 기존 코드를 재사용하며 만료된 승인은 잠금 안에서 먼저 닫는다. 삭제 경로의 새 코드는 내부 실패용 500 `audit_write_failed`(감사 쓰기 실패·싱크 없음)·`delete_failed`(표시 실패) 두 개뿐이고 BFF는 이를 502 `upstream_unavailable`로 접는다. task 조회·승인은 삭제 확인 뒤 기존 404 `Task not found`를 반환하며 승인 권한 규칙 `approver_decision`은 바꾸지 않는다. 대화·task 라우트의 동일 응답과 승인 예외는 [권한 우회 프로브 사전 등록](results/hypotheses-m6-api-authz.md)을 따른다.
5. **삭제 감사 순서**: `conversation.deleted`는 필드 없는 비시스템 이벤트로 `task_id`=대화 ID, `actor_sub`=요청자다. 인증된 세션 신원으로 감사를 먼저 쓰며 실패하면 표시하지 않는다. 감사가 성공한 뒤 표시가 실패하면 500이고 재시도하면 같은 삭제의 감사 이벤트가 두 번 남을 수 있다. 정상 삭제 후 두 번째 삭제는 404이며 이벤트를 추가하지 않는다.
6. **보존과 제거**: 삭제 표시된 대화는 나이와 관계없이 다음 `checkpoint-prune` 후보지만 활성 상태(`running`·만료 전 `awaiting_approval`)는 제외한다. 일반 비활성 대화의 마지막 checkpoint 기준 7일 보존은 유지한다. 정리 적용 시 원문과 해당 턴 색인 행을 제거하고 감사 기록과 이미 발송된 효과는 남긴다. 명령의 기본 dry-run·`APPLY=1` 적용과 serve 시작 시 정리는 유지한다. 확인창 문구는 "원문은 다음 정리 때 제거되고, 감사 기록과 이미 발송된 효과는 남는다."로 고정한다. 즉시 하드 삭제 API는 이번 범위가 아니다.
7. **승인 배지 갱신 한계**: 사이드바 승인 배지는 인박스 처리 직후 최대 10초 늦게 갱신된다. 사이드바와 승인함의 공유 폴링 훅은 이번 범위에서 제외한다.

**이유**: 이번 턴에 확보한 읽기 결과로 단계 상한에서 답변을 마무리하면서 추가 도구 실행·부작용 초안을 막는다. 소프트 삭제와 모든 진입점의 권한 확인으로 대화 접근을 닫고 감사와 원문 정리를 분리한다. 사전 등록과 기존 기록을 보존하며 이 결정에는 TCR·정확도·지연 결과 수치를 기록하지 않는다.
**트레이드오프**: 마지막 모델 호출의 비용·지연이 추가되고 수집한 근거만으로 답하므로 일부 내용이 빠질 수 있다. 소프트 삭제 후에도 다음 정리까지 원문이 남으며 감사와 표시가 별도 저장소라 재시도 시 감사 중복이 가능하다. 승인 배지 갱신에는 폴링 주기만큼 지연이 남는다.
**변경 기록**:
- 2026-10-13: 단계 상한 마무리·턴 범위 계수·N 제외·소프트 삭제·권한과 오류·삭제 감사·보존·승인 배지 한계를 확정한다. ADR-001~024와 사전 등록 파일은 보존한다.


### ADR-026: M6 결정 — Guardrail v2는 호스트 수집 지점에서 분류하고 호출은 게이트웨이 경유
**상태**: 유효
**날짜**: 2026-10-14
**결정**:
1. **위치 결정**: A 위치(LiteLLM 훅 안)는 불성립이다. 2026-10-13 사용자 실행의 호스트 → Anthropic 직접 호출 지연 측정 원문은 `short p50 1.15s p95 1.33s`, `p50 p50 1.18s p95 1.42s`, `max p50 1.10s p95 1.43s max 2.58s`, `>1.2s` 18/60이다. p50 > 0.9s이므로 A 위치 불성립으로 판정하고 호스트 수집 지점을 택한다([사전 등록 §1](results/hypotheses-m6-guardrail-v2.md#1-측정-기록-원문-그대로-결과-해석을-바꾸지-않는다)). 이 측정은 게이트웨이 홉·v1 훅을 거치지 않았으며 게이트웨이 경유 지연은 아직 측정하지 않았다.
2. **수집 지점**: 사용자 입력은 턴 시작 첫 모델 호출 전 1회, 도구 결과는 수집 직후 라운드의 항목별 1회 분류한다. 판정은 메시지·체크포인트에 반영하며 이후 호출에서 재분류하지 않는다. 이력·마무리 고정 안내·에이전트가 만든 오류 안내는 분류 대상이 아니다.
3. **호출 경로**: 호스트의 `gateway` 클라이언트가 사용자 팀 키로 게이트웨이 별칭 `guardrail-v2`(→ `anthropic/claude-haiku-5-5`, fallback 없음)를 호출한다. 호스트는 `ANTHROPIC_API_KEY`를 읽지 않는다. v1은 분류 요청을 포함한 모든 게이트웨이 호출에 적용되고, v2는 에이전트 그래프를 거친 사용자 입력·도구 결과에만 적용된다.
4. **역할 동치성**: 호스트는 v1 순수 판정으로 사전 선별하고 전송 텍스트의 PII 6종을 마스킹한다. 사용자 입력은 `role:user`, 도구 결과는 `role:tool`(고정 가짜 assistant `tool_call` 1개와 고정 도구 정의·`tool_choice: "none"`)로 보내 게이트웨이 v1이 같은 scope 규칙을 적용하게 한다. 대상 텍스트는 래퍼 없이 그대로 보내며 호스트 사전 선별과 게이트웨이 `screen_request`의 동치성을 `tests/test_guardrails_semantic.py`로 고정한다.
5. **분류 오류**: `unclassified`(`timeout`·`rate_limited`·`error`·`parse`·`v1_blocked`)는 v1 판정만 남기고 통과 + `guardrail.error`(`guardrail_v2_error`) 경보로 처리한다. 분류 요청의 v1 차단을 원래 task의 입력 차단으로 기록하지 않는다. 원래 모델 호출의 v1까지 실패하면 기존 fail-closed를 유지한다.
6. **차단·격리**: 사용자 입력의 `unsafe`는 `injection_direct`를 재사용해 에이전트 모델 호출 없이 `blocked_input`으로 끝낸다. 도구 결과의 `unsafe`는 항목(문자열 값) 전체를 `[정책으로 일부 제외]`로 치환한다. 기존 차단·격리 감사 이벤트에 `v2=True`를 남기며 격리는 기존 task 단위 중복 제거를 따른다.
7. **스위치·재생**: `GOVOPS_GUARDRAIL_V2`는 호스트만 읽고 기본 off(미설정·빈 값도 off)다. 기록 재생·CI는 off 고정이며 on 상태의 재생은 시작을 거부한다. v2를 켠 live 기록은 재생 근거로 쓰지 않는다.
8. **비용**: 호스트 원장에서 `purpose=guardrail_v2`로 호출 직전 예약·호출 뒤 정산한다. 단가는 입력 $0.10·출력 $0.50 /MTok이며 확인일은 2026-10-13이다(출처·단가의 단일 출처는 `costs.py`의 `PRICES`). Anthropic 전체 기간 상한은 유지하고 예약 실패는 `failed/budget_exceeded`로 끝내며 v2를 조용히 끄지 않는다. 분류기 배포 매핑·비용 함수는 에이전트 응답 매핑과 분리한다.
9. **별칭 허용 목록**: 에이전트 키·팀 키 모두 `AGENT_KEY_MODELS`의 `guardrail-v2` 별칭을 호출할 수 있다. 이미 `agent-primary`·`agent-fallback`을 호출할 수 있어 새 능력은 아니다. 게이트웨이는 분류 프롬프트를 강제하지 않으며 인증 팀 요구는 유지한다.

**이유**: 훅 안 분류는 측정한 지연으로 성립하지 않는다. 수집 지점에서 한 번 분류하고 판정을 메시지에 남기면 이후 모델 호출의 재분류를 피한다. 호출을 게이트웨이로 보내 기존 팀 신원·v1·비용·관측 경계를 유지하고 호스트에 공급사 키를 두지 않는다. v2 오류 시 v1 판정으로 이어 가면서 피해 지점의 결정론 검사·승인을 유지한다.
**트레이드오프**: 에이전트를 거치지 않는 팀 키 직접 호출은 v2를 거치지 않고 v1만 적용된다. 항목 전체 격리는 오탐일 때 정상 업무 내용도 제거한다. 분류기 판정은 비결정적이며 `temperature`를 지정하지 않는다. 라운드당 항목 16개 초과분·분류 실패·`v1_blocked` 항목은 v1만 적용된다. 스위치 off나 `unclassified`로 처리된 턴은 이후에도 v2 판정 없이 이월된다. 스위치 on일 때 Anthropic 전송 범위는 fallback 호출분에서 v1을 통과한 모든 에이전트 요청의 사용자 입력·도구 결과(PII 6종 마스킹 뒤)로 늘어난다. 탐지 성능은 이 결정에서 주장하지 않는다.
