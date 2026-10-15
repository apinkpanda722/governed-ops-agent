# M7 기록 화면 엔드포인트 권한 프로브 사전 등록

작성 시점: `m7-records-demo` phase 실행 전 (2026-10-15). live 실행 없음.
결과를 본 뒤 바꾸지 않는다. 이 파일은 phase 실행 전에 커밋한다(FP-043). 기존 사전 등록 파일(`hypotheses-m6-api-authz.md` 등)은 수정하지 않는다.

## 1. 범위와 이유

기록 화면은 감사 체인의 일부를 사용자에게 읽기 전용으로 보여 주는 새 경계다.

- `GET /conversations/{conversation_id}/audit`: 대화 소유자 본인의 턴별 감사 이벤트(허용 필드만).
- `GET /audit/verify`: 서비스 체인 검증의 전역 요약(개수·고정 코드만).

타인의 기록을 읽거나, 존재 여부를 알아내거나, 삭제·정리된 대화의 기록에 접근하거나, 원문·해시 원본·서명·서버 경로를 얻는 우회를 막는지 확인한다.

- 에이전트·LLM·외부 API를 호출하지 않는다. 결정론 pytest(와 BFF의 vitest)만 쓰며 `make verify` 안에서 실행된다.
- 레드팀 120건·평가셋·골든·기존 사전 등록은 바꾸지 않는다.
- 판정은 이진이다: 아래 프로브가 모두 통과하면 통과, 하나라도 실패하면 실패한다. 목표 수치는 두지 않는다.

## 2. 프로브

테스트는 실제 서버 진입점(`api/__main__.py`의 `build_server_config` + lifespan)을 거치고 lifespan이 주입한 실제 `TaskService`를 쓴다(자리표시자 서비스가 응답하면 500이므로 모든 프로브의 상태 코드는 500이 아니다, FP-070). 감사 체인은 테스트 전용 임시 디렉터리·임시 키로 만든 합성 체인이다.

**라우트 표**

| ID | 라우트 | 404 본문 |
|---|---|---|
| A1 | `GET /conversations/{id}/audit` | `{"detail": "conversation_not_found"}` |
| A2 | `GET /audit/verify` | (404 없음, 인증만 요구) |

**대상 표 (A1)**

| ID | 대상 | 준비 |
|---|---|---|
| S1 | 타인의 대화 | 다른 사용자가 만든 `completed` 대화 ID. 프로브 사용자는 요청자가 아니다 |
| S2 | 존재하지 않는 id | 형식은 맞는(32 hex) 무작위 ID |
| S3 | 삭제된 대화 | 소유자가 삭제한 대화의 첫 턴 ID |
| S4 | 형식이 잘못된 id | `not-a-valid-id`, 대문자 hex, 길이 31 등 |
| S5 | 승인자의 대상 | 승인 대기 중인 타인의 대화. 프로브 사용자는 그 대화의 정책상 승인자다 |
| S6 | 정리된 대화 | 턴 색인에서 행이 제거된 뒤(보존 정리) 체인에는 이벤트가 남은 대화의 ID |
| S7 | 두 번째 턴 ID | 소유자 본인 대화의 두 번째 턴 ID(대화 ID가 아니다) |

**프로브 1: 응답 동일성.** A1 × S1·S2·S3·S4·S5·S6·S7은 모두 404이고 본문이 `{"detail": "conversation_not_found"}`와 정확히 같다. 서로 구별되지 않는다. 오류 본문에 제목·상태·소유자·이벤트 수 정보가 없다.

**프로브 2: 소유자 성공 본문.** 소유자가 자기 대화 ID로 요청하면 200이고 다음을 만족한다.

- 최상위 키 집합은 `conversation_id`·`turns`·`truncated` 뿐이다.
- 각 이벤트의 키는 `seq`·`ts`·`event`·`actor`·`hash12` 와 허용 목록(`tool`·`decision`·`check_result`·`reason`·`code`·`mock_id`·`fallback`·`cost_usd`·`draft_hash12`)의 부분집합이다.
- 응답 본문 전체에 다음이 없다: 64 hex 해시 전체, 128 hex 서명, `kid`, `prev_hash`, `input_sha256`, `tool_call_sha256`, `deployment_id`, 절대 경로 문자열, 합성 토큰·`Authorization` 문자열.
- 다른 대화의 턴 ID에 속한 이벤트가 하나도 없다(같은 체인에 다른 사용자 이벤트를 넣어 둔다).
- 승인 이벤트의 `actor`는 승인자 라벨이다(소유자에게 노출).

**프로브 3: 필드 방어.** 체인에 허용 목록 밖 필드(`text`·`body`)를 직접 심은 합성 레코드(서명은 유효하지 않아도 읽기 경로는 허용 필드만 투영한다)를 넣어도 응답에 그 필드가 없다. 값이 형식 규칙(해시·mock ID·열거형)을 통과하지 못하면 그 필드는 버려진다.

**프로브 4: 읽기 전용.** A1을 승인 대기·실행 중·완료 대화에 호출해도 대화 상태·`expires_at`·감사 체인 길이·효과가 변하지 않는다(`_expire`를 호출하지 않는다).

**추가 프로브**

| ID | 프로브 | 기대 |
|---|---|---|
| E1 | 세션 없음(인증 헤더 없음)으로 A1·A2 | 401, 본문 `{"detail": "Authentication required"}` |
| E2 | `AuditLog`가 없는 서비스(디렉터리 없음)로 A1 소유자 요청 | 503 `{"detail": "audit_unavailable"}` |
| E3 | A2 정상 체인 | 200, `status: "pass"`, 키 집합 `status`·`checked_at`·`chain`·`anchors`·`legacy_unverified`·`known_limit`, `chain`의 키는 `records`·`files`·`anchors_checked`·`problems` |
| E4 | A2 체인 변조(수정·중간 삭제·순서 변경·고정점 후 꼬리 잘라내기·다른 키 서명) 각각 | 200, `status: "fail"`, `problems`에 `hash_mismatch`·`seq_gap`·`seq_order`/`prev_hash_mismatch`·`tail_truncated`·`kid_unknown` 중 해당 코드 |
| E5 | A2 응답 본문에 경로·`kid`·해시·서명이 있는지 | 없다. `problems`의 원소 키는 `code`·`seq`뿐이다 |
| E6 | 고정점 파일이 손상된 상태에서 A2 | 503 `audit_unavailable`, 본문에 사유 문장·경로 없음 |
| E7 | 같은 체인에 A2를 10초 안에 여러 번 동시 호출 | 검증 실행이 1회이고(단일 비행·캐시) 응답 본문이 모두 같다 |
| E8 | 마지막 줄이 개행 없이 끝난 체인(쓰기 도중 상태)에 A1·A2 | 500이 아니다. A1은 완성된 줄까지의 이벤트를 돌려주고 A2는 체인 FAIL로 단정하지 않는다(`in_progress` 취급은 구현이 정하되 응답 코드는 200 또는 503) |
| E9 | 삭제 성공 뒤 같은 대화의 A1 | 404, 감사 체인 레코드는 그대로 남아 있다 |
| E10 | A1·A2 호출 뒤 체인 파일의 크기·`mtime_ns` | 변하지 않는다 |

**BFF 프로브(vitest, 소켓 스텁 업스트림)**

| ID | 프로브 | 기대 |
|---|---|---|
| B1 | 쿠키 없이 `GET /api/conversations/{id}/audit`·`GET /api/audit/verify` | 401, 업스트림 호출 0건 |
| B2 | Origin 불일치 | 403, 업스트림 호출 0건 |
| B3 | 형식이 잘못된 id | 404, 업스트림 호출 0건 |
| B4 | 업스트림 404 | 404 `conversation_not_found`로 투영 |
| B5 | 업스트림 5xx·503 | 502 `upstream_unavailable` |
| B6 | 업스트림이 허용 목록 밖 필드·64 hex 해시·`kid`를 섞어 돌려줄 때 | 브라우저 응답에 없다(투영이 제거) |
| B7 | 응답 헤더·본문에 `govops_session` 토큰 값 | 없다 |

## 3. 구현 위치

- 읽기 모듈 단위: `tests/test_audit_records.py`(step 1)
- 서비스 단위: `tests/test_api_audit_view.py`(step 3)
- 실제 서버 진입점 경유 HTTP 프로브(A1×S, E1~E10): `tests/test_api_audit_route.py`(step 4), 라우트 결선: `tests/test_api_entrypoint.py`
- BFF: `web/src/app/api/bff.integration.test.ts`(step 5)

## 4. 변경 절차

프로브를 추가하는 것은 허용되지만 이 파일을 수정하지 않고 새 등록 파일을 만든다. 프로브의 기대값을 실행 결과에 맞춰 바꾸지 않는다.
