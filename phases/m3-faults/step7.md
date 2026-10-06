# Step 7: faults-docs

## 읽어야 할 파일

- `/AGENTS.md`, `/docs/ADR.md`(ADR-018·019·020·021), `/docs/EVAL.md`, `/docs/THREAT_MODEL.md`("F1~F11" 표), `/docs/results/hypotheses.md`, `/docs/results/hypotheses-m3.md`, `/docs/results/2026-10-06-m3-dev2-live.md`, `/.claude/skills/failure-patterns/SKILL.md`
- `/docs/ARCHITECTURE.md`, `/docs/PRD.md`, `/docs/ADR.md`(마지막 ADR 번호 확인: 현재 ADR-021), `/docs/THREAT_MODEL.md`(F4·F6·F10·F11 행), `/docs/EVAL.md`("장애 주입" 표본 수 "미정" 문구), `/AGENTS.md`(명령어 블록), `/tests/test_docs_consistency.py`, `/docs/results/hypotheses-m3-faults.md`, `/src/govops/evalrun/faults.py`·`fault_scenarios.py`·`faults_smoke.py`, `/src/govops/replay/chaos_a.py`, `/src/govops/tools/a_mcp.py`·`fault_transport.py`, `/src/govops/golden.py`·`/src/govops/agent/graph.py`(프로필 정리 결과)

## 작업

**배경**: `m3-faults`가 만든 것을 문서에 반영한다. 문서는 **결정 내용만** 쓴다(누가 결정했는지·경위를 쓰지 않는다. 검토 이력이 필요하면 "설계 검토"라고만 쓴다). 수치는 코드 상수·등록 파일에서 가져온다. TDD: `test_docs_consistency`가 확인하는 항목을 먼저 확장한다.

1. **`docs/ADR.md`에 ADR-022를 추가**한다: M3 결정 — 장애 주입 하네스. 포함 내용: 골든·기본 프로필을 `writes_org` 하나로 정리하고 M2 러너의 실행 경로를 삭제(공용 헬퍼 유지, 결과 문서 보존), `AMcpClient`가 A의 5xx·429를 재시도·서킷 대상으로 본다(401 경로 불변), F11은 chaos A 서버(원시 서버, 가짜 A 미사용, 합성 토큰만, 요청 헤더 미기록), F4 클라이언트 쪽 주입, F6 `down_alias`, F10은 항상 F5와 결합(1.0초 짧음/6.0초 긺), 154회·분모 132, 중단·재개 규칙(같은 시드·순서로 남은 계획만, 결과에 따른 재실행 금지), 서킷 실행마다 초기화, 이월 범위(F7·F6 중 Guardrail 통과는 M4). 기존 ADR의 형식(상태·맥락·결정·결과)을 따른다.
2. **`docs/ARCHITECTURE.md`**: `evalrun/faults.py`·`fault_scenarios.py`·`faults_smoke.py`, `replay/chaos_a.py`, `tools/fault_transport.py`의 위치와 계층 경계, `make eval-faults`·`make smoke-faults` 흐름, 프로필 정리 결과(`PROFILES = {writes_org}`).
3. **`docs/THREAT_MODEL.md`**: F4(클라이언트 쪽 주입 명시)·F6(`down_alias`, 이월 범위)·F10(F5 결합 1.0/6.0초)·F11(chaos A, 판정 ①②, 한계) 행과 V14·V8 검증 행을 새 구현에 맞게 고친다. 5xx·429 분류 복원을 V14 근거에 반영한다.
4. **`docs/EVAL.md`와 `docs/PRD.md`**: 장애 주입 표본 수 "미정" 문구를 **`docs/results/hypotheses-m3-faults.md` 참조**로 바꾼다(154·분모 132는 등록 파일이 원본이고 문서는 가리킨다). 골든 14개가 `writes_org` 단일 프로필 기록임을 반영한다.
5. **`AGENTS.md`**: 명령어 블록에 `make eval-faults`(`caffeinate -i make eval-faults` 안내, 약 75~80분, 사용자 실행)와 `make smoke-faults`(사용자 실행)를 추가한다. 다른 항목은 건드리지 않는다. 기술 스택의 프로필 설명이 있으면 새 상태에 맞게만 고친다.
6. **`tests/test_docs_consistency.py`**: 새 문서 항목(ADR-022 존재, AGENTS 명령어 두 개, EVAL·PRD의 등록 파일 참조, 프로필 문구)을 검사하도록 확장한다. 기존 검사를 약화하지 않는다.

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

## 금지사항

- 문서에 "누가 결정·조언했는지"를 쓰지 마라("조언 세션" 표현 금지, 필요하면 "설계 검토"). 이유: 문서는 결정 내용만 쓴다.
- 문서에 실행 결과(성능·TCR·중복 건수) 수치를 쓰지 마라. 이유: live 실행 전이고 결과는 결과 문서가 쓴다. 계획 수치(154·132·시드)는 등록 파일을 가리키며 쓴다(FP-043).
- `docs/results/`를 수정하지 마라. 이유: 사전 등록 파일과 결과 문서 영역이다.
- `docs/PRD.md`의 범위·목표를 바꾸지 마라("미정" 문구를 등록 파일 참조로 바꾸는 것만 허용).
- 기대값·목표를 실행 결과를 본 뒤 정하지 마라(FP-043). 이유: 사전 등록 전에 결과가 기대값을 결정하면 평가가 아니다. 시나리오별 기대 결과는 코드 상수와 등록 파일에 실행 전 확정한다.
- 이 step이 의존하는 외부 지점을 실측 없이 가정하지 마라(FP-050). 이유: 승인 뒤 live에서 결함이 드러나 수정이 늘었다. 읽기 목록의 실측 근거를 따르고 다른 동작을 관측하면 가정하지 말고 보고한다.
- 이 파일의 지시와 AGENTS.md 계층 경계가 충돌하거나 이 step의 작업·금지사항이 서로 모순되면 임의로 판단하지 말고 `blocked`로 보고하라(FP-054). 이유: 모순을 임의로 해소하면 계층 경계가 깨진다.
- 이 step이 필요로 해서 테스트를 바꿀 때는 변경 목록(파일·테스트 이름·이유)을 최종 보고에 적어라(FP-056). 이유: 완화된 단언이 다음 step에서 방치된다. 삭제는 그 테스트가 검증하던 대상 자체가 이 step에서 제거된 경우만 허용한다.
- `.claude/`(skills·commands)와 `docs/results/`의 사전 등록 파일(`hypotheses.md`·`hypotheses-m3.md`·`hypotheses-m3-grading.md`·`hypotheses-m3-prompt.md`·`n-registration-m3.md`·`m3-dev-supersedes.json`·`m3-dev2-supersedes.json`·`2026-10-06-m3-dev-live.md`·`2026-10-06-m3-dev2-live.md` 등)은 읽기만 하고 수정하지 않는다(이 step이 명시적으로 허용한 경우 제외). 이유: 읽기 목록의 파일을 지시 없이 고치지 않게 한다(FP-055).
- `scripts/`, `phases/`, `web/`, `data/contract/`, `data/eval/`을 수정하지 마라(이 step이 명시적으로 허용한 경우 제외).
- `.env`와 A의 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라. 토큰·키 값을 코드·테스트·문서·로그에 남기지 마라.
- A 저장소(`~/PycharmProjects/ko-docs-agentic-rag`)를 수정하지 마라(읽기 전용으로 참고하는 것만 허용).
- 실제 LiteLLM·OpenAI·Anthropic·Gemini·A MCP·Langfuse·Docker에 접속하는 테스트나 코드 실행을 하지 마라(로컬 루프백 서버와 합성 토큰은 허용). 이유: `make verify`는 키·네트워크 없이 통과해야 한다.
- `data/results/`의 실행 결과 디렉토리와 `data/replay/llm/`·`data/replay/a_run/`·`data/replay/golden/`의 기록 파일을 수정하지 마라(읽기만). 이유: 실행 기록과 골든 기록은 증거이며 live 러너가 만든다.
- 기존 테스트를 약화하지 마라(단언 삭제·`require_all=False`·슬라이스 비교·허용 오차 확대). 이름·시그니처 변경 때문에 필요한 호출·기대값 수정만 허용한다(FP-056). 계층 가드(`tests/test_architecture.py`)를 약화하지 마라.
