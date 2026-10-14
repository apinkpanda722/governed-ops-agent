# Step 5: v2-docs-sync

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md`(계층 경계, CRITICAL 규칙)
- `/docs/ARCHITECTURE.md`, `/docs/ADR.md`, `/docs/THREAT_MODEL.md`
- `/docs/M6_GUARDRAIL_V2_DESIGN_DRAFT.md`(이 phase의 승인된 설계), `/docs/results/hypotheses-m6-guardrail-v2.md`(사전 등록, 읽기만 한다)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다
- `/docs/ADR.md`(ADR-007·ADR-008·ADR-013·ADR-023·ADR-025 — ADR-023 본문은 수정하지 않는다), `/docs/ARCHITECTURE.md`(`게이트웨이와 Guardrail 비대칭 구조` 절의 `m4-current` 표식 밖 목록), `/docs/THREAT_MODEL.md`(TH2·TH9·TH10, 2절 데이터 흐름, 6절 잔여 위험), `/docs/EVAL.md`(`:124`의 Guardrail v2 행, 공급사 표), `/docs/PRD.md`, `/.env.example`
- `/tests/test_docs_consistency.py`(ADR-023·ADR-024 절 문자열 단언, `AGENT_KEY_MODELS` 언급), `/tests/test_env_defaults.py`

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

**수정할 파일**(이 목록이 전부다): `docs/ADR.md`, `docs/ARCHITECTURE.md`, `docs/THREAT_MODEL.md`, `docs/EVAL.md`, `.env.example`, `tests/test_docs_consistency.py`. 단, 아래 **소비자 점검 대상** 단락의 파일은 그 단락의 조건(단언이 실제로 깨질 때, 새 값을 반영하는 것에 한함)에서만 이 목록에 포함된다. TDD.

열람 금지 경로: `data/interim/`
수정 허용 경로: `docs/ADR.md`, `docs/ARCHITECTURE.md`, `docs/THREAT_MODEL.md`, `docs/EVAL.md`, `.env.example`, `tests/test_docs_consistency.py`, `scripts/test_step_files.py`, `tests/test_broker.py`, `tests/test_compose.py`, `tests/test_repo_rules.py`

열람 금지의 범위: 이 금지는 **Codex 세션이 `data/interim/` 아래 파일을 직접 열람·출력·인용하는 것**에 대한 것이다. 이 step의 코드와 테스트는 `data/interim/`의 실제 파일 내용을 읽지 않는다. 테스트는 임시 디렉터리·합성 데이터·테스트용 키만 쓴다. 기본 경로 문자열이 코드에 있는 것은 이 금지의 대상이 아니다.

**소비자 점검 대상**(**~에 한함, 단언 약화 금지**): 이 step의 변경을 시뮬레이션한 전체 `pytest`에서 깨진 테스트는 본문의 `테스트`·`검증` 절에 명시한 것뿐이며 명시가 없으면 깨진 테스트는 없었다. 다음 파일은 변경 대상 모듈·파일을 import·참조하는 소비자로 `수정 허용 경로`에 올려 두었다. **단언이 실제로 깨질 때만, 정확히 새 값을 반영하는 것에 한해** 수정한다. 정확 일치를 부분 집합·포함 비교로 바꾸지 않고, 깨지지 않는 파일은 수정하지 않는다: `scripts/test_step_files.py`, `tests/test_broker.py`, `tests/test_compose.py`, `tests/test_repo_rules.py`. 이 단락의 파일을 수정했다면 최종 응답 summary에 파일명과 깨진 단언 이름을 적는다.

**배경**: 코드 변경(step 0~4)을 문서에 맞춘다. `docs/results/`의 사전 등록 파일과 `docs/M6_GUARDRAIL_V2_DESIGN_DRAFT.md`는 수정하지 않는다. AGENTS.md는 이 step에서 수정하지 않는다(명령어 표 한 줄은 step 4에서 더했다). **AGENTS.md CRITICAL 규칙과 ADR-007 본문 문구는 바꾸지 않는다.**

### 1. `docs/ADR.md`

- **ADR-026**을 파일 끝에 더한다. 제목: "M6 결정 — Guardrail v2는 호스트 수집 지점에서 분류하고 호출은 게이트웨이 경유". 상태 유효, 날짜 2026-10-14. 결정 항목: ① A 위치(LiteLLM 훅 안) 불성립 — 지연 측정 원문(`short p50 1.15s p95 1.33s`, `p50 p50 1.18s p95 1.42s`, `max p50 1.10s p95 1.43s max 2.58s`, `>1.2s` 18/60)과 판정(p50 > 0.9s, `docs/results/hypotheses-m6-guardrail-v2.md` §1 인용). ② 호스트 수집 지점(사용자 입력은 턴 시작 1회, 도구 결과는 수집 직후 항목별 1회, 판정은 메시지에 반영되어 재분류 없음). ③ 호출은 게이트웨이 별칭 `guardrail-v2`(`anthropic/claude-haiku-5-5`, fallback 없음), 사용자 팀 키, 호스트는 `ANTHROPIC_API_KEY`를 읽지 않음. ④ 사용자 입력은 `role:user`, 도구 결과는 `role:tool`(고정 가짜 `tool_call` 1개)로 보내 게이트웨이 v1이 같은 scope 규칙을 적용하게 하고 호스트 사전 선별과의 동치성을 테스트로 고정. ⑤ `unclassified`(`timeout`·`rate_limited`·`error`·`parse`·`v1_blocked`)는 v1만 남기고 통과 + `guardrail.error`(`guardrail_v2_error`). ⑥ 사용자 입력 차단 코드는 `injection_direct` 재사용, 도구는 항목 전체 격리. ⑦ 스위치 `GOVOPS_GUARDRAIL_V2`(기본 off, 호스트만 읽음), 기록 재생·CI는 off 고정. ⑧ 비용은 호스트 원장 `purpose=guardrail_v2` 예약·정산, 단가 입력 $0.10·출력 $0.50 /MTok(확인일 2026-10-13). ⑨ 에이전트 키·팀 키 모두 별칭 호출 가능(새 능력 없음: 이미 `agent-primary`·`agent-fallback`을 호출할 수 있음). 이유·트레이드오프(에이전트 비경유 호출은 v2를 거치지 않음, 항목 단위 격리의 오탐 비용, 분류기 비결정성, 라운드 항목 상한 초과 시 v1만 적용).
- ADR-007·ADR-008에는 **변경하지 않는다**(문구가 여전히 맞다). `test_m6_decision_contract`의 `previous`가 ADR-001~024 전체를 해시로 고정하므로 ADR-008 변경 기록도 추가하지 않는다. v2 분류기의 위치·방식은 ADR-026에만 기록한다.

### 2. `docs/ARCHITECTURE.md`

- 게이트웨이·Guardrail 절의 표식(`m4-current`) 밖 목록의 v2 문구를 다음에 맞춘다: v2는 호스트 수집 지점에서 분류하고 호출은 게이트웨이 별칭 `guardrail-v2`를 경유한다. v1은 게이트웨이(모든 호출), v2는 에이전트 경유 호출만. `m4-current` 표식 안(M4 확정 내용)은 수정하지 않는다.
- 데이터 흐름 변화를 적는다: Anthropic으로 나가는 내용이 fallback 호출분에서 **v1을 통과한 모든 에이전트 요청의 사용자 입력과 도구 결과(PII 6종 마스킹 뒤)** 로 늘어난다(스위치 on일 때).

### 3. `docs/THREAT_MODEL.md`

- 6절 잔여 위험에 항목 추가: 에이전트를 거치지 않고 팀 키로 게이트웨이를 직접 부르는 호출은 v2를 거치지 않는다(v1만 적용). 라운드당 항목 16개 초과·분류 실패·`v1_blocked`일 때 그 호출은 v1만 적용된다. 분류기 판정은 비결정적이다(`temperature` 미지정). 이력 이월 한계(스위치 off나 `unclassified`로 처리된 턴은 이후에도 v2 판정 없이 이월). **한 줄: "분류기 별칭은 범용 호출이 가능하다"**(게이트웨이는 프롬프트를 강제하지 않으며, 에이전트 키·팀 키는 이미 `agent-primary`·`agent-fallback`을 호출할 수 있어 새 능력은 아니다).
- TH10(PII 노출)·2절 데이터 흐름 다이어그램에 분류기 경로(호스트 → 게이트웨이 → Anthropic)를 반영한다. V11(Guardrail 장애)·F7에 "v2 오류 시 v1 판정으로 통과 + 경보"의 검증(결정론 테스트 이름)을 적는다.

### 4. `docs/EVAL.md`

- `:124` 근처의 `Guardrail v2` 행을 M6 7절(공식 test 3회)에 맞춰 `test 40 × 3 + dev 20 × 1`로 고치고 설명을 맞춘다. 다른 행은 수정하지 않는다. 공급사 표·`purpose` 설명에서 Guardrail v2 분류기가 게이트웨이 경유임을 한 줄로 적는다.

### 5. `.env.example`

- 변수 이름 `GOVOPS_GUARDRAIL_V2=`(값 없음)을 `GOVOPS_GUARDRAIL_*` 또는 인접 항목 근처에 더한다. `tests/test_env_defaults.py`의 변수 표 규칙을 따른다.

### 6. 테스트(`tests/test_docs_consistency.py` ~에 한함, 단언 약화 금지)

- **깨지는 소비자(시뮬레이션으로 확인)**: `test_m6_decision_contract`는 `adr.split("### ADR-025:", 1)`의 **뒤 전체**를 ADR-025 절로 보고 번호 목록 `1.`~`7.`의 정확 일치와 숫자 패턴 부재(소수·퍼센트·`a/b` 형식)를 단언한다. 파일 끝에 ADR-026을 더하면 이 절에 ADR-026이 섞여 깨진다. 수정은 **ADR-025 절을 다음 `### ADR-` 제목 앞에서 끊는 것에 한한다**(`section = section.split("\n### ADR-", 1)[0]`을 `previous, section = …` 직후에 더한다). `previous`의 sha256 고정값·표식 단언·번호 목록 단언·숫자 패턴 단언은 그대로 둔다. 같은 방식으로 ADR-025 절을 보는 다른 단언이 있으면 같은 끊기만 적용한다.
- 새 단언 하나를 더한다: `### ADR-026:`이 정확히 1개이고 `**상태**: 유효`·`**날짜**: 2026-10-14`·`**결정**:`·`**이유**:`·`**트레이드오프**:`·`hypotheses-m6-guardrail-v2.md`를 포함하며 AGENTS.md에 `- CRITICAL: 에이전트의 모델 호출은`로 시작하는 줄이 정확히 1개이고 그 줄이 `LiteLLM)로만 한다.`를 포함한다(ADR-007 본문 문구 `주 모델·fallback·v2 분류기는 LiteLLM 경유`도 그대로).
- 문서 변경을 반영한 상태에서 `tests/test_docs_consistency.py`·`tests/test_env_defaults.py`가 통과해야 한다. ADR-023·ADR-024 절 문자열을 단언하는 테스트가 있으므로 두 절의 본문은 바이트 단위로 수정하지 않는다.

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

- ADR-023·ADR-024 본문과 ARCHITECTURE의 `m4-current` 표식 안 문구를 수정하지 마라. 이유: `tests/test_docs_consistency.py`가 이 절들의 문자열을 단언한다(FP-071).
- AGENTS.md CRITICAL 규칙과 ADR-007 본문 문구를 바꾸지 마라. 이유: 이번 설계는 두 문구를 그대로 지킨다(분류 호출이 gateway 클라이언트·LiteLLM 경유라 여전히 참이다).
- 문서에 측정 결과 수치 외의 TCR·정확도·차단율 수치를 쓰지 마라. 이유: 이 phase는 탐지 성능을 주장하지 않는다(사전 등록 §5). ADR-026에는 지연 측정 원문만 인용한다.
- `docs/ARCHITECTURE.md`의 `## 알려진 한계` 절과 `docs/THREAT_MODEL.md`의 `TH15`·`V16` 행에 소수·퍼센트·`a/b` 형식 숫자를 쓰지 마라. 이유: `test_m6_decision_contract`가 이 영역의 숫자 패턴 부재를 단언한다(FP-071). 데이터 흐름·잔여 위험은 게이트웨이 절·6절에 쓴다.
- `docs/results/`의 사전 등록 파일과 `docs/M6_GUARDRAIL_V2_DESIGN_DRAFT.md`를 수정하지 마라. 이유: 등록 후 변경은 새 등록이다(FP-043).
