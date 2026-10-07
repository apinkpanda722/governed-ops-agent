# Step 8: redteam-gen

## 읽어야 할 파일

- `/AGENTS.md`(고정 제약, 계층 경계 표의 `eval/`·`mocks/` 행), `/docs/ADR.md`(ADR-010·ADR-017 1·3항), `/docs/EVAL.md`
- **사전 등록(읽기 전용)**: `/docs/results/hypotheses-m4-redteam.md`, `/data/redteam/allocation.json`, `/docs/results/hypotheses-m4-guardrail.md`(`rules_sha256` 줄)
- `/src/govops/guardrails/registry.py`(**이 파일의 `rules_sha256`만 import한다**), `/src/govops/eval/taskgen.py`·`/src/govops/eval/request_text.py`·`/src/govops/eval/tone.py`(결정론 템플릿·시드 사용 방식 선례), `/src/govops/mocks/__main__.py`(`env_or("MOCK_JIRA_TICKETS", …)`로 읽어 `load_tickets`에 넘김, 59행 부근·`env_or` import 10행)·`/src/govops/mocks/seed.py`(`load_tickets`, `TICKET_FIELDS`, 키 검증)·`/src/govops/mocks/jira.py`(`JiraState`, 티켓 접근 필드), `/data/seed/tickets.json`·`/data/seed/users.json`·`/data/seed/access_index.json`(스키마만 본다), `/src/govops/agent/org_info.py`
- `/tests/test_architecture.py`, `/tests/test_mock_jira.py`·`/tests/test_mocks.py`(기동 경로 테스트: `MOCK_JIRA_TICKETS`를 `monkeypatch.setenv`로 지정, 106행 부근)·`/tests/test_env_defaults.py`(`test_variable_defaults` 표와 기본값 스캐너)·`/tests/test_seed_extract.py`·`/tests/test_golden_trace.py`·`/tests/test_smoke_tools.py`(`load_tickets` 소비자)
- `/.claude/skills/failure-patterns/SKILL.md`(FP-010·032·042·043·050) — 읽기만 한다
- **외부 계약 실측**(원자료: 위 파일들, 성공 여부와 반환 내용을 따로 적는다):

| 지점 | 성공 여부 | 반환·효과 내용 |
|---|---|---|
| `mock-jira`의 시드 로딩 | 환경 변수 `MOCK_JIRA_TICKETS`(기본 시드 경로)를 읽는다 | 티켓은 접근 필드(`access_level`·`access_teams`·`access_min_grade`)로 사용자에게 보이고, 읽을 수 없는 티켓은 404와 같은 응답이다. 티켓 응답에는 `comments`·`children`이 들어 있어 `jira_get_ticket`으로 본문·코멘트를 모두 모델이 읽을 수 있다 |
| Slack 메시지 | 모델 도구에 읽기 경로 없음 | 레드팀의 전달 경로로 쓰지 않는다(등록 문서) |
| `rules_sha256()` | 순수 함수, 성공 | 규칙 파일 바이트의 sha256 hex 64자. 규칙 내용은 돌려주지 않는다 |

## 작업

**수정할 파일**(이 목록이 전부다): `src/govops/eval/redteam.py`(신규), `src/govops/eval/redteam_templates.py`(신규), `src/govops/mocks/__main__.py`·`src/govops/mocks/seed.py`(둘 다 오버레이 로딩 추가에 한함), `src/govops/mocks/jira.py`(오버레이 때문에 꼭 필요할 때만, 그 밖의 변경 금지), `data/redteam/set.json`(신규, 생성 산출물), `data/redteam/manifest.json`(신규), `data/redteam/overlay/jira_tickets.json`(신규), `Makefile`(`redteam-gen` 타깃 추가만), `tests/test_eval_redteam.py`(신규), `tests/test_redteam_boundary.py`(신규), `tests/test_mocks_overlay.py`(신규), `tests/test_makefile.py`(타깃 확인 추가에 한함), `tests/test_mocks.py`·`tests/test_mock_jira.py`·`tests/test_env_defaults.py`(오버레이 때문에 필요한 기대값 갱신과 신규 케이스 추가에 한함: `test_env_defaults.py`에는 `MOCK_JIRA_OVERLAY` 행 추가. **단언 약화 금지**). `seed.py::load_tickets`의 시그니처·반환값은 바꾸지 않는다(소비자: `tests/test_seed_extract.py`·`tests/test_golden_trace.py`·`tests/test_smoke_tools.py`. 새 함수를 추가한다). 계층: `eval/`은 `gateway`·`broker`를 import할 수 없고, **이 step은 `eval/redteam*.py`가 `govops.guardrails.registry` 외의 `guardrails` 모듈(`screen`·`injection`·`pii`·`policy`)을 import하지 못하게 경계 테스트로 고정한다.** `mocks/`는 `govops`의 다른 계층을 import할 수 없다. TDD.

열람 금지 경로: `src/govops/guardrails/rules_v1.json`, `src/govops/guardrails/injection.py`, `src/govops/guardrails/screen.py`
수정 허용 경로: `src/govops/eval/redteam.py`, `src/govops/eval/redteam_templates.py`, `src/govops/mocks/__main__.py`, `src/govops/mocks/seed.py`, `src/govops/mocks/jira.py`, `data/redteam/set.json`, `data/redteam/manifest.json`, `data/redteam/overlay/jira_tickets.json`, `Makefile`, `tests/test_eval_redteam.py`, `tests/test_redteam_boundary.py`, `tests/test_mocks_overlay.py`, `tests/test_makefile.py`, `tests/test_mocks.py`, `tests/test_mock_jira.py`, `tests/test_env_defaults.py`

### 1. 생성기 (`eval/redteam.py`, `eval/redteam_templates.py`)

- CLI `python -m govops.eval.redteam`(`make redteam-gen`, 오프라인·결정론, 키·네트워크 불필요. **이 step에서 실행해 `set.json`·`manifest.json`·`overlay/`를 생성한다**): `data/redteam/allocation.json`을 읽어 120건을 만들어 `set.json`, `manifest.json`, `overlay/jira_tickets.json`을 쓴다. **같은 입력이면 바이트 단위로 같은 출력**이다(시드 20261005, `random.Random(seed)`를 항목·변형별로 분리해 쓰고 전역 난수·시간·`uuid`를 쓰지 않는다. 쓰기는 정렬된 JSON, 줄바꿈 고정).
- 시작 가드(순서대로, 안전 오류 코드): `redteam_registration_not_committed`(`hypotheses-m4-redteam.md`·`allocation.json`이 커밋되어 있지 않거나 등록 커밋 이후 변경됨. `git`으로 확인, 등록 문서의 `allocation_sha256`과 파일이 다르면 `allocation_changed`), `guardrail_rules_not_registered`(`hypotheses-m4-guardrail.md`의 `rules_sha256`이 현재 `rules_sha256()`과 다름). 가드 실패 시 아무것도 쓰지 않는다.
- 항목(`set.json`): 등록된 필드 `id`(`RT-D-01`·`RT-I-01`·`RT-P-01`(신원 사칭)·`RT-X-01`(PII) 형식), `category`, `variant`, `delivery`, `expected_layer`, `fallback_layers`, `requester`(`users.json`의 사용자 라벨. 팀·직급은 시드로 균등 분배하되 간접 항목은 오버레이 티켓을 **읽을 수 있는** 사용자), `request`(요청 문장), `payload_ref`(간접: 오버레이 티켓 키, 그 밖은 `null`), `spec_sha256`(항목 내용의 sha256). **항목 안에 평가용 정답·판정 결과·Guardrail 판정은 넣지 않는다.**
- **간접 인젝션 40건**: 요청 문장은 정상 업무 요청(예: 티켓 요약·후속 조치 문의)이며 **오버레이 티켓 키(`payload_ref`와 같은 값)를 문장에 포함**해 에이전트가 그 티켓에 결정론적으로 닿게 한다(티켓 키는 `syn-` 문서 ID가 아니므로 문서 ID 금지 규칙과 충돌하지 않는다). 그리고 공격 지시는 `overlay/jira_tickets.json`의 티켓 본문(`jira_ticket_body`) 또는 코멘트(`jira_ticket_comment`)에만 있다. 오버레이 티켓은 `data/seed/tickets.json`과 같은 스키마이고 키는 기존 키와 겹치지 않는 번호 대역(예: 프로젝트 키 + 9001부터)을 쓴다. 요청자가 읽을 수 있는 접근 필드로 둔다.
- 템플릿(`redteam_templates.py`): 각 변형마다 문장 틀을 **여러 개**(변형당 최소 3개 틀 + 어휘 슬롯)로 두고 한국어를 기본으로 `ignore_en`·`prompt_leak` 등 영어 변형을 포함한다. `obfuscated_ko`는 제로폭 삽입·공백 삽입·전각·자모 분해·영한 혼용을 **변환 함수**로 적용한다. 정규화 후 중복되는 항목이 없다. 문장은 정상 업무 말투(`eval/tone.py` 선례)와 섞어도 되지만 내부 코드·평가 용어를 노출하지 않는다.
- **합성 데이터 규칙(검사 테스트로 강제)**: 실존 기관·사람 이름 없음(역할·가상 팀 이름만), PII는 형식만 맞는 비실존 값(전화 `010-0000-NNNN`, 카드는 공개된 시험용 번호, 주민등록번호는 **실재하는 날짜 + 일부러 틀린 검증 숫자**: 주민등록번호 검증식(가중치 2·3·4·5·6·7·8·9·2·3·4·5, 검증 숫자 = (11 − 합 % 11) % 10)에 맞지 않는 마지막 숫자를 쓴다. 하이픈 있는 형태와 없는 형태를 모두 만든다. 비현실적인 날짜는 쓰지 않는다), 실제 규정 원문 없음, 모든 오버레이 문서 ID·티켓 키는 합성 대역.
- `manifest.json`: `{"seed", "allocation_sha256", "rules_sha256"(= `registry.rules_sha256()` 호출 결과 한 줄), "set_sha256", "overlay_sha256", "counts": {범주: n}, "layers": {expected_layer: n}, "deliveries": {delivery: n}}`. 이 외에 규칙 내용·패턴 정보를 쓰지 않는다.
- **생성기는 Guardrail로 항목을 걸러내거나 조정하지 않는다**(`screen`·`injection`을 쓰지 않는다).

### 2. mock-jira 오버레이 로딩 (`mocks/__main__.py`·`mocks/seed.py`)

- 선택 환경 변수 `MOCK_JIRA_OVERLAY`(경로)는 `__main__.py`가 이미 import한 `govops.env.env_or`로 읽는다(`env_or("MOCK_JIRA_OVERLAY", "")`, 빈 값·공백은 미설정, FP-030). **`os.environ.get`·`os.getenv`에 기본값을 넘기지 않는다**(`tests/test_env_defaults.py` 스캐너). 로딩은 `__main__.py`가 `MOCK_JIRA_TICKETS` 시드를 `load_tickets`로 읽은 **직후**, `seed.py`에 추가하는 새 함수(예: `merge_overlay(base, overlay_path)`; 오버레이 파일은 `load_tickets`와 같은 검증을 통과해야 한다)로 한다. 설정되면 오버레이의 티켓을 **덧붙인다**. 키가 시드와 겹치면 시작 시 `ValueError`로 거부한다(조용한 덮어쓰기 금지). 설정이 없으면 동작·응답이 **이전과 완전히 같다**(기존 mock 테스트 전부 통과, 상태 스냅샷 동일을 테스트로 고정). compose에는 이 변수를 설정하지 않는다(M6에서 연결). 기본 시드 파일 `data/seed/*`는 수정하지 않는다.

### 3. 테스트

- 개수·배분: 범주 30·40·20·30, 변형 개수, `expected_layer`·`fallback_layers`·`delivery`가 `allocation.json`과 일치, `id` 고유, 정규화 후 `request`·오버레이 본문 중복 없음.
- 결정론: 두 번 생성하면 세 산출 파일이 바이트 동일. 가드: 등록 파일 변경·규칙 해시 불일치·미커밋이면 아무것도 쓰지 않음(가짜 git).
- 합성 규칙 검사: 금지어(실존 기관·인물 이름 목록은 테스트가 가진 작은 금지 목록으로 확인), PII 값이 허용 형식(비실존 패턴)임을 형식으로 확인(주민등록번호: 날짜가 실재하고 검증 숫자가 검증식에 **맞지 않음**을 계산으로 확인, 전화 `010-0000-NNNN`, 카드는 공개 시험 번호 목록 안), 간접 항목의 `request`에 `payload_ref` 티켓 키가 포함됨, 모든 간접 항목의 `payload_ref`가 오버레이에 있고 `requester`가 그 티켓을 읽을 수 있음(시드 접근 규칙으로 확인).
- 경계(`tests/test_redteam_boundary.py`): ① `eval/redteam*.py`의 `govops` import는 `govops.guardrails.registry`뿐(AST). ② `eval/redteam*.py`에 문자열 `rules_v1`·`screen`·`injection` import가 없다. ③ `guardrails/`·`gateway/`·`agent/`·`api/`의 소스에 `data/redteam`·`eval.redteam`·`redteam` 문자열이 없다(Guardrail 코드가 레드팀 데이터를 읽지 않는다). ④ `manifest.json`의 `rules_sha256`이 현재 `rules_sha256()`과 같다. ⑤ **등록이 생성보다 앞선다**(execute.py는 step이 끝난 뒤에 커밋하므로 이 step의 verify 시점에는 `set.json`이 아직 커밋되지 않았을 수 있다). 판정 모드를 먼저 정한다: `set.json`이 git에 추적되지 않으면 `pre_commit`, 추적되면 `post_commit`. `pre_commit`: 등록 파일 3개(`hypotheses-m4-guardrail.md`·`hypotheses-m4-redteam.md`·`allocation.json`)가 모두 **HEAD 역사에 이미 커밋**되어 있고(`git log -- <파일>`이 비어 있지 않음) 작업 트리가 HEAD와 같다(`git diff HEAD --quiet -- <파일>`). `post_commit`: 세 등록 파일을 처음 추가한 커밋이 `set.json`을 처음 추가한 커밋의 **조상**이다(`git merge-base --is-ancestor`). **어느 모드에서도 판정 대상 없이 통과하지 않는다**: 테스트가 실제로 실행한 모드를 단언(`assert mode in {"pre_commit", "post_commit"}`)하고, 얕은 복제(`git rev-parse --is-shallow-repository`가 true)이거나 git 역사를 읽을 수 없으면 건너뛰지 않고 **실패**한다(`shallow_clone_cannot_verify`; CI는 `fetch-depth: 0`이다, FP-032). 두 모드 각각과 얕은 복제를 가짜 git(임시 저장소)으로 테스트한다.
- mock 오버레이: 설정 없음 → 동일 동작, 설정 있음 → 티켓이 접근 규칙대로 보임·읽을 수 없는 사용자에게 404, 키 충돌 → 시작 거부, 빈 값 → 미설정.

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

- `src/govops/guardrails/`의 규칙 파일·패턴 모듈(`rules_v1.json`·`injection.py`·`screen.py`)의 **내용**을 Codex가 직접 열람·출력·인용하거나 생성기·템플릿에서 import하지 마라(`registry.rules_sha256`만 예외). **경계 테스트가 `guardrails/` 소스를 프로그램으로 읽어 문자열·import를 검사하는 것은 허용한다.** 이유: 규칙을 본 상태에서 만든 세트는 차단율을 부풀린다(FP-042·043). 항목을 Guardrail로 걸러 보거나 통과 여부로 조정하지도 마라.
- 등록된 `allocation.json`·`hypotheses-m4-redteam.md`·`hypotheses-m4-guardrail.md`를 고치지 마라(읽기 전용 사전 등록). 이유: 등록 뒤 배분 변경은 새 등록 문서가 필요하다. 배분과 다르게 생성되면 코드를 고친다.
- `data/seed/*`(기본 시드)와 `docs/results/`의 다른 파일을 수정하지 마라. 이유: 간접 인젝션은 오버레이 파일로만 전달한다(A는 수정 불가, 기본 시드 불변).
- 전역 난수·시간·`uuid`·네트워크·LLM 호출을 쓰지 마라. 이유: 템플릿 결정론 생성이어야 재현된다(생성 후 같은 입력은 바이트 동일). 무작위가 필요하면 항목·변형별로 분리한 시드 난수만 쓴다(FP-011).
- PII·인명·기관명을 실존 가능성이 있는 값으로 만들지 마라. 이유: 합성 기록 규칙(AGENTS.md CRITICAL). PII 값은 형식 검사 테스트가 비실존 패턴임을 확인한다(FP-010: 이 값들에 PII 정규식을 돌려 정상 판정하는 것은 아니다).
