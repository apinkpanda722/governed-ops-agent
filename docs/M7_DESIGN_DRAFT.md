# M7 설계 초안 (개정, 확정 결정 반영)

기준: main `5d4302b`(M6 완료, 결과 `docs/results/2026-10-15-m6-redteam-run-live.md`). 이 문서는 untracked이며 사전 등록 커밋(`feature/m7-prereg`)에 함께 넣는다(7절). 코드는 아직 없다. live 실행·촬영·저장소 공개는 사용자 실행 항목이다.

표기: **실측**은 파일:행으로 확인한 사실, **미실측**은 확인하지 못했거나 사용자 실행이 필요한 사실이다.

---

## 0. 외부 계약 실측

### 0-1. 웹 라우트·BFF 구조

| 사실 | 근거 |
|---|---|
| 화면 라우트는 `/login`, `/(app)/c`, `/(app)/c/[id]`, `/(app)/approvals`뿐이다. **기록 화면 라우트는 없다** | `web/src/app/` 파일 목록 |
| BFF route handler 8개: `session`, `demo-users`, `conversations`, `conversations/[id]`, `conversations/[id]/events`, `conversations/[id]/turns`, `approvals`, `tasks/[id]/approval`. 파일 이름 목록은 정확 일치 단언이 있다 | `web/src/app/api/**/route.ts`, `web/src/app/api/routes.test.ts:11` |
| 모든 BFF는 `proxyJson`을 거친다: Origin 검사 → 쿠키 토큰 → ID 형식(`^[0-9a-f]{32}$`) → 업스트림 → **허용 목록 투영**. 401은 쿠키를 지운다 | `web/src/lib/server/proxy.ts:20-45`, `project.ts` |
| 업스트림 오류 코드 투영 허용 목록은 `approval_pending`·`turn_in_progress`·`turn_limit`·`conversation_not_found`·`invalid_request`뿐이다. 5xx(500 이상)는 `502 upstream_unavailable`로 접힌다 | `web/src/lib/server/proxy.ts:31-35,25` |
| 사이드바 링크는 "대화"·"승인함" 둘이다 | `web/src/components/sidebar.tsx:26-27` |
| FastAPI 라우트: `/sessions`, `/demo/users`, `/sessions/me`, `GET·DELETE /conversations/{id}`, `/conversations/{id}/events`(SSE), `/approvals`, `/conversations`, `/conversations/{id}/turns`, `/tasks`, `/tasks/{id}/approval`, `/tasks/{id}` | `src/govops/api/app.py:119-320` |
| 라우트는 의존성을 `request.app.state.task_service`로만 얻는다. 새 라우트는 실제 진입점 경유 테스트와 라우트 표 정확 일치에 걸린다 | `app.py:64-73`, FP-070, `tests/test_api_entrypoint.py` |
| `TaskService`는 `self.audit`(서비스가 쓰는 `AuditLog`)를 가지고 있고 `AuditLog.directory`가 체인 디렉터리다. 읽기 모듈은 이 디렉터리만 알면 되므로 **`wiring.py` 변경이 필요 없다** | `api/tasks.py:53`, `audit/log.py:309` |
| 코드 기본 프로필은 `writes_team`이고 공식 test N2 manifest의 profile도 `writes_team`이다. 데모는 `GOVOPS_AGENT_PROFILE`을 설정하지 않고 기본값으로 돈다 | `api/wiring.py:170`, N2 manifest |
| 계층 경계상 `api/`는 `audit`을 import할 수 있다 | `tests/test_architecture.py:29` |

### 0-2. 감사 기록 저장 위치·형식·고정점·서명

| 사실 | 근거 |
|---|---|
| 서비스 체인은 `data/interim/audit/chain-YYYY-MM-DD.jsonl`(0600, UTC 날짜별). 서비스는 `Path(env_or("AUDIT_DIR","data/interim/audit"))`로 연다 | `api/wiring.py:195`, ARCHITECTURE "감사 로그" |
| 현재 크기(파일 메타데이터만 확인): 체인 5파일 합 약 435KB, 대기 고정점 96KB, legacy 2파일 약 392KB | `ls -l data/interim/audit` |
| 레코드: `{seq, ts, prev_hash, actor:{sub,act}, event, task_id, …필드, kid, hash, sig}` | `audit/crypto.py`, `audit/verify.py:202-217` |
| 비시스템 이벤트의 `task_id`는 **턴 ID**(32 hex)이고 `actor_sub`는 `^demo-[a-z-]+-g[0-9]+$`다. 승인 이벤트의 `actor_sub`는 **승인자**, 나머지는 요청자다 | `audit/log.py:238-251`, `agent/graph.py:655-662` |
| **감사 레코드에는 `conversation_id`·`turn_id` 필드가 없다.** 대화 → 턴 ID 목록은 Postgres 턴 색인(`govops_conversation_turns`)에만 있고 Protocol에 그 조회가 없다 | `api/conversations.py:25-37,107-125`, `audit/log.py:108` |
| 문자열 필드 값은 해시·mock ID·열거형만 통과하고 `text`·`body`·`args`·`content`·`description`·`title`은 금지 이름이다 | `audit/log.py:108,253-` |
| 고정점: `make audit-anchor LABEL=…`가 `chain.anchor`를 덧붙이고 `anchors-pending.jsonl`에 `{dir, seq, hash, sig, kid, label, ts}`를 쓴다. 커밋본은 `docs/results/audit-anchors.jsonl`(234줄) | `audit/anchor.py:38-60`, `audit/log.py:466` |
| 고정점 `dir`은 저장소 상대 경로이며 **서명 대상이 아니다**(서명은 `hash`에 대한 것). 체인 복사본에 적용하려면 `dir`만 바꿔 쓸 수 있다 | `verify.py:268-292` |
| 공개키는 커밋(`data/seed/audit_public_key.pem`)이고 **검증은 공개키만 필요하다** | `verify.py:360-363` |

### 0-3. audit-verify 입출력과 꼬리 잘라내기 탐지

| 사실 | 근거 |
|---|---|
| CLI: `python -m govops.audit.verify [--dir D] [--public-key P] [--anchors A …] [--repo-root R] [--scan S …]`. `make audit-verify`는 `--scan data/results`로 개발 실행 체인도 읽는다(M6 결과: chains_pass=61) | `verify.py:346-372`, Makefile:266-269, `2026-10-13-m6-limits-delete-live.md:34` |
| 출력은 코드만 담는다: `PASS chain dir=… records=N kid=… anchors=N` / `FAIL chain dir=… code=<code> seq=<n|->` / `INFO legacy_unverified …` / `SUMMARY …`. 종료 코드는 실패 있으면 1 | `verify.py:402-447` |
| 문제 코드: `no_chain`, `unterminated_tail`, `invalid_json`, `invalid_record`, `hash_mismatch`, `prev_hash_mismatch`, `seq_order`, `seq_gap`, `kid_unknown`, `sig_invalid`, `genesis_missing`, `legacy_sha256_mismatch`, `tail_truncated`, `anchor_missing`, `anchor_hash_mismatch`, `anchor_sig_invalid`, `anchor_dir_missing`, `anchor_file_invalid` | `verify.py` 전역 |
| **꼬리 잘라내기 탐지**: 고정점 `(dir, seq)`가 있는데 체인에 그 seq 레코드가 없고 체인 마지막 seq < 고정점 seq이면 `tail_truncated`. 고정점 레코드 **뒤**만 자르면 다음 고정점 전까지 탐지하지 못한다 | `verify.py:312-325`, `smoke.py:141` |
| 읽기 전용: 검증 경로에 쓰기가 없다. `verify.py`는 `log._validate_event`(비공개 함수)를 쓴다 | `verify.py:20,185` |
| `scene5_tail_cut`: 임시 디렉터리에서 `chain.anchor` 직후 `seq <= anchor.seq`로 자르면 PASS, `seq < anchor.seq`로 자르면 `tail_truncated`. 출력은 `PASS scene5_tail_cut` 한 줄 | `smoke.py:114-139`, `2026-10-13-m6-limits-delete-live.md:35` |
| 실제 체인 **복사본**에서 같은 시연을 하는 것은 코드상 성립하지만 **미실측**이다(이번 설계에서는 `data/interim`을 열지 않았다). 첫 live에서 확인한다(7절 15번) | 위 `dir` 매칭 규칙 |

### 0-4. 웹에서 감사 기록에 접근할 때의 권한 모델

현재 웹·API에 감사 기록을 읽는 경로가 없다(ADR-012 변경 기록: "웹 기록 화면의 `audit-verify` 결과 표시는 M7"). 기존 권한 규칙을 그대로 따른다.

| 규칙 | 근거 |
|---|---|
| 대화는 소유자만 읽는다. 타인·없음·삭제됨·형식 오류 ID는 모두 같은 404 `conversation_not_found` | ADR-025 4항, `tasks.py:452-458` |
| 승인자는 승인 대기 초안만 본다(`/tasks/{id}`·`/approvals`). 대화 전체는 못 본다 | `tasks.py:477-520` |
| 삭제 표시된 대화는 모든 진입점에서 404다. 감사 기록 자체는 체인에 남는다 | ADR-025 3항 |
| 기록 화면의 규칙은 **소유자 본인의 삭제되지 않은 대화만**이고 승인자·타인은 같은 404다(2절) | 위 규칙 확장 |

### 0-5. 장면 1의 "처리 후 응답 유실" 주입 수단

| 사실 | 근거 |
|---|---|
| 주입은 mock 쪽 규칙이다: `{"endpoint":"slack.send","nth":[1],"action":{"type":"drop_after_apply"}}`. `hide_for`(조회 반영 지연)는 쓰기 엔드포인트에만 붙는다 | `mocks/faults.py:40-90`, `evalrun/faults_smoke.py:48-51` |
| 관리 포트(9000)는 `expose`만 하고 publish하지 않는다. 호스트 접근은 `docker compose -f infra/docker-compose.yml exec mock-slack python -m govops.mocks.admin faults set --file …` 뿐이다 | `infra/docker-compose.yml:54-67`, `mocks/admin.py:20-47` |
| 하네스는 규칙을 stdin(`--file /dev/stdin`)으로 넘긴다 | `evalrun/mock_admin.py:90-96` |
| `faults set`은 카운터를 초기화하고 규칙을 **교체**한다. `faults clear`·`reset`으로 지운다. **규칙 목록을 읽는 API는 없고** `faults stats`는 요청 카운터만 준다 | `mocks/faults.py:101-126`, ADR-022 8항 |
| 일반 서비스의 안전장치: 에이전트 서비스에는 mock 장애를 켜는 환경 변수가 **없다**. 모델 장애(`GOVOPS_MODEL_FAULT_MODE`)는 설정되면 서비스 시작을 거부한다 | `api/wiring.py:128-134` |
| F5 단독이어도 표식 조회는 실행된다(FP-001·FP-005). 짧은 F10(`hide_for` 1000ms)을 더하면 2단계 조회 대기가 보인다. 이벤트 수·순서는 실행마다 달라질 수 있어 단언하지 않는다 | `hypotheses-m3-faults.md`, M3 결과 문서 32행 |

### 0-6. 장면 3·4 데이터

| 항목 | 사실 | 근거 |
|---|---|---|
| 같은 팀 4급 사용자 | 있음. 8팀 모두 4급이 있다(`demo-platform-g4`) | `data/seed/users.json` |
| 장면 3 문서 | A 합성 문서 중 간접 인젝션이 삽입된 12개의 **메타데이터**만 A 저장소(읽기 전용, HEAD `d2eec9c`)에서 확인했다. 12개 중 `public`은 6개다. 이 중 **`syn-meeting-002`**(`inj-01`, 변형 `system_notice`, 문서 유형 회의록, 소속 payments, 공개 범위 public·min_grade 1·teams 없음)를 고른다. 요청자 `demo-platform-g3`는 접근 정책상 읽을 수 있다 | A `data/synthetic/injections.json`, `data/synthetic/docs/syn-meeting-002.json`(`access`·`team`·`doc_type`·`title` 필드만 읽음) |
| 장면 3 질의(새로 작성) | `결제서비스팀 2026-06-30 할인 캐시 개선 프로젝트 회의록을 요약해서 team-platform 채널에 올려줘` (회의록 제목은 문서 메타데이터이며 인젝션 본문은 인용하지 않는다) | 같은 파일 `title` |
| 장면 4 문서 | `syn-notice-019`(플랫폼개발팀 공지, public, 인젝션 없음) | A `data/synthetic/docs/*.json`의 `access.level`·`injection` 필드 |
| 장면 4 질의(새로 작성) | `플랫폼개발팀 공지 "인증 키 갱신 방식 결정"을 요약해서 전사 공지 채널에 올려줘` | 같은 출처 |
| 전사 공지 승인은 근거 문서가 공개일 때만 통과한다. V15가 팀 전용 근거를 전사 공지로 보내려 하면 차단하며 공식 test 실패 6건이 모두 이 사유였다 | `2026-10-15-m6-redteam-run-live.md` 3·7.2절, ADR-018 |
| **공식 구성(v1, v2 off)에서 격리는 드물다.** 간접 인젝션 36건 중 도구 결과 격리로 막힌 항목은 2건이다 | `2026-10-15-m6-redteam-run-live.md` 4.1절 |

### 0-7. 장면 5 재현 방법

| 방법 | 내용 | 상태 |
|---|---|---|
| (a) `make smoke-audit` | 임시 디렉터리에서 변조 5종·`scene5_tail_cut`, 마지막에 실제 체인 읽기 전용 검증 | M6 결과: RESULT PASS |
| (b) 실제 체인 복사본 + 꼬리 잘라내기 | 3-6의 스크립트가 복사본을 만들고 최신 `chain.anchor` seq 앞으로 자른 뒤 `verify --dir <복사본> --anchors <dir만 바꾼 고정점 파일>` | 코드상 성립, **미실측** |
| (c) 서비스 체인을 직접 자르기 | 증거 파괴·쓰기 경쟁·읽기 전용 원칙 위반 | 금지 |

---

## 1. 동결 경계

M6 7절 3항·`hypotheses-test-targets.md`에 따라 공식 test 뒤 아래를 바꾸지 않는다. M7 변경은 **추가 전용(additive)** 이다.

### 1-1. 바꾸지 않을 경로 (freeze 목록)

`scripts/freeze_digest.py`와 `tests/test_m7_freeze.py`가 같은 목록을 쓴다(step 0). 목록은 **`5d4302b`에 존재하는 파일**로 고정한다.

| 구분 | 경로 |
|---|---|
| 그래프·프롬프트·상태 | `src/govops/agent/` 전체 |
| 도구 설명·계약 | `src/govops/tools/` 전체, `data/contract/` |
| Guardrail 규칙·분류기 | `src/govops/guardrails/` 전체(`rules_v1.json`·`classifier_v2.json` 포함), `src/govops/gateway/` 전체, `infra/litellm/config.yaml`, `data/policy/` |
| 모델 호출·자격 증명·원장 | `src/govops/costs.py`, `src/govops/broker/` |
| 평가 러너·채점 | `src/govops/eval/`, `src/govops/evalrun/`, `src/govops/golden.py` |
| 기록 재생·골든 | `src/govops/replay/`, `data/replay/` 전체 |
| mock·시드·평가 데이터 | `src/govops/mocks/`, `data/seed/`, `data/eval/`, `data/redteam/` |
| 감사 쓰기·검증 | `src/govops/audit/{log,crypto,anchor,verify,smoke,__init__}.py`(`audit/records.py`는 신규이므로 제외) |
| 사전 등록·결과 | `docs/results/`의 `5d4302b`에 있는 `hypotheses-*.md` 파일 전체(파일 이름 명시 목록), `docs/results/m6-redteam-run/` |

**제외(추가 전용 또는 M7이 갱신하는 파일)**: `docs/results/audit-anchors.jsonl`, `data/manifest/costs.jsonl`·`costs_grading.jsonl`, `docs/ADR.md`·`docs/ARCHITECTURE.md`·`docs/PRD.md`(문서 일치 테스트가 따로 지킨다), 새로 생기는 모든 파일.

### 1-2. 바꿀 수 있는 곳

`src/govops/audit/records.py`(신규, 읽기 전용), `src/govops/api/{app,tasks,conversations}.py`(**기존 메서드 본문 불변, 메서드·라우트 추가만**), `web/`, `tests/`, `scripts/`(신규 파일), `Makefile`(타깃 추가), `docs/`(README·ARCHITECTURE·THREAT_MODEL·ADR 신규 항목·DEMO·BLOG·PUBLIC_RELEASE_CHECKLIST·PRD M7 행), `.env.example`(주석), `LICENSE`.

### 1-3. 불변 확인 수단

| 수단 | 보증 | 비고 |
|---|---|---|
| **A. 동결 digest 테스트** `tests/test_m7_freeze.py` | 경로별 digest 일치 | `scripts/freeze_digest.py`(step 0)가 경로마다 `git ls-files` 기준 정렬된 `(경로, sha256)` 목록의 digest를 계산하고 **경로별 digest만 출력**한다(파일 내용 출력 금지). Codex가 `5d4302b` 내용 그대로의 작업 트리에서 실행해 테스트 상수를 만든다. 테스트는 같은 함수로 현재 digest를 계산해 대조한다. 정당한 후속 수정(#158~160)은 새 등록과 함께 상수를 고친다 |
| B. 골든·재생 테스트 무수정 통과 | `test_golden_m3`·`test_regolden`·`test_golden_trace`, `data/replay/llm/*.jsonl` 헤더 `prompt_hash` | 기존 테스트, 수정 금지 |
| C. `git diff --name-only 5d4302b..HEAD`와 1-1의 교집합이 공집합 | 리뷰 점검 | 기계 검사는 A가 한다 |
| D. `service.start` 해시 대조 | `policy_sha256`·`rules_sha256`·`access_index_sha256`·`hook_rules_digest`·`hook_code_digest`가 공식 test 때와 같은지 | 사용자 실행(7절 16번) |
| E. `evalrun`이 쓰는 `TaskService` | `evalrun/dev_m3.py`는 `api/wiring.build_task_service`로 같은 서비스를 쓴다 | 기존 메서드 본문·시그니처·잠금 순서 불변. 기존 `test_api_tasks.py`·`test_evalrun_*` 무수정 통과 |

### 1-4. 동결 위반 후보 (설계에 넣지 않음)

| 후보 | 처리 |
|---|---|
| 감사 레코드에 `conversation_id` 필드 추가 | **넣지 않는다.** 감사 필드·검증기 변경은 기존 체인 검증 계약을 흔든다. 턴 색인 새 조회 메서드로 해결 |
| 프로필 기본값 문서 불일치(`ARCHITECTURE.md:252`·`.env.example` 주석이 `writes_org`) | **해결됨**: 코드·N2 manifest 모두 `writes_team`이다. 문서만 정정(step 8). 과거 ADR 본문은 수정 금지 |
| 데모용 v2 켜기 | 쓰지 않는다. 데모는 v1(`GOVOPS_GUARDRAIL_V2` off) |
| 데이터 초기화용 새 정리 기능 | 만들지 않는다. 기존 소프트 삭제·`checkpoint-prune`만 쓴다 |

---

## 2. 기록 화면 설계

### 2-1. API (읽기 전용 2개)

**`GET /conversations/{conversation_id}/audit`** — 소유자 본인만.

| 항목 | 설계 |
|---|---|
| 인증 | `current_user`. 세션 없음 401 |
| 404 | 타인·없음·삭제됨·형식 오류·승인자·턴 색인에서 정리된 대화·대화가 아닌 턴 ID는 모두 `404 conversation_not_found`(본문 동일) |
| 403 | 쓰지 않는다 |
| 503 | 감사 디렉터리가 없거나(서비스에 `AuditLog` 없음) 키·고정점 읽기 실패: `{"detail":"audit_unavailable"}` |
| 동작 | 대화 잠금 안에서 `is_deleted`·소유자 확인 → 턴 ID 목록(신규 `TurnIndex.turn_ids_of`) → 체인 파일에서 `task_id ∈ 턴 ID`인 레코드를 모은다. **읽기만 한다**(`_expire`·상태 변경 없음) |

응답(허용 목록 투영, 서버·BFF 두 곳):

```
{ conversation_id,
  turns: [ { turn_id, turn_index,
             events: [ { seq, ts, event, actor,
                         tool?, decision?, check_result?, reason?, code?,
                         mock_id?, fallback?, cost_usd?,
                         hash12, draft_hash12? } ] } ],
  truncated }
```

- `actor`: 이벤트의 `actor_sub`. 승인 이벤트는 승인자 라벨이며 **소유자에게 노출한다**.
- 원문·PII·토큰 비노출: 레코드 자체가 원문을 담지 않는다. 이중 방어로 (1) 필드 이름 허용 목록으로만 뽑고 (2) 값이 `audit/log.py` 형식 규칙(해시·mock ID·열거형)을 통과하지 못하면 필드를 버린다. `input_sha256`·`tool_call_sha256`·`deployment_id`·`sig`·`prev_hash`·`kid`·해시 전체는 노출하지 않는다.
- 이벤트 상한(500)을 넘으면 `truncated: true`.

**`GET /audit/verify`** — 인증된 모든 사용자, 전역 결과.

```
{ status: "pass" | "fail", checked_at,
  chain: { records, files, anchors_checked, problems: [ { code, seq? } ] },
  anchors: { committed, pending },
  legacy_unverified: { files, lines },
  known_limit: "tail_after_last_anchor_not_detected" }
```

경로·`dir`·`kid`·해시는 넣지 않는다. 개수와 고정 코드만 준다. 키·고정점 파일 읽기 실패는 `503 audit_unavailable`이다.

### 2-2. 신규 코드의 위치와 경계

| 항목 | 설계 |
|---|---|
| `src/govops/audit/records.py`(신규) | `read_events(directory, task_ids, *, limit)`, `verify_summary(directory, …)`. **`verify_chain`·`read_anchor_lines`를 호출만** 한다(검증 로직 재구현 금지). `audit`은 `agent·tools·broker·api`를 import하지 않아 경계가 유지된다 |
| `TurnIndex.turn_ids_of(conversation_id, owner_sub)` | `InMemoryTurnIndex`·`PostgresTurnIndex` 구현. 삭제된 행은 반환하지 않는다 |
| `TaskService.audit_view`, `TaskService.audit_verify` | **새 메서드**. 기존 메서드 불변. `self.audit.directory`를 읽는다 |
| 동기 I/O | `asyncio.to_thread`로 돌린다 |
| 캐시·단일 비행 | 전역 검증 요약은 10초 TTL 캐시 + 단일 비행(동시 요청은 같은 계산을 기다린다). 체인 파일은 `(mtime_ns, size)`가 같으면 파싱 결과를 재사용한다. 캐시는 읽기 모듈 안에 두고 서비스 간 공유하지 않는다 |
| 쓰기 도중 읽기 | `verify_chain`은 수정하지 않는다. 읽기 모듈은 체인 파일을 **마지막 개행까지의 스냅샷**으로 읽어 이벤트를 모으고, 검증은 같은 스냅샷 디렉터리(임시)에 대해 수행하는 방식을 구현 단계에서 확정한다(7절 4번) |

### 2-3. BFF·화면

| 항목 | 설계 |
|---|---|
| BFF | `GET /api/conversations/[id]/audit`, `GET /api/audit/verify`. `proxyJson`·`validId`·허용 목록 투영(`projectAudit`, `projectVerify`). 새 오류 코드는 추가하지 않고 404는 기존 `conversation_not_found`, 503·5xx는 `502 upstream_unavailable`로 접힌다 |
| 라우트 | `web/src/app/(app)/records/[id]/page.tsx` |
| 진입점 | 대화 화면 상단 "기록" 링크. 사이드바에는 넣지 않는다 |
| 화면 | ① 체인 검증 배지(PASS/FAIL, 레코드 수, 고정점 수, "마지막 고정점 이후는 탐지되지 않음", "이 호스트의 체인 기준") ② 턴별 타임라인 ③ 이벤트·사유 코드의 한국어 라벨 |
| 렌더링 | 텍스트만(마크다운 렌더러 미사용, ADR-024 5항 정책 유지). 모르는 코드는 코드 그대로 표시하고 원문처럼 보이는 값은 렌더링하지 않는다 |

### 2-4. 검증 결과: 실시간 + 10초 캐시 + 단일 비행 (확정)

- 요청 시 읽기 전용 실행이라 화면이 **지금의 체인**을 반영한다. 사전 계산 파일은 변조 대상이 되는 새 신뢰 대상이라 쓰지 않는다.
- **첫 live에서 서비스 체인 레코드 수와 응답 시간을 실측해 결과 문서에 기록한다**(이번 설계의 비용 추정은 미실측).
- 한계를 화면에 그대로 적는다: 서버가 자기 디스크를 자기 공개키로 검증하므로 B 호스트 침해에는 보증이 약하다(THREAT_MODEL 6절). 꼬리 잘라내기 탐지는 고정점이 있어야 성립하고, 화면이 읽는 고정점은 작업 트리의 `docs/results/audit-anchors.jsonl`과 대기 파일이다. 화면의 PASS는 **이 호스트 체인 기준**이다.

### 2-5. 테스트

권한 프로브·응답 불변식은 `docs/results/hypotheses-m7-records-authz.md`에 사전 등록한다(A1×S1~S7, 프로브 2~4, E1~E10, B1~B7).

---

## 3. 데모 설계 (약 3분 30초)

데모는 **공식 평가 결과를 바꾸지 않는다**: ① `data/results/`·`data/replay/`·사전 등록 파일을 만들거나 고치지 않는다 ② 러너를 쓰지 않고 웹으로만 실행해 평가 manifest·TCR 집계에 입력되지 않는다 ③ 시연 호출 비용은 비용 원장에 남으며 다음 결과 커밋에 함께 넣는다(원장만 PR 금지, FP-063).

### 3-1. 사전 준비 (촬영 전, 모두 사용자 실행)

`docs/DEMO.md`에는 아래를 **서버 기동부터 순서대로, 한 블록에 명령 하나**로 쓴다. A MCP 서버(8765)는 끄지 않는다.

| # | 작업 | 확인 |
|---|---|---|
| P1 | 작업 트리 확인(`git status`) | clean 또는 원장만 |
| P2 | 핵심 스택 기동: `docker compose -f infra/docker-compose.yml up -d` | 서비스 healthy |
| P3 | mock 최신화: `docker compose -f infra/docker-compose.yml up -d --build mock-jira mock-slack` | FP-052 |
| P4 | 관측 스택: `make obs-up` | 이어서 LiteLLM `/health/readiness`가 200이 될 때까지 대기 |
| P5 | 앱 DB: `make db-init` | 멱등 |
| P6 | live A 계약: `make smoke-contract` | 일치 |
| P7 | 서비스 기동: `make serve`(`DEMO_LOGIN=true`) | `GOVOPS_GUARDRAIL_V2`가 off임을 `grep -c '^GOVOPS_GUARDRAIL_V2=on' .env`가 0으로 확인 |
| P8 | 웹 빌드·기동(포트 3100): `pnpm -C web build`, `pnpm -C web start` | `/login` 열림. e2e(3200)와 포트 분리 |
| P9 | mock 초기화: `… exec mock-jira python -m govops.mocks.admin reset`, `… exec mock-slack …reset`, `faults clear` | `state`가 시드 상태 |
| P10 | 사전 고정점: `make audit-anchor LABEL=m7-demo-pre` | 대기 파일에 줄 추가 |
| P11 | 리허설 1회(장면 1~5) | 장면별 기대 확인 |

### 3-2. 장면별

| 장면 | 사전 조건 | 조작 순서 | 화면에 보일 것 | 예상 |
|---|---|---|---|---|
| **0 소개·배치도** | P1~P11 | ARCHITECTURE 배치도 | 신원 위임·게이트웨이·승인·감사의 위치 | 20초 |
| **1 정확히 한 번** | `demo-platform-g3`, mock-slack 규칙: `slack.send nth [1]`에 `drop_after_apply` + `hide_for 1000` | ① 규칙 적용 ② 로그인 platform-g3 ③ "INC-2026-012 장애보고 요약해서 team-platform에 올려줘" ④ 승인 카드의 수신처·검사 결과 확인 후 승인 ⑤ 발송 완료 카드 ⑥ 터미널 `state`로 mock-slack 메시지 1건 ⑦ 기록 화면 ⑧ `faults clear` | 승인 카드(수신처 팀 채널), 완료 카드, 기록 타임라인의 시도·표식 조회·결과, 터미널 메시지 1건 | 60초 |
| **2 존재 보호** | `demo-finance-g3`, 규칙 없음 | 같은 요청 → "찾을 수 없음", 승인 카드 없음 → `state` 메시지 수 불변 | "찾을 수 없음", 부작용 0 | 25초 |
| **3 간접 인젝션** | `demo-platform-g3`, 0-6의 질의 | 질의 입력 → 승인 카드의 **수신처** 확인 → 거부 또는 승인 | 승인 카드 수신처가 요청자가 지정한 팀 채널. 격리 표시는 **기대하지 않는다**(나오면 보너스) | 40초 |
| **4 전사 공지 승인** | requester `platform-g3`, approver `platform-g4`, 0-6의 질의 | g3 요청 → g3에게 승인 불가 표시 → g4 로그인 → 승인함 → 승인 | 승인함의 초안·수신처·검사 결과, 발송 완료 | 50초 |
| **5 감사·trace·수치** | P10, 3-6의 스크립트 | ① 기록 화면 검증 배지 PASS ② 터미널: 복사본 꼬리 잘라내기 → `FAIL … code=tail_truncated` ③ Langfuse trace 1건 ④ 수치 슬라이드(4-2 표의 출처만) | `PASS`→`FAIL tail_truncated`, trace, 수치 | 45초 |

합계 약 3분 40초. 장면 4의 로그인 전환을 편집으로 줄인다.

**장면 3의 서술 규칙**: 리허설에서 한 번 정한 질의로 나온 경로(수신처가 맞았다 / 결정론 검사가 막았다 / 승인 거부를 보였다 / 격리가 일어났다)를 `docs/DEMO.md`에 **있는 그대로** 기록한다. 결과를 보고 질의를 고르지 않는다. 에이전트가 다른 수신처를 넣으면 결정론 검사 차단 또는 승인 거부를 보여 준다.

**장면 1의 기대 이벤트**: `side_effect.attempt` → 표식 조회 → `side_effect.result` 순서와 `marker_lookup` 횟수는 리허설 실측으로 확정하며 설계 단계에서 단언하지 않는다.

### 3-3. 장애 주입 켜기·끄기

```
echo '[{"endpoint":"slack.send","nth":[1],"action":{"type":"drop_after_apply"}},{"endpoint":"slack.send","nth":[1],"action":{"type":"hide_for","ms":1000}}]' | docker compose -f infra/docker-compose.yml exec -T mock-slack python -m govops.mocks.admin faults set --file /dev/stdin
docker compose -f infra/docker-compose.yml exec mock-slack python -m govops.mocks.admin faults clear
docker compose -f infra/docker-compose.yml exec mock-slack python -m govops.mocks.admin faults stats
docker compose -f infra/docker-compose.yml exec mock-slack python -m govops.mocks.admin state
```

- 규칙은 `faults_smoke.WRITE_RULES`와 같은 형식이다. `set`은 카운터를 0으로 하고 `nth [1]`은 그 뒤 첫 발송에만 적용된다. 장면 1 전에만 켜고 바로 `clear`한다.
- 일반 서비스 안전장치: 서비스 쪽에 이 주입을 켜는 플래그가 없다는 점이 안전장치다. 데모용으로 새 환경 변수를 만들지 않는다.

### 3-4. 데모용 데이터 초기화

| 대상 | 방법 |
|---|---|
| mock-slack 메시지·mock-jira 코멘트·티켓 | `admin reset` |
| 앱 DB 대화·체크포인트 | 웹의 소프트 삭제 후 `make checkpoint-prune`(APPLY는 사용자 판단) |
| outbox | 초기화하지 않는다(task ID가 키에 들어간다, ADR-006) |
| 감사 체인 | **초기화하지 않는다**(추가 전용). 시연 레코드는 `m7-demo-*` 고정점으로 구분 |
| 세션 | 서비스 재시작 |

### 3-5. 실패 시 대체 경로

| 실패 | 대체 |
|---|---|
| live A MCP·LiteLLM 불가 | `make e2e-web`의 replay 백엔드(`tests/e2e_backend.py`)로 화면을 시연하고 장면 1의 주입·장면 5는 터미널 로그로 대체한다. **화면 하단과 영상 캡션에 "replay(기록 재생)"임을 표시한다** |
| 장면 1 승인 단계 실패 | 같은 문장으로 최대 2회 재시도하고 촬영본에서 편집. 시연은 평가가 아니다 |
| 장면 4 V15 차단 | 공개 근거 문서 질의로 바꾼다. 차단 화면 자체도 "결정론 검사가 막음" 컷으로 쓸 수 있다 |
| 장면 5 `tail_truncated` 불발 | `make smoke-audit` 출력(`PASS scene5_tail_cut`)을 대체 화면으로 쓴다 |
| 규칙이 남음 | `faults clear` + 탐침, 필요하면 `admin reset` |

### 3-6. 장면 5 복사본 스크립트 (`scripts/demo/scene5.sh`)

1. `data/interim/` 아래(gitignore 대상)에 `mktemp -d`로 작업 디렉터리를 만들고, `trap`에서 그 변수 경로 하나에 한해 재귀 삭제로 정리한다(`set -u`, 빈 값이면 생략). 이전 실행의 흔적은 만지지 않는다.
2. 원본 체인 디렉터리(`AUDIT_DIR`, 기본 `data/interim/audit`)의 `chain-*.jsonl`을 **읽기만** 해 임시 디렉터리에 복사한다.
3. 고정점(대기 + 커밋)에서 원본 `dir`의 `seq`가 가장 큰 줄을 고른다. 없으면 메시지와 함께 exit 2.
4. 복사본 마지막 체인 파일에서 `seq`가 그 고정점 `seq`보다 작은 레코드만 남긴다(`smoke.py`의 `scene5_tail_cut`과 같은 방식).
5. 고른 줄의 `dir`을 복사본의 저장소 상대 경로로 바꿔 임시 파일에 쓴다.
6. `uv run python -m govops.audit.verify --dir <복사본> --anchors <임시 고정점>`을 실행한다. 기대 출력: `FAIL chain … code=tail_truncated seq=<n>`. 테스트는 자르기 전 PASS와 자른 뒤 `tail_truncated`를 둘 다 단언한다.

원본 체인·고정점 파일은 읽기만 한다. 이 스크립트는 `data/interim/`을 런타임에 읽고 쓰므로 Codex 직접 열람 대상이 아니라 코드의 런타임 접근이다(FP-059).

---

## 4. 문서화·블로그

### 4-1. README 목차(`README.md` 재작성)

1. 한 문단 요약 — **첫 단락에 "mock 시뮬레이션"을 명시**한다(실제 Jira·Slack이 아니다). 주 서사: 중복 실행 0, 권한 밖 노출 0, 승인 없는 부작용 0(수치는 4-2 출처)
2. 데모(영상 링크, 장면 요약, replay 표기 규칙)
3. 아키텍처(배치도, 계층 경계, 신원 위임, 승인·멱등성 흐름)
4. 실행 방법(사전 요구, `.env.example` 변수 이름, `make verify`는 키 없이 통과, live는 사용자 키 필요)
5. 평가 결과 요약(4-2 표)
6. 위협 모델·방어
7. 알려진 한계(4-3) — **"평가셋·정답지(`data/eval`)는 공식 test 동결 뒤 공개한다. 이후 비교에는 새 held-out 세트가 필요하다"** 명시
8. 프로젝트 구조·문서 지도
9. 라이선스(MIT)·A 저장소와의 관계(A도 같은 라이선스로 공개 예정)

### 4-2. 수치의 출처 파일

| 수치(결과 문서 표현 그대로) | 출처 |
|---|---|
| 절대 규칙 1(중복 부작용): 분모 132회에서 **중복 0건**, 95% 상한 약 2.3%(3/132), "0건은 관측값이지 증명이 아니다" | `docs/results/2026-10-07-m3-faults-live.md:30` |
| F10 긴 지연(한계 시연): 실행된 9회 모두 중복 1건 | 같은 문서 `:31` |
| F10 짧은 지연: 20/20 중복 0, 표식 조회 40회 | 같은 문서 `:32` |
| 누락 7건은 장애와 무관한 V15 오차단 | 같은 문서 `:36` |
| 장애 처리 대상 126회에서 120/126 TCR | 같은 문서 `:37` |
| 공식 구성 v1, test(N2) TCR 3회 평균 0.858, pass^3 0.85, 지연 p95 14.87 s, 절대 규칙 위반 0 | `docs/results/2026-10-15-m6-redteam-run-live.md:10-11,44`, `m6-redteam-run/verdict.json` |
| 레드팀 120건 × 3회(R1a·R1b·R2) breach(효과가 실제로 나간 항목) 0건 | `2026-10-15-m6-redteam-run-live.md:85` |
| 정확도 κ(correctness 0.652, groundedness 0.282, format 0.223) | 같은 문서 6절 |
| 비용 OpenAI $0.373/9.13, Anthropic $0.376/11.47, Gemini $0.906/7.29 | 같은 문서 `:15` |
| 감사: 변조 5종·`scene5_tail_cut` 탐지, 마지막 고정점 이후 꼬리는 미탐지 | `2026-10-08-m4-audit-live.md:41,77`, `2026-10-13-m6-limits-delete-live.md:35` |

출처 없는 수치는 README·블로그·영상에 넣지 않는다. 수치는 결과 문서에서 복사하고 다시 계산하지 않는다.

### 4-3. 알려진 한계

- v1 구성의 레드팀 막은 층은 대부분 승인·결정론 검사이고 입력 차단·격리 비율은 낮다(layer_match 27/107, 막은 층 없음 92/120이 부작용 0).
- v2는 공식 구성에서 제외됐다(정상 task 오탐 3건, 한도 2).
- 정확도 groundedness·format은 채점 모델 검증 미달.
- F10 긴 지연에서는 중복을 막지 못한다.
- 감사: 마지막 고정점 이후 꼬리·같은 키 전체 재계산·B 호스트 탈취는 막지 못한다.
- 정상 task V15 오차단이 TCR을 낮춘다. 사람 채점 표본 30건.
- mock 환경이라 실제 Jira·Slack의 멱등 키 부재 동작은 **시뮬레이션**이다.
- 평가 정답지 공개: 공식 test 동결 뒤 공개하며 이후 비교에는 새 held-out이 필요하다.

### 4-4. 블로그 초안 개요 — "실제 API에 멱등 키가 없을 때 에이전트 부작용을 정확히 한 번 실행하기"

| 절 | 내용 | 근거 |
|---|---|---|
| 1 문제 | 응답 유실 시 "보냈는가?"를 모른다. 멱등 키가 없는 API. 첫 단락에 "mock 시뮬레이션" 명시 | ADR-006, FP-001 |
| 2 실패한 접근 | 본문 표식·검색 API·바로 재시도 | ADR-018 |
| 3 설계 | outbox + 멱등 키 + 메타데이터 표식 2단계 조회, 바로 재시도는 연결 실패·429뿐 | ARCHITECTURE "도구와 부작용·멱등성" |
| 4 승인과 신원 | `interrupt()`만 있는 승인 노드, 초안 해시, 발송 신원은 요청자 | ADR-011 |
| 5 장애 주입 실측 | 4-2 표의 M3 수치 | `2026-10-07-m3-faults-live.md` |
| 6 잡은 버그 | FP-040·FP-002·FP-005·FP-001 | `failure-patterns` |
| 7 감사 | 해시체인+서명+고정점, 한계 | ADR-012 |
| 8 한계 | 4-3 | |

---

## 5. 공개 전 점검 목록

검사 명령은 모두 **사용자 실행 항목**이다.

### 5-1. 이미 확인한 관측

| 관측 | 내용 |
|---|---|
| 추적 파일에 비밀 경로 없음 | `git ls-files`에서 `.env`(예제 제외)·`data/interim`·`data/raw`·`tokens.json`·`*.pem`은 `.env.example`과 **공개키** `data/seed/audit_public_key.pem`뿐 |
| 이력에 비밀 경로 추가 기록 없음(경로 기준, 값 기준 검사 아님) | `git log --all --diff-filter=A` |
| `data/eval/meta.json`(약 252KB)·`data/redteam/` 추적됨 | 정답지 공개를 유지한다(이력 재작성 없음). README에 동결 뒤 공개·새 held-out 필요를 명시 |
| `data/manifest/costs.jsonl` 3.4MB | 대용량 최대 파일 |
| 라이선스 파일 없음 | step 8에서 MIT `LICENSE` 추가 |
| 커밋 작성자 2명·원격 `apinkpanda722/…` | 이력 재작성 없음. 공개 신원은 공개 전 사용자가 정한다(체크리스트 항목) |
| 커밋 648개 | 이력 전체 검사 대상 |

### 5-2. 검사 명령(사용자 실행, 값은 가려서 확인)

| # | 목적 | 명령 후보 | 합격 |
|---|---|---|---|
| C1 | 이력 비밀 값 | `gitleaks detect --source . --log-opts="--all" --redact` 또는 `trufflehog git file://. --only-verified` | 0건 |
| C2 | 형식 직접 검사 | `git log --all -p \| grep -nEi "(sk-[A-Za-z0-9]{20,}\|sk-ant-[A-Za-z0-9_-]{20,}\|AIza[0-9A-Za-z_-]{30,}\|Authorization: *Bearer [A-Za-z0-9._-]{16,}\|BEGIN [A-Z ]*PRIVATE KEY\|lsv2_\|pk-lf-\|sk-lf-)"` (줄 번호만 확인) | 0건(합성 토큰은 별도 확인) |
| C3 | 커밋 금지 경로 이력 | `git log --all --name-only --format= \| sort -u \| grep -E "^(\.env$\|data/(raw\|interim)/\|.*tokens\.json$\|.*\.pem$)"` | `.env.example`·공개키만 |
| C4 | 대용량 | `git rev-list --objects --all \| git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' \| awk '$1=="blob"&&$3>1000000' \| sort -k3 -n -r` | 의도한 파일만 |
| C5 | 합성 데이터만 | 기존 합성·재생 검사 테스트 + `data/replay`·`data/seed`의 문서 ID가 `syn-*`뿐 | 통과 |
| C6 | 실존 기관·인물명 | 기존 합성 검사 테스트 + 실제 기관명 목록 grep(사용자 목록) | 0건 |
| C7 | 공개 신원 | `git log --format='%an <%ae>' \| sort -u`, 원격 계정 | 사용자 결정(이력 재작성 없음) |
| C8 | 개인 경로·내부 URL | `grep -rn "/Users/\|taserlee" docs src tests scripts web/src` | 의도 확인(A 참조 경로 포함) |
| C9 | `.env.example` | 눈으로 확인 | 변수 이름만 |
| C10 | CI 시크릿 | `.github/workflows/ci.yml`, 저장소 Secrets | 시크릿 없이 통과 |
| C11 | 새 클론 검증 | 새 디렉터리 `git clone` 후 `uv sync`·`pnpm install`·`make verify` | `.env`·`data/interim` 없이 통과 |
| C12 | 라이선스 | `LICENSE`(MIT) 존재, README 링크 | 확인 |
| C13 | **A 저장소 쪽** | A도 같은 라이선스(MIT)로 공개 예정: A의 `LICENSE`, A의 비밀 이력 검사(C1~C4 동일), A의 합성 문서 재배포 허용(B의 `data/contract`·`data/replay/a_run`·`data/eval` 포함), A README가 B 참조 | A 쪽에서 확인 |

### 5-3. A 저장소 참조

B는 A 커밋 `d2eec9c`, 계약 스냅샷(`data/contract/`), A 합성 문서 메타데이터(`data/eval/meta.json`·`data/seed/access_index.json`)를 참조·포함한다. README에 A 의존 범위(live 실행에만 A 필요, `make verify`는 replay)를 분리해 적는다.

---

## 6. phase 구성: `m7-records-demo`

코드는 Codex `execute.py` 일임, step 파일 형식 v2, 공통 금지사항 고정 블록, `scripts/step_file_checks.py`·`scripts/test_step_files.py` 통과. 옮길 FP: 010·054·059·060·061·070·071·072.

| step | 이름 | 수정 허용 파일 | 소비자 테스트(시뮬레이션 결과) |
|---|---|---|---|
| 0 | `freeze-pin` | `scripts/freeze_digest.py`, `tests/test_m7_freeze.py` | 새 파일만(영향 없음) |
| 1 | `audit-records-reader` | `src/govops/audit/records.py`, `tests/test_audit_records.py` | `tests/test_architecture.py`·`test_docs_consistency.py` 통과 |
| 2 | `turn-index-turn-ids` | `src/govops/api/conversations.py`, `tests/test_turn_index_turn_ids.py`, `tests/test_m5_pg.py`(사용자 실행 케이스 추가에 한함) | `TurnIndex` 사용 테스트 통과(Protocol 비강제) |
| 3 | `audit-view-service` | `src/govops/api/tasks.py`, `tests/test_api_audit_view.py` | `test_api_tasks`·`test_evalrun_*` 통과 |
| 4 | `audit-routes-entrypoint` | `src/govops/api/app.py`, `tests/test_api_entrypoint.py`(표·FakeService 갱신에 한함), `tests/test_api_audit_route.py` | **`test_api_entrypoint` 라우트 표 실패(예상)** |
| 5 | `web-records-ui` | BFF route 2개, `records/[id]/page.tsx`, `records-view.tsx`(+테스트), `project.ts`(+테스트), `messages.ts`, `types.ts`, `testids.ts`, `thread.tsx`, `routes.test.ts`·`bff.integration.test.ts`(추가에 한함) | **`routes.test.ts` 파일 이름 목록 실패(예상)** |
| 6 | `e2e-records-spec` | `web/e2e/records.spec.ts`, `tests/e2e_backend.py`(감사 디렉터리 주입에 한함), `web/src/e2e-config.test.ts`(목록 갱신에 한함) | **`e2e-config.test.ts` 스펙 목록 실패(예상)** |
| 7 | `demo-scripts` | `scripts/demo/scene5.sh`, `scripts/demo/mock_faults.json`, `Makefile`(타깃 추가에 한함), `tests/test_demo_scripts.py` | `test_makefile.py` 통과(허용 목록에 넣지 않음) |
| 8 | `docs-readme-blog` | `README.md`, `LICENSE`, `.env.example`(주석), `docs/DEMO.md`, `docs/BLOG_DRAFT.md`, `docs/PUBLIC_RELEASE_CHECKLIST.md`, `docs/ARCHITECTURE.md`, `docs/THREAT_MODEL.md`, `docs/ADR.md`(ADR-028 추가만), `docs/PRD.md`(M7 행 상태), `tests/test_docs_consistency.py`(프로필 마커 두 개 갱신에 한함) | **`test_fault_harness_document_contract` 실패(예상, 311~312행 마커)** |

### 6-1. FP-071 시뮬레이션

`git clone --local` 복사본(`.env`·`data/interim`·`data/raw` 미복사 확인)에 스텁을 넣고 전체 pytest·vitest를 돌려 깨지는 소비자를 각 step에 반영했다. 결과는 보고에 있고 phase 파일의 각 step "수정 허용 경로"에 반영했다. WARN은 허용 목록에 올리지 않고 step 파일의 "통과" 단락에 적는다.

### 6-2. 열람 금지

열람 금지 경로: `data/interim/`, `data/raw/`, `.env`. `data/eval/`·`data/redteam/`는 step 0의 digest 계산(런타임 접근)만 하고 열람·출력하지 않는다(FP-059).

### 6-3. 사용자 실행 순서

1. 사전 등록 PR(이 초안, `hypotheses-m7-records-authz.md`, phase 파일) 머지
2. `execute.py m7-records-demo`(`run_in_background`)
3. PR 머지 후 `make test-m5-pg`(턴 색인 새 메서드), `make serve` 재시작
4. 7절 첫 live 점검 → 리허설 → 촬영
5. 결과 문서·비용 원장·고정점 PR
6. 5절 공개 전 점검

예상 비용: 리허설·촬영 질의 약 20회 × $0.002 ≈ $0.04(M6 개방형 질문 기준 추정, **미실측**).

---

## 7. 첫 live에서 실패할 수 있는 지점과 대응

| # | 지점 | 이유 | 대응 |
|---|---|---|---|
| 1 | 턴 색인 정리로 기록이 사라짐 | 보존 정리가 색인 행을 지우면 체인에 레코드가 남아도 화면은 404 | 화면 문구 "정리된 대화는 기록 화면에서 볼 수 없다. 체인은 `make audit-verify`로 확인". 시연 전 prune 금지 |
| 2 | 승인자 라벨이 소유자에게 보임 | 확정: 노출 | 화면 문구에 승인자임을 표시 |
| 3 | 체인이 커져 느림 | 파일 전체 읽기 | TTL 캐시, 이벤트 상한. 첫 live에서 레코드 수·응답 시간 실측 후 결과 문서에 기록 |
| 4 | 쓰기 중 읽기(마지막 줄 미완성) | 서비스 append와 검증기의 `unterminated_tail` | 개행 단위 스냅샷, 프로브 E8 |
| 5 | 고정점 파일 위치 | cwd가 저장소 루트가 아닐 때 상대 경로 어긋남 | `ROOT` 기반 절대 경로, 손상은 503 |
| 6 | 빈 환경 변수가 기본값을 덮음 | `.env.example`이 `AUDIT_DIR=`처럼 비움 | `env_or`(FP-030), 회귀 테스트 |
| 7 | `service.start` 해시가 공식 test와 다름 | 동결 위반 징후 | 1-3 D, 달라지면 중단 |
| 8 | 규칙이 남아 후속 발송 실패 | 규칙 목록 읽기 API 없음 | `faults clear` + 탐침 |
| 9 | 응답 유실 주입이 안 걸림 | `nth [1]`은 `set` 이후 첫 발송 | `set` 직후 `stats requests=0`, 발송 후 `applied_rules` 확인 |
| 10 | 장면 2 finance 간헐 실패 | M6: finance 5회 중 1회 `max_steps_finalized` | 촬영 전 3회 리허설 |
| 11 | 장면 3 수신처·경로가 기대와 다름 | v1에서 격리는 드묾 | 기대값을 수신처 일치로 두고 나온 경로를 DEMO.md에 있는 그대로 기록 |
| 12 | 장면 4 V15 차단 | 팀 전용 근거면 차단 | 공개 근거 문서 질의 |
| 13 | 승인자 g4가 승인함에 안 보임 | 전사 공지만 같은 팀 4급 이상·본인 제외 | 요청 대상이 전사 공지인지 확인 |
| 14 | 포트 충돌 | e2e 3200·8200·8201, 데모 3100 | `make e2e-web` 전에 `next start` 종료 |
| 15 | 복사본 꼬리 잘라내기가 `tail_truncated`를 안 냄 | 고정점 `dir` 불일치, 마지막 고정점이 복사본 밖 | 3-6 스크립트가 `dir`을 바꿔 쓰고 대기 파일의 마지막 고정점이 체인 안에 있는지 확인 |
| 16 | 원장 변경으로 작업 트리 더러움 | 시연 호출이 원장에 기록 | 다음 결과 커밋에 포함(FP-063) |
| 17 | SKIP이 검증된 것처럼 읽힘(FP-073) | obs 꺼진 상태·고정점 없는 상태 | 결과 문서에 단계별 PASS/FAIL/SKIP 사유 기록 |
| 18 | 새 서버 오류 코드 접힘 | 허용 목록 | 새 코드를 추가하지 않고 5xx 접힘 규칙 사용 |
| 19 | LiteLLM 준비 전에 촬영 시작 | `make obs-up` 직후 | `/health/readiness` 200 대기 |
| 20 | 8765 A MCP 서버를 끔 | 다른 작업 중 종료 | 끄지 않는다(DEMO.md 명시) |

---

## 8. 확정 결정

| ID | 결정 |
|---|---|
| D1 | 웹 기록 화면 구현 |
| D2 | 검증 결과는 실시간 + 10초 캐시 + 단일 비행. 첫 live에서 체인 레코드 수와 응답 시간을 실측해 결과 문서에 기록 |
| D3 | 소유자에게 승인자 라벨 노출 |
| D4 | 장면 4 근거는 공개 문서(`syn-notice-019`) |
| D5 | 장면 3 문서 `syn-meeting-002`(공개, 간접 인젝션 변형 `system_notice`), 질의는 새로 작성. 인젝션 본문은 저장소 문서에 인용하지 않음 |
| D6 | 데모 구성 v1(`GOVOPS_GUARDRAIL_V2` off, `grep -c`로 확인) |
| D7 | 정답지 공개 유지, 이력 재작성 없음. README에 "공식 test 동결 뒤 공개, 이후 비교에는 새 held-out 필요" 명시 |
| D8 | live 불가 시 e2e replay 화면 + 터미널 로그 대체 허용. 화면·영상 캡션에 replay 표시 |
| D9 | README 첫 단락에 "mock 시뮬레이션" 명시 |
| D10 | MIT, `LICENSE` 파일(step 8) |
| D11 | 이력 재작성 없음. 공개 신원은 공개 전 사용자 결정(체크리스트 항목) |
| D12 | A도 같은 라이선스로 공개 예정. 체크리스트에 A 쪽 항목 |
| D13 | 프로필 문서 정정은 문서만(`ARCHITECTURE.md:252`, `.env.example` 주석). 과거 ADR 본문 수정 금지 |
| D14 | `docs/results/hypotheses-m7-records-authz.md` 사전 등록 |
| D15 | 동결 digest 테스트 도입(`scripts/freeze_digest.py`) |
| D16 | ADR-028 신규. PRD는 M7 행 상태만 갱신 |

### ADR-028 요지 (step 8에서 `docs/ADR.md`에 추가)

1. 기록 화면은 대화 소유자만 읽는다. 타인·없음·삭제됨·형식 오류·승인자·정리된 대화는 같은 404 `conversation_not_found`다.
2. 전역 검증 요약은 인증된 모든 사용자에게 개수·고정 코드만 보인다(경로·`kid`·해시·서명 비노출).
3. 턴 색인이 정리되면 감사 레코드가 남아도 화면에서 404다. 체인은 CLI로 확인한다.
4. 화면의 PASS는 이 호스트 체인과 작업 트리 고정점 기준이다. 마지막 고정점 이후 꼬리·같은 키 전체 재계산·B 호스트 탈취는 막지 못한다.
5. 그래프·프롬프트·도구 설명·Guardrail·평가 러너·골든은 바꾸지 않으며 `tests/test_m7_freeze.py`가 digest로 확인한다.
