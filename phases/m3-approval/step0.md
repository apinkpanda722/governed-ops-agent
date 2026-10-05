# Step 0: claim-gap

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md` (ADR-005·006·008·011·012·017·018), `/docs/ARCHITECTURE.md` ("에이전트 상태 전이", "도구와 부작용·멱등성"), `/docs/THREAT_MODEL.md` (V5·V6·V9·V15)
- `/src/govops/tools/effects.py`, `/src/govops/tools/outbox.py`, `/src/govops/audit/log.py`, `/tests/test_effects_matrix.py`

## 작업

`m3-effects`에서 만든 실행기의 결함 하나를 고친다. 선점에 실패한 호출(`claimed=false`: `already_succeeded`·`already_failed`·`in_progress`)이 남기는 `side_effect.result` 감사 기록이 끝내 실패하면, 지금은 **원래 실행의 outbox 행**에 `audit_gap=True`가 찍힌다. 그 행은 다른 호출의 것이므로 건드리면 안 된다.

- `claimed=false` 경로에서는 outbox 행을 **갱신하지 않는다**. 감사 기록 실패는 반환하는 `EffectResult.audit_gap=True`에만 반영한다.
- 선점에 성공한 호출의 결과 기록 실패는 지금처럼 outbox 행과 `EffectResult.audit_gap` 모두에 반영한다.
- TDD: 테스트를 먼저 쓴다. ① 이미 `SUCCEEDED`인 행에 같은 키로 재실행하고 결과 감사 쓰기를 실패시키면 `EffectResult.audit_gap`은 True이고 원래 행의 `audit_gap`은 False 그대로, `updated` 흔적(상태·시도 횟수)도 그대로 ② 선점 성공 호출의 결과 감사 실패는 둘 다 True.

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
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- 선점 실패 호출이 outbox 행을 갱신하게 두지 마라. 이유: 그 행은 다른 실행의 기록이다.
- 이 step에서 실행기의 다른 동작(재시도·조회 규칙)을 바꾸지 마라.
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라.
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse에 접속하는 테스트를 만들지 마라. 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- 기존 테스트를 깨뜨리지 마라. 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.
