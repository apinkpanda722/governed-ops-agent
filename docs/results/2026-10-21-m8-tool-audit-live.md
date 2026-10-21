M8 추가 — 공식 M6 수치 아님

# M8 감사 체인 도구 호출 기록 live 결과 (m8-tool-audit)

날짜: 2026-10-21. 지시 47(사용자 결정 2026-10-21, 동결 경계 변경 승인). 조립된 서비스에서 모델이 낸 tool_call마다 감사 레코드를 하나 이상 남기는 변경과, 그 변경 뒤 첫 live 확인이다. 공식 M6 수치는 변경 전 코드로 낸 값이며 바꾸지 않았다. 실행 결과는 모두 **사용자 보고**(main `e0d15ad`)이고, 이 세션이 직접 실행한 것은 `make verify`·CI와 비용 원장 변경분 집계뿐이다. 응답·요청 본문과 키 값은 열람하지 않았다.

**사전 등록**(코드 전 커밋, FP-043)

| 등록 | 커밋 | 내용 |
|---|---|---|
| [도구 호출 감사 등록](hypotheses-m8-tool-audit.md) | `86e94fd` | 경로 대응·레코드 형식·HMAC·키 검사·fail-open·동결 경계·대조 규칙·화면 노출·고정 코드·한계 |
| ADR-029(`docs/ADR.md`) | `86e94fd` | ADR-012 감사 범위를 읽기 도구 호출까지 넓힘 |

설계 초안: [`M8_TOOL_AUDIT_DRAFT.md`](../M8_TOOL_AUDIT_DRAFT.md)(PR #247).

## 1. 한눈에 보기

| 항목 | 결과 | 출처 |
|---|---|---|
| 키 검사와 기동 | `make serve`가 키 검사를 통과해 정상 기동 | 사용자 보고 |
| `make audit-toolcall-check` | `PASS audit_toolcall_check turns=8 tool_calls=14 spans=14` | 사용자 보고 |
| `make audit-verify` | `chains_pass=634 chains_fail=0 legacy_unverified_files=40 anchors_checked=275`, `anchor_dir_missing` 0건(M8 ⑤ 결과 폴더를 원래 경로로 되돌린 뒤, 3절) | 사용자 보고 |
| 오늘 체인(`chain-2026-10-21.jsonl`) 이벤트 줄 수(첫 smoke 직후) | 33줄: `tool.call` 7, `model.call` 11, `side_effect.draft` 4, `service.start` 1 | 사용자 보고(어드바이저가 줄 수만 확인) |
| `make smoke-conversation` | 2회 모두 14개 PASS + `FAIL no_leak_in_views contract_mismatch`(원인 미확정, 4절) | 사용자 보고 |
| 비용(두 번의 smoke) | 정산 48행: 주 모델(OpenAI, `agent_primary`) 24행 $0.002635, fallback(Anthropic, `agent_fallback`) 24행 $0. 예약 48행 | 이 세션이 원장 변경분에서 합계·행 수만 집계 |
| `make verify`·CI | phase step마다 exit 0, PR #249 CI pass | 이 세션 |

검증 범위: 이번 live로 확인한 것은 **smoke 한 번 분량의 실제 대화에서 `tool.call`이 기록되고, 체인이 서명·연결 검증을 통과하고, 실행 계열 `tool.call` 수가 trace 도구 span 수와 같았다**는 것까지다. 경로별 완전성(9개 경로)은 합성 테스트(`tests/test_agent_tool_audit.py`)로 검증했다. 운영 부하에서의 append 비용은 측정하지 않았다(설계 때 합성 측정 중앙값 0.15 ms·p95 0.22 ms).

## 2. 구현 요약

| step | 커밋 | 내용 |
|---|---|---|
| 0 `tool-audit-registration` | `86e94fd` | 사전 등록, ADR-029, `test_m8_freeze` 키 |
| 1 `audit-tool-event` | `9bf3c01` | `tool.call` 형식(`audit/log.py`), `audit/argmac.py`(전용 키 HMAC·kid), `audit/toolcall.py`(기록기) |
| 2 `graph-tool-audit` | `a20d7bd` | 그래프 기록 지점 3곳(읽기 실행, 무효 제안, 마무리 단계 미실행), `build_graph(tool_audit=None)` |
| 3 `wiring-args-key` | `bbb110a` | `build_task_service`의 키 검사(감사 기록 전)와 기록기 주입, 합성 키 테스트 환경, `.env.example` |
| 4 `records-tool-view` | `25aca86` | 기록 화면 서버 투영(도구 이름·상태·결과 수, 해시 비노출) |
| 5 `web-tool-view` | `35819b7` | BFF 투영·라벨·기록 화면 |
| 6 `toolcall-check` | `34e8aef` | `make audit-toolcall-check`(읽기 전용 대조) |

### 2-1. digest 변경 이력

| 동결 키(`tests/test_m7_freeze.py`) | 이전 | 새 | 변경 커밋 |
|---|---|---|---|
| `src/govops/agent` | `f801700264f23227445cc0dd1d248da52a2af9708b004ba2341e9bc598c9d58e` | `7746520a965d32294df474524a19988f6d966d1111b446dfbf991d97fcbe7637` | `a20d7bd` |
| `src/govops/audit/log.py` | `014309c3b447ef2e34af338b53e39f1ef77d99fd5945cef03ea070f12acbf22d` | `19765856f3f96aa13f6fb7abe071452b5566e8c07c59563329f17976a28949b2` | `9bf3c01` |

다른 동결 경로(tools·gateway·guardrails·eval·evalrun·`audit/crypto.py`·`verify.py`·`smoke.py`·data·`infra/litellm/config.yaml`)는 diff가 없다. `HOOK_FILES`에 두 파일이 없어 `service.start` 해시는 바뀌지 않는다. 골든·재생 테스트는 수정 없이 통과했다.

## 3. 사용자 실행에서 드러난 절차 결함

### 3-1. 키 생성 명령의 줄바꿈

등록 문서 12절과 설계 초안 10절의 키 생성 명령 `python3 -c "… print('AUDIT_ARGS_HMAC_KEY=' + …)" >> .env`는, `.env` 마지막 줄 끝에 줄바꿈이 없자 키를 앞 줄(`GOVOPS_GUARDRAIL_V2`) 뒤에 이어 붙였다. `grep -c '^AUDIT_ARGS_HMAC_KEY='`가 0이었고, 줄 어디서든 찾으면 1이었다. 어드바이저가 값을 보지 않고 변수 이름과 길이(앞 변수 값 3자, 키 값 64자)로 확인했다. 사용자가 키 앞에 줄바꿈을 넣어 고쳤고, 두 확인(키 줄, `GOVOPS_GUARDRAIL_V2=off` 줄)이 모두 1이었다(사용자 보고).

- 설계 초안 10절은 동결 대상이 아니어서 줄바꿈을 먼저 붙이는 명령으로 고쳤다. 등록 문서 12절은 동결이라 고치지 않고 여기에 정정 사실로 적는다.
- 이후 사용할 명령(줄바꿈 없는 임시 파일로 새 줄에 들어감을 확인함):

```
python3 -c "import secrets; print('\nAUDIT_ARGS_HMAC_KEY=' + secrets.token_hex(32))" >> .env
```

### 3-2. 결과 폴더 이동과 `audit-verify`

1차 `make audit-verify`는 `chains_pass=594 chains_fail=0`이었지만 `anchor_dir_missing` 40건으로 종료 코드 1이었다(사용자 보고). 원인은 M8 ⑤ 통제·후보 실행 폴더(각 20 attempt)를 FP-067 절차로 `data/interim/m8-regress/`로 옮겨, 고정점이 가리키는 `data/results/dev/<run_id>`가 없어진 것이다. 두 폴더를 임시로 `data/results/dev/`로 되돌리고 재실행하니 1절의 값으로 통과했고, 그 뒤 다시 옮겼다(M5 결과 문서 2026-10-08과 같은 선례).

- **한계(해결책은 정하지 않음)**: `audit-verify`는 고정점의 원래 경로에 폴더가 있어야 통과한다. 그래서 FP-067 절차대로 결과 폴더를 `data/interim`으로 옮기면 `audit-verify`가 깨진다.

## 4. smoke 실패 (원인 미확정, 이슈 #250)

`make smoke-conversation` 2회 모두 14개 PASS와 `FAIL no_leak_in_views contract_mismatch`였다(사용자 보고). 어드바이저의 읽기 전용 진단(사용자 실행, 키 경로만 출력)으로 확인한 사실은 다음과 같다.

- `safe()` 판정이 걸린 곳은 응답 하나의 `turn.request` 값에 smoke의 SENTINEL이 들어 있는 것 하나였다.
- 대화 목록·승인함·각 대화의 현재 화면에는 금지 키와 SENTINEL이 없었다.
- 차단 확정 뒤 `blocked_card_shape`는 PASS(`request` None)였다.

smoke가 차단 요청을 보낸 뒤 poll이 받은 `running` 상태의 요청자 본인 화면으로 **추정**하지만, 상태를 직접 캡처하지 않았다.

- **이번 변경 때문이라는 증거는 없다.** 차단된 요청은 도구를 부르지 않는 경로라 `tool.call` 기록 지점이 없다. 다만 이 smoke는 M5(2026-10-09, 15개 PASS) 이후 다시 돌린 적이 없고, 변경 전 커밋(`0f5d8eb`)에서 재현해 비교하지 않았다.
- 별도 이슈 #250으로 추적하며 이번에 고치지 않았다. 수정 방향(차단 확정 화면에서만 본문 유무를 보고, 차단 확정 화면이 실제로 관측됐는지 확인)은 검사 기준을 바꾸는 일이라 사용자 결정을 기다린다.

## 5. 진행 이력

| PR | 내용 |
|---|---|
| #247 | 설계 초안과 phase 파일(지시 47) |
| #249 | phase 구현(step 0~6) |
| 이 PR | 결과 문서, 설계 초안 키 생성 명령 정정, m8-rollout 결과 문서 한 줄, 비용 원장 |

- phase 실행 중 blocked 1회(step 1): `tests/test_audit.py::test_events`가 `EVENTS` 전체를 `test_audit_events.CASES`의 필드로 실제 기록하는데, step 파일의 허용 범위가 이를 빠뜨렸다(FP-071). `tool.call`만 합성 필드로 기록하고 1줄 기록을 단언하도록 허용 범위를 고쳐(`38529a0`) 재실행했다. `CASES`에 넣는 방법은 시스템 이벤트 분류(`test_identity`)와 필드 범위 테스트(`tool_call_sha256`)와 충돌해 쓰지 않았다.
- **설계 중 사고(고정 제약 위반)**: append 비용을 처음 잴 때 벤치마크 파일을 `tests/` 밖에 두어 conftest의 임시 감사 키가 적용되지 않았다. 그 결과 기본 경로의 실제 감사 서명 키(`data/interim`)로 pytest 임시 디렉터리에 200줄이 서명됐다. 키는 출력·복사하지 않았고 그 임시 체인 파일(201줄)은 바로 삭제했다. 보고한 측정값은 `tests/` 아래 임시 키로 다시 잰 값이다. 재발 방지: 측정·실험 코드는 `tests/` 아래 conftest 합성 키 환경에서만 돌리고, 밖에서 돌려야 하면 `AUDIT_DIR`·감사 키 경로 환경 변수를 임시 디렉터리로 먼저 지정한다.

## 6. 한계

등록 11절을 그대로 옮기고 이번 live에서 드러난 것을 더한다.

- 키를 가진 운영자는 추측 값을 대조할 수 있다(HMAC은 키 보유자에 대한 보호가 아니다).
- 키를 잃으면 과거 해시를 검증할 수 없다. 키를 교체하면 `args_hmac_kid`가 바뀌고, 옛 kid의 레코드는 옛 키로만 검증된다. 키 백업은 사용자 몫이다.
- 읽기와 쓰기의 인자 해시 방식이 다르다(읽기 HMAC, 쓰기 무염 sha256). 쓰기의 무염 해시는 승인 결합과 동결 때문에 유지한다.
- 그래프를 직접 조립한 경로(단위 테스트)에서는 `tool.call`을 남기지 않는다. 완전성은 `build_task_service`로 조립한 서비스에서만 보장한다.
- `tool.call` 기록은 fail-open이다. 감사 쓰기 실패 시 trace의 `tool.audit_gap`으로만 남는다.
- 대화 화면의 이벤트 표시 상한 500개에 더 빨리 도달한다.
- 이 변경 전의 실행(공식 M6·M8 측정)의 감사 체인에는 `tool.call`이 없다.
- 결과 폴더를 `data/interim`으로 옮기면 `audit-verify`의 고정점 검사가 실패한다(3-2절).
- live 확인은 smoke 한 번 분량(8턴, 실행 계열 `tool.call` 14개)뿐이다. 운영 부하에서의 append 비용과 장시간 실행은 확인하지 않았다.

## 7. 하지 않은 것

- 변경 전 커밋에서 smoke를 재현해 4절의 실패와 비교하지 않았다.
- 롤아웃을 켠 서버(`GOVOPS_V2_ROLLOUT=on`)에서 smoke나 `audit-toolcall-check`를 돌리지 않았다.
- 공식 M6 평가나 M8 측정을 다시 실행하지 않았다. 그 체인에는 `tool.call`이 없다.

## 8. README·포트폴리오 표현 범위

- 쓸 수 있는 것: "모델의 모든 도구 호출(읽기 포함)을 위변조 탐지 가능한 감사 체인에 남기고, 인자는 전용 키 HMAC으로만 기록하며, trace의 도구 span 수와 개수를 대조하는 점검을 두었다."
- 함께 쓸 것: 공식 M6 수치는 변경 전 코드의 값이고, live 확인은 smoke 한 번 분량이며, 기록은 fail-open이라는 점.
- 쓰지 않는 것: 운영 부하에서 검증했다는 표현, 키 보유자에 대해서도 인자를 보호한다는 표현.
