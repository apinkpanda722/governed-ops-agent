# M4 감사 로그 live 확인 결과 (smoke-audit, audit-anchor, audit-verify)

대상 구현: [ADR-012](../ADR.md)의 해시체인 + Ed25519 서명 + 고정점(PR #77, main `e4fe852`). 고정점 줄은 [audit-anchors.jsonl](audit-anchors.jsonl)에 있다. 이 확인은 네트워크·API 키·LiteLLM 없이 로컬에서 실행했고 원래 폴더의 git 상태는 변하지 않았다(`data/interim/`은 gitignore).

## 실행 조건

| 항목 | 값 |
|---|---|
| 날짜 | 2026-10-08 |
| 모드 | 로컬 live 확인(실제 서명 키, 실제 `data/interim/audit` 디렉터리). 외부 API 호출 없음 |
| 실행 시점 코드 | main `e4fe852`(PR #77 머지, 작업 트리 깨끗) |
| 실행 순서 | `make smoke-audit` → `make audit-anchor LABEL=m4-audit-first` → `make audit-verify` |
| 서명 키 | kid `02580ff2e0eb`(커밋된 공개키 `data/seed/audit_public_key.pem`의 DER SHA-256 앞 12자와 일치), 개인키 파일 모드 0600, 짝 일치 |
| 감사 디렉터리 | `data/interim/audit`: 디렉터리 모드 0700, 파일 모드 0600 |
| 첫 체인 | `chain-2026-10-08.jsonl` 레코드 2건: `chain.genesis` seq 1036, `chain.anchor` seq 1037 |
| genesis가 봉인한 legacy | 2파일: `2026-10-06.jsonl` 10줄, `2026-10-07.jsonl` 1025줄, 합 1035줄(`legacy_last_seq` 1035) |

## `make smoke-audit` 출력

```
uv run python -m govops.audit.smoke
PASS key
INFO kid=02580ff2e0eb
INFO key_mode=0600
INFO key_pair=match
PASS chain_basic
INFO records=4 kid=02580ff2e0eb
PASS tamper_modify
PASS tamper_delete_middle
PASS tamper_reorder
PASS tamper_tail_truncate
PASS tamper_other_key
PASS scene5_tail_cut
INFO known_limit=tail_after_last_anchor_not_detected
PASS real_chain
INFO records=2 anchors=1
INFO legacy_unverified files=2 lines=1035 (체인 없음, 미검증)
RESULT PASS
```

- 변조 5종(수정, 중간 삭제, 순서 변경, 꼬리 잘라내기, 다른 키 서명)이 각각 기대한 문제 코드로 탐지됐다. 합격 조건은 종류마다 기대 코드가 정확히 나오는 것이다.
- `scene5_tail_cut`은 M7 장면 5(고정점 기록 뒤 꼬리 잘라내기 탐지)를 임시 체인에서 재현한 것이다.
- `real_chain`은 이 시점에 첫 체인이 있었고(`records=2 anchors=1`) 읽기 전용 검증을 통과했다.

## `make audit-anchor LABEL=m4-audit-first` 출력

체인에 `chain.anchor`(seq 1037)를 덧붙이고 대기 파일에 쓴 줄과 같은 한 줄이다. 대기 파일 `data/interim/audit/anchors-pending.jsonl`의 이 줄을 바이트 그대로 [audit-anchors.jsonl](audit-anchors.jsonl)에 옮겼다(대기 파일은 그대로 두며 `audit-verify`가 두 파일을 `(dir, seq, hash)`로 중복 제거해 한 번만 센다).

```
{"dir":"data/interim/audit","hash":"415b0ba535d0700a7d17467679c372bf7462ec611146708e3d41507c03ac532d","kid":"02580ff2e0eb","label":"m4-audit-first","seq":1037,"sig":"3d186a4a4f1304254aee96e0fc4db093d5fc46533939cdbacd276886c38575688ce9845d3f6a6043f3dd78b7a8dc4d291ad9c0a847d7551e7367edaa32e83d0a","ts":"2026-10-08T03:45:19.864934+00:00"}
```

## `make audit-verify` 출력

```
PASS chain dir=data/interim/audit records=2 kid=02580ff2e0eb anchors=1
INFO legacy_unverified dirs=39 files=40 lines=1235 (체인 없음, 미검증)
SUMMARY chains_pass=1 chains_fail=0 legacy_unverified_files=40 anchors_checked=1
```

종료 코드 0. 통과한 체인은 1개(레코드 2건)이고 고정점 1건을 대조했다.

## 체인 없음(미검증) 보고의 구성

`legacy_unverified dirs=39 files=40 lines=1235`는 PASS 개수에 들어가지 않는다. 구성은 아래와 같다.

| 구분 | dirs | files | lines |
|---|---|---|---|
| 커밋된 시도별 감사 디렉터리(`data/results/dev/<run>/attempts/<task>/audit/`) | 38 | 38 | 200 |
| `data/interim/audit` legacy(genesis가 봉인) | 1 | 2 | 1035 |
| 합 | 39 | 40 | 1235 |

커밋된 시도별 디렉터리의 파일은 체인 이전 형식이라 해시·서명이 없어 검증 대상이 아니다. 이후 dev 실행 결과 PR에는 시도별 `chain-*.jsonl`과 그 고정점이 포함된다.

## 한계

- 마지막 고정점 **뒤**만 자른 꼬리는 다음 고정점 전까지 탐지하지 못한다(`smoke-audit`의 `known_limit`).
- 같은 키로 전체를 재계산한 체인은 체인 자체로는 통과하고 고정점으로만 탐지된다.
- B가 탈취되면 서명 키도 노출되어 고정점 이전 구간까지 재서명할 수 있다([THREAT_MODEL](../THREAT_MODEL.md) TH8).
- 모델 호출 실패의 `model.call` 미기록 등 ADR-012 변경 기록의 이월 4건은 [ADR-012](../ADR.md)를 따른다.

## 구현 과정의 검증

- PR #77 CI `verify` 통과(7분 28초). 병합 전 로컬 전체 `make verify`도 통과했다.
- phase 실행 중 `blocked`가 3건 있었고 원인 유형과 반영한 항목은 아래와 같다. 단언은 약화하지 않았다.

| 원인 유형 | 반영 |
|---|---|
| 열람 금지 문장의 주체·범위 미지정(Codex의 직접 열람과 코드의 런타임 접근 혼동) | FP-059 확장 |
| 같은 phase 앞 step이 만든 테스트와 기존 테스트의 정확 일치 단언을 뒤 step 설계에서 점검하지 않음 | FP-060·061 확장 |
| 잠금 추가 뒤 드러난 동시 승인 응답의 순서 단언 | FP-012 사례 추가 |
