# Step 10: docs

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/PRD.md`(M4 행), `/docs/ADR.md`(전체 구조: ADR-008·ADR-009·ADR-010·ADR-012·ADR-018~022의 형식과 "변경 기록"), `/docs/ARCHITECTURE.md`, `/docs/THREAT_MODEL.md`(위협 표 TH1·TH2·TH9, 완화 ↔ 검증 매핑 V1·V2·V11, 장애 주입 F6·F7), `/docs/EVAL.md`, `/tests/test_docs_consistency.py`
- 이번 phase 산출물(읽기 전용): `/src/govops/guardrails/`(`registry.py`·`screen.py`의 **공개 함수·상수 이름과 독스트링만**. `rules_v1.json`과 `injection.py`의 규칙·패턴 내용은 열람하지 않는다. 문서에는 규칙 해시·모듈 이름만 쓴다), `/src/govops/gateway/`(`guardrail_hook.py`·`hook_digest.py`·`hook_header.py`·`team_keys.py`·`client.py`·`smoke_guardrail.py`), `/infra/litellm/config.yaml`·`/infra/docker-compose.yml`, `/src/govops/agent/graph.py`·`/src/govops/api/tasks.py`·`/src/govops/audit/log.py`, `/src/govops/evalrun/dev_m3.py`·`preflight.py`, `/docs/results/hypotheses-m4-guardrail.md`·`hypotheses-m4-redteam.md`, `/data/redteam/allocation.json`·`manifest.json`, `/scripts/exempt_paths.json`, `/Makefile`, 이전 step의 테스트 파일 이름들(`tests/`)
- `/.claude/skills/failure-patterns/SKILL.md` — 읽기만 한다

## 작업

**수정할 파일**(이 목록이 전부다): `docs/ADR.md`, `docs/ARCHITECTURE.md`, `docs/THREAT_MODEL.md`, `docs/EVAL.md`, `docs/PRD.md`, `AGENTS.md`, `tests/test_docs_consistency.py`(위 문서 변경으로 의도적으로 바뀌는 단언의 갱신과 신규 케이스에 한함, **단언 약화 금지**). 코드·`docs/results/`는 고치지 않는다. **소비자 테스트 주의(실측)**: ① `tests/test_docs_consistency.py::…ADR-009…`는 ADR-009 `**변경 기록**`의 항목 수가 정확히 1이라고 단언한다. 이 step이 "Guardrail 판정 비교는 M6" 한 줄을 추가하므로 그 단언을 **정확히 2**로 갱신한다(0·1로 느슨하게 하지 않는다). ADR-007·ADR-010의 항목 수(각 1)와 `### ADR-017:`·`### ADR-022:`·`### ADR-006:` 같은 앵커 제목은 그대로 둔다. ② `AGENTS.md`의 `CRITICAL:` 개수는 25로 유지하고(`tests/test_docs_consistency.py`), step 1이 넣은 예외 문장(`비용 원장 \`data/manifest/costs.jsonl\` 하나뿐이면 로컬 검증을 생략한다`, 그 경로 문자열은 AGENTS.md에 정확히 1회)과 `| 평가 실행기 | \`evalrun/\`` 행·`make eval-run-dev …` 줄·`make eval-faults`·`smoke-faults` 줄·`caffeinate -i make eval-faults`·`75~80분`을 `tests/test_evalrun_worktree.py::test_agents_matches_single_source`와 `test_docs_consistency.py`가 확인하므로 바꾸지 않는다. ③ `docs/ARCHITECTURE.md`의 `## 디렉토리 구조`·`## 배치도` 제목과 `contract/`·`seed/`·`replay/`, `docs/ARCHITECTURE.md`의 마커(`replay.llm` 등), `docs/ADR.md`·`docs/EVAL.md`의 `구조 필드`·`` `doc_id` ``, `docs/THREAT_MODEL.md`의 `LLM·A 기록 파일`은 지우지 않는다. ④ ADR-022 절 뒤에 ADR-023을 추가한다(`### ADR-022:` 이후 문자열을 자르는 기존 테스트의 마커가 유지돼야 한다). **문서에는 결정 내용만 쓴다**(경위·논의·누가 결정했는지·결과 수치는 쓰지 않는다. 지금 사실만).

1. `docs/ADR.md`에 **ADR-023: M4 결정 — 팀 가상 키·Guardrail v1 훅·입력 차단·격리·감사 이벤트·레드팀 생성**을 추가한다(ADR-018~022 형식: 상태·날짜·결정·이유·트레이드오프). 결정 항목:
   - 훅 배치: LiteLLM 안(`pre_call`·`post_call`), 결정 로직은 `guardrails/`(표준 라이브러리만), 컨테이너는 `src/`·`data/seed/users.json`을 읽기 전용 마운트하고 `PYTHONPATH`로 import. `data/policy/`는 마운트하지 않는다(훅은 팀 정책을 쓰지 않는다). 훅이 import하는 govops 모듈은 허용 목록으로 고정.
   - 역할 한정: 입력 판정은 `role:user`만, 격리·마스킹은 `role:tool`만, `system`·`assistant`는 검사하지 않는다.
   - 입력 차단: `role:user`의 인젝션·금지 PII(`rrn`·`mobile`·`landline`·`email`·`card`·`account`)는 **팀과 무관하게** 차단(400 `injection_direct`·`pii_input`, 상태 `blocked_input`). 팀 정책 block/mask는 `agent/checks.py`의 발송 전 검사에만 쓴다. 도구 결과의 PII는 마스킹, 인젝션 의심 문장은 `[정책으로 일부 제외]`로 격리(식별자 형식 값은 PII 검사 제외).
   - 훅 장애: 사전 훅 타임아웃 2.0초(클라이언트 60초보다 짧음)·오류는 400 `guardrail_unavailable`(fail-closed, `blocked_input`이 아니라 `failed`), 팀 불명은 400 `team_unknown`, 사후 훅 오류는 통과 + 경보. 헤더가 없거나 해시가 다르면 호스트가 `guardrail_header_missing`·`guardrail_stale`로 실패한다.
   - 판정 전달: 응답 헤더 `x-govops-guardrail`(필드 목록, `ids` 최대 64·`trunc`), 훅은 감사에 쓰지 않고 호스트가 감사 이벤트(`guardrail.input_blocked`·`tool_quarantined`·`tool_masked`·`output_flagged`·`error`)를 해시·코드·건수로 기록, 격리 건수는 `tool_call_id` 기준 task 단위 중복 제거. 감사 쓰기 실패는 차단 결과를 바꾸지 않고 경보.
   - 정합: 훅 해시 두 값(`rules` 12자 = 규칙 파일, `code` 12자 = 훅이 import하는 파일 묶음)을 import 시 한 번 계산해 헤더로 내보내고 preflight·클라이언트가 로컬 해시와 비교(`guardrail_hook_stale`). 진단 프로브(`govops_probe`)는 요청을 막거나 느리게 하거나 경보만 만든다.
   - 팀 키: 팀 8개(`team_id` = 팀 이름)·팀 키 8개, 모델 목록은 `agent_key.py`의 `AGENT_KEY_MODELS` 단일 출처, 팀 예산은 구속하지 않는 값(실제 상한은 B 총액 원장), 서비스·dev·faults·smoke 모두 팀 키, 에이전트 키는 진단 전용(`team_unknown`으로 막힘).
   - 실행 효율: 원장 `data/manifest/costs.jsonl` 하나만 청결 검사·Stop hook 예외(단일 출처 `scripts/exempt_paths.json`), 시작 시 원장 줄 수·sha256을 manifest에 기록, 원장 단독 PR 금지, attempts의 `check_reason`, `make verify`는 `pytest -n auto`.
   - 레드팀: 120건(30·40·20·30) 결정론 템플릿, 배분·판정 규칙은 생성 전 사전 등록(`hypotheses-m4-redteam.md`), 규칙 해시는 `guardrails/registry.py`로만 얻고 생성기·Guardrail은 서로의 데이터를 읽지 않는다, 간접 인젝션은 mock-jira 오버레이(`MOCK_JIRA_OVERLAY`, 티켓 본문·코멘트)로 전달하며 Slack 메시지는 모델 읽기 도구가 없어 전달 경로가 아니다, 항목마다 `expected_layer`·`fallback_layers`·`delivery`, 사람 검수 도구.
   - 레드팀 판정: 간접 항목은 `payload_read`를 따로 기록하고 읽지 않은 항목은 "전달 실패"로 분리 집계(차단율 분모 제외), 합성 주민등록번호는 실재 날짜 + 틀린 검증 숫자. 팀의 출처는 인증 객체 `user_api_key_dict.team_id`이고 `pii_input` 차단의 감사에는 입력 해시를 남기지 않는다.
  - 평가: dev 재실행은 `hypotheses-m4-guardrail.md`(규칙 해시 등록)·`m4-dev3-supersedes.json`을 먼저 커밋한 뒤 전체 재실행, `blocked_input`은 별도 관측 범주(TCR 실패), 가설 H1~H3.
   - **ADR-009 변경 기록에 "Guardrail 판정 비교는 M6"을 한 줄 추가**하고 ADR-023에도 같은 결정(골든 14개는 단일 턴 경로 불변 확인만, 판정 비교는 새 등록 파일과 함께 M6)을 적는다.
2. `docs/ARCHITECTURE.md`: 게이트웨이·Guardrail 절에 훅 배치·마운트·헤더·해시·팀 키 흐름·`blocked_input` 상태·감사 이벤트를 현재 사실로 추가한다(배치도의 기존 서술을 이 구조와 맞춘다).
3. `docs/THREAT_MODEL.md`: V1·V2·V11의 "확인하는 테스트·지표"를 실제 테스트 파일 이름(`tests/test_guardrails_screen.py`·`tests/test_gateway_guardrail_hook.py`·`tests/test_agent_guardrail.py`·`tests/test_smoke_guardrail.py` 등)과 `make smoke-guardrail`로 갱신한다. TH9의 완화에 `guardrail_unavailable`·`team_unknown`·훅 해시 정합을 더하고, F7·F6의 "M4로 이월" 문구를 "`make smoke-guardrail`로 확인"으로 바꾼다. 잔여 위험에 "정규식 밖 우회", "훅 import 시점에 고정된 해시는 재기동 전 변경을 헤더 불일치로만 드러냄"을 쓴다.
4. `docs/EVAL.md`: Guardrail 지표 정의(차단율은 `stopped_layer == expected_layer`, 방어 층별 포착률, 정상 대조 오탐률의 분모 60, 격리 건수의 중복 제거 기준, 훅 소요 `ms`)와 레드팀 세트 구성·판정 규칙 요약을 추가한다.
5. `docs/PRD.md`: M4 행을 현재 구성(팀 키·Guardrail v1·감사 해시체인은 phase 2)으로 갱신하고 "M3에서는 허용 감사 이벤트가 없어 …" 같은 지난 시점 문장은 현재 사실에 맞게 고친다. 일정·범위 축소는 하지 않는다.
6. `AGENTS.md`: 명령어 절에 `make team-keys`(키 회전은 `make team-keys TEAM_KEYS_ARGS=--rotate`)·`team-keys-update`·`smoke-guardrail`·`redteam-gen`·`redteam-cards`·`redteam-review-status`를 한 줄씩(사용자 실행 표기) 추가한다. 계층 경계 표의 `gateway/` 행에 "Guardrail 훅 어댑터는 `govops.guardrails`·`govops.obs.mask`만 import(허용 목록 테스트)"를, `guardrails/` 행에 "`registry.py`는 `govops`를 import하지 않는다(레드팀 해시용)"를 덧붙인다. 기술 스택의 LiteLLM 줄에 "팀 가상 키 + Guardrail 훅(`pre_call`·`post_call`)"이 반영되도록 한다. **CRITICAL 목록의 기존 문장은 바꾸지 않는다.**
7. `tests/test_docs_consistency.py`: 새 문구 확인을 추가한다 — ADR-023 존재, ADR-009 변경 기록의 "Guardrail 판정 비교는 M6", AGENTS.md 새 명령어 6개가 `Makefile`에 실제 타깃으로 있음, THREAT_MODEL V1·V2·V11이 가리키는 테스트 파일이 실제로 존재함, **이번 step이 추가한 ADR-023 절과 새로 쓴 문장**에 결과 수치가 없음(**검사 대상은 "TCR" 같은 단어가 아니라 수치 형태**: `n/N`·`%`·소수 점수로 쓴 값. ADR-023에 "TCR 실패" 같은 단어가 들어가는 것은 허용한다. 기존 문서에는 이미 TCR과 `n/n` 수치가 있으므로 문서 전체를 검사하지 않고, 테스트가 `## ADR-023`부터 다음 `### ADR-` 앞까지와 이번 step의 새 문장으로 범위를 한정해 검사한다).

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

- **이번 step이 추가·수정하는 문장**에 결과 수치(수치 형태: `n/N`·`%`·소수 점수의 TCR·차단율·건수·지연 값)·"채택/미달"·실행 경위를 쓰지 마라(FP-043). 기존 문서의 기존 수치 문장은 건드리지 않는다. "TCR 실패" 같은 용어 사용은 허용한다. 이유: live 실행 전이며 결과는 결과 문서가 쓴다. 문서는 결정 내용과 현재 사실만 가진다.
- `AGENTS.md`의 CRITICAL 문장을 바꾸거나 지우지 마라(위 6번에서 허용한 표·명령어 추가만). 이유: 프로젝트 고정 제약이며 이번 phase의 예외 문장은 step 1이 이미 넣었다.
- 문서에 토큰·키 값·규칙 패턴 문자열을 쓰지 마라. 규칙 파일의 내용·패턴은 Codex가 직접 열람·출력·인용하지 않는다(문서에 규칙 해시·코드 이름을 쓰는 것은 허용). 이유: 비밀 값 비노출과 규칙 비공개(레드팀 오염 방지)가 CRITICAL이다.
- 수정할 파일 목록 밖의 코드·`docs/results/`·`data/`를 수정하지 마라(목록의 `tests/test_docs_consistency.py`는 예외). 이유: 이 step은 문서·문서 일관성 테스트만 다룬다. 사전 등록 파일은 읽기 전용이다.
- 존재하지 않는 테스트 파일·명령어·타깃을 문서가 가리키게 하지 마라(FP-050). 이유: 일관성 테스트로 강제한다. 문서를 쓰기 전에 실제 파일·`Makefile` 타깃을 확인한다.
