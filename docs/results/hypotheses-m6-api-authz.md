# M6 대화 삭제 엔드포인트 권한 우회 프로브 사전 등록

작성 시점: `m6-limits-delete` phase 실행 전 (2026-10-13). live 실행 없음.
결과를 본 뒤 바꾸지 않는다. 이 파일은 phase 실행 전에 커밋한다(FP-043).

## 1. 범위와 이유

대화 삭제(`DELETE /conversations/{conversation_id}`, 소프트 삭제)가 새 권한 경계를 만든다. 타인의 대화를 지우거나, 존재 여부를 알아내거나, 삭제된 대화에 다시 접근하는 우회를 막는지 확인한다.

- 레드팀 120건(`data/redteam/set.json`, `set_sha256` 고정, 검수 확정본)은 **바꾸지 않는다**. 이 프로브는 그 세트에 추가하지 않고 별도 등록한다.
- 에이전트·LLM·외부 API를 호출하지 않는다. 결정론 pytest(와 BFF의 vitest)만 쓰며 `make verify` 안에서 실행된다. 실제 API 호출은 없다.
- 판정은 이진이다: 아래 프로브가 모두 통과하면 통과, 하나라도 실패하면 실패한다. 목표 수치는 두지 않는다.

## 2. 프로브

테스트는 실제 서버 진입점(`api/__main__.py`의 `build_server_config` + lifespan)을 거치고 lifespan이 주입한 실제 `TaskService`를 쓴다(자리표시자 서비스가 응답하면 500이므로 모든 프로브의 상태 코드는 500이 아니다, FP-070).

**라우트 표**

| ID | 라우트 | 대화 라우트의 404 본문 |
|---|---|---|
| R1 | `GET /conversations/{id}` | `{"detail": "conversation_not_found"}` |
| R2 | `GET /conversations/{id}/events`(SSE) | `{"detail": "conversation_not_found"}` (스트림을 열지 않는다) |
| R3 | `POST /conversations/{id}/turns` | `{"detail": "conversation_not_found"}` |
| R4 | `DELETE /conversations/{id}` | `{"detail": "conversation_not_found"}` |
| R5 | `GET /tasks/{id}` | `{"detail": "Task not found"}` |
| R6 | `POST /tasks/{id}/approval` | `{"detail": "Task not found"}` |

**대상 표**

| ID | 대상 | 준비 |
|---|---|---|
| S1 | 타인의 대화 | 다른 사용자가 만든, 승인 대기가 아닌(`completed`) 대화 ID. 프로브 사용자는 그 팀의 승인자가 아니다 |
| S2 | 존재하지 않는 id | 형식은 맞는(32 hex) 무작위 ID |
| S3 | 삭제된 대화 | 소유자가 삭제한 대화의 **첫 턴 ID**와 **두 번째 턴 ID** 둘 다 |
| S4 | 형식이 잘못된 id | 길이·문자가 틀린 값(`not-a-valid-id`, 대문자 hex, 길이 31 등) |

**프로브 1: 응답 동일성.** R1~R6 × S1~S4의 조합 중 **R6 × S1을 뺀** 모든 조합에서 상태 코드가 404이고 본문이 위 표의 라우트별 값과 정확히 같다. 같은 라우트에서 S1·S2·S3·S4의 응답이 서로 구별되지 않는다(R2는 404이고 스트림이 열리지 않는다). R6에서는 S2·S3·S4가 404 `Task not found`로 서로 같다(삭제 확인은 승인 권한 판정보다 먼저 한다). 오류 본문에 제목·상태·소유자 정보가 없다.

**R6 × S1(예외)**: 승인 권한은 기존 `approver_decision` 규칙(`api/tasks.py`의 `_decision`, FORBIDDEN이면 403 "Approval rejected")이 정한다. 이 규칙은 이번 변경에서 바꾸지 않는다. 이 칸의 기대값은 "응답이 200이 아니고 대화의 상태·감사 로그·효과에 변화가 없다"이다.

**추가 프로브**

| ID | 프로브 | 기대 |
|---|---|---|
| E1 | 승인 대기 중인 자기 대화를 삭제 | 409, 본문 `{"detail": "approval_pending"}`. 대화는 삭제되지 않고 감사 이벤트 `conversation.deleted`가 없다 |
| E2 | 실행 중인 자기 대화를 삭제 | 409, 본문 `{"detail": "turn_in_progress"}`. 삭제되지 않고 감사 이벤트 없음 |
| E3 | 세션 없음(인증 헤더 없음)으로 R1~R6 | 401, 본문 `{"detail": "Authentication required"}` |
| E4 | 두 번째 턴 이후의 `turn_id`로 `DELETE /conversations/{turn_id}` | 404(그 ID는 대화 ID가 아니다), 대화는 삭제되지 않는다 |
| E5 | 삭제 성공 뒤 같은 대화가 `GET /conversations` 목록과 `GET /approvals`(소유자·다른 승인자 둘 다)에 나타나는지 | 나타나지 않는다 |
| E6 | 삭제 두 번째 시도 | 404이고 감사 이벤트 `conversation.deleted`가 늘지 않는다 |
| E7 | 소유자의 삭제 성공 | 200 `{"conversation_id": …, "deleted": true}`, 감사 이벤트 정확히 1건(`task_id`=대화 ID, `actor_sub`=요청자, 다른 필드 없음) |
| E8 | 만료된 승인 대기 중인 자기 대화를 삭제 | 만료를 먼저 닫은 뒤 삭제 성공(200) |

**BFF 프로브(vitest, 소켓 스텁 업스트림)**

| ID | 프로브 | 기대 |
|---|---|---|
| B1 | 쿠키 없이 `DELETE /api/conversations/{id}` | 401, 업스트림 호출 0건 |
| B2 | Origin 불일치 | 403, 업스트림 호출 0건 |
| B3 | 형식이 잘못된 id | 404, 업스트림 호출 0건 |
| B4 | 업스트림 404·409(`approval_pending`·`turn_in_progress`) | 같은 상태와 코드로 투영 |
| B5 | 업스트림 5xx | 502 `upstream_unavailable` |

## 3. 구현 위치

- 서비스 단위: `tests/test_api_conversation_delete.py`(step 3)
- 실제 서버 진입점 경유 HTTP 프로브(R×S, E1~E8): `tests/test_api_conversation_delete_route.py`(step 4), 라우트 결선: `tests/test_api_entrypoint.py`
- BFF: `web/src/app/api/bff.integration.test.ts`(step 5)

## 4. 변경 절차

프로브를 추가하는 것은 허용되지만 이 파일을 수정하지 않고 새 등록 파일을 만든다. 프로브의 기대값을 실행 결과에 맞춰 바꾸지 않는다.
