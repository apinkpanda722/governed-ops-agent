# M8 feature flag 점진 배포 추가 등록 1 (`hypotheses-m8-rollout-addendum-1.md`)

**M8 추가 측정 — 공식 M6 수치 아님** · `scope: "m8"`

기준 등록: [`hypotheses-m8-rollout.md`](hypotheses-m8-rollout.md)(동결, 수정하지 않음). 이 문서는 코드 변경보다 먼저 커밋하며, 커밋 뒤 본문을 수정하지 않는다(FP-043). `tests/test_m8_freeze.py`가 digest를 고정한다. 사용자 승인 2026-10-21.

이 추가 등록은 롤백 사유 하나를 더하고 평가 구현 하나를 바꾼다. 임계(0.05·0.10·8000 ms), 창 K=20, 최소 표본 20, 후보 60, 꼬리 5000줄, SALT, 기록 키, CLI 인자는 그대로다. 아직 운영 실행 결과는 없다(로컬 시연 PASS 2경로는 이 변경 뒤 사용자가 다시 실행한다).

## 1. 분류 기록 손상 fail-safe (규칙 추가)

- **발견**: m8-rollout 구현 검토(PR #239)에서, `classify.jsonl`에 깨진 줄이 있으면 `store.read_classify_tail`의 `ValueError`가 배정 경로 밖으로 올라가 롤아웃을 켠 상태에서 새 대화 생성이 실패함을 확인했다. 기준 등록은 이 경우의 처리를 정하지 않았다.
- **규칙**: 롤백 평가 중 다음 중 하나가 일어나면 롤백 사유 `rollback_records_invalid`로 롤백한다.
  - `store.read_classify_tail`이 `ValueError` 또는 `OSError`를 낸다.
  - 기록의 `status` 때문에 `policy.evaluate`가 `ValueError`를 낸다.
- **순서**: 기준 등록 11절 (a)(`insufficient_v2_sample`)보다 **앞**이다. 기록을 읽을 수 없으면 안전을 판단할 수 없으므로 표본 수와 관계없이 롤백한다.
- **롤백 처리**: 기준 등록 11절과 같다. `state.json`을 `pct_bp=0, kill=true, rolled_back_at=<UTC ISO>, reason="rollback_records_invalid"`로 쓰고, 노출 기록에 `rollback` 행(`reason`, 롤백 직전 `pct_bp`)을 남기며, 이번 배정은 `v1`이다. 상태 쓰기도 실패하면 기존 규칙대로 이번 배정만 `v1`이고 예외는 올리지 않는다.
- **코드 상수**: `policy.ROLLBACK_CODES`는 `evaluate`가 내는 4개 그대로 두고, 이 사유는 별도 상수 `policy.RECORDS_INVALID_CODE = "rollback_records_invalid"`로 둔다.

## 2. 고정 코드 목록 추가 (기준 등록 14절에 더함)

| 구분 | 코드 |
|---|---|
| 평가 | `rollback_records_invalid` |

## 3. 평가 구현 변경 (결과 동일)

- 기준 등록 9절은 분류 기록을 "`turns.conversation_of(turn_id)`로 대화에 연결"한다고 적었다. 이 방식은 꼬리의 고유 `turn_id`마다 한 번씩 조회해 평가 한 번에 최대 5000회를 부른다.
- 같은 대응(turn → 대화)을 `turns.turn_ids_of(conversation_id, owner_sub)`로 구한다. v2 창의 대화(최대 K=20개)마다 한 번 불러 turn_id 집합을 만들고, 그 집합에 속한 기록만 그 대화에 연결한다. `owner_sub`는 창을 고를 때 읽는 상태의 `user_id`다. 평가 한 번에 `conversation_of`는 0회, `turn_ids_of`는 K 이하다.
- 결과는 같다: 소프트 삭제되지 않은 대화에서는, 같은 기록·같은 창에서 각 대화의 `ConversationOutcome`(턴별 분류 호출)이 기준 방식과 같다. `TurnIndex` 프로토콜은 바꾸지 않는다.
- 차이(한계): `turn_ids_of`는 소프트 삭제된 턴(`deleted_at`이 있는 행)을 돌려주지 않지만 `conversation_of`는 돌려준다. 그래서 창 안에 소프트 삭제된 대화가 있으면 그 대화의 분류 기록은 이 방식에서 연결되지 않는다(그 대화는 상태 조회 경로에서 이미 404다). 이 대화의 차단 여부는 기존과 같이 창에 들어간다.

## 4. 사용자 실행

변경 머지 뒤(블록당 한 명령):

```
make m8-rollout-exercise
```
