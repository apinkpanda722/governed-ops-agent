# Harness Framework (Codex executor)

계획은 Claude Code에서 세우고, 코드 수정은 Codex가 step 단위로 실행하는 하네스다. 스택과 무관하게 에이전트 프로젝트와 애플리케이션 프로젝트 모두에 쓴다.

> 원본: [jha0313/harness_framework](https://github.com/jha0313/harness_framework)의 포크. 워크플로우(`/harness` → `phases/` → `execute.py`)와 step 설계 원칙은 원본을 따르고, 아래 "원본 대비 변경점"만 이 저장소에서 수정했다.

## 흐름

1. Claude Code에서 `/harness`를 실행한다. 0단계 "프로젝트 셋업"에서 스택 골격, `make verify`, `AGENTS.md`, `docs/`, 프로젝트 전용 hook을 채운다.
2. 이어서 step을 설계하고 `phases/{task}/`를 생성한다.
3. 변경사항을 커밋한 뒤 `python3 scripts/execute.py {task}`로 실행한다.
4. `/review`로 결과를 검토한다.

## 구성

| 경로 | 역할 |
|------|------|
| `AGENTS.md` | 에이전트 규칙 단일 원본. `CLAUDE.md`는 `@AGENTS.md` import만 한다 |
| `Makefile` | `make verify` — Stop hook, execute.py, step AC가 공통으로 부르는 검증 진입점 |
| `scripts/execute.py` | step 순차 실행기 (Codex 호출, 검증, 재시도, 커밋) |
| `scripts/step-result.schema.json` | Codex 최종 응답 스키마 (`status`, `summary`, `reason`) |
| `scripts/hooks/` | 위험 명령 차단, `make verify` Stop hook (스택 중립) |
| `.codex/config.toml`, `.claude/settings.json` | 두 도구가 같은 hook 스크립트를 가리킨다 |

## 하네스 소유 vs 프로젝트 소유

하네스는 "어떻게 일하는가"만 소유한다. 스택·도메인에 따라 달라지는 것은 프로젝트가 `/harness` 0단계에서 만든다.

| 하네스가 제공 | 프로젝트가 채우거나 추가 |
|------|------|
| `scripts/execute.py`, 결과 스키마, 하네스 테스트 | 스택 골격 (`uv init`, `create-next-app` 등 공식 도구) |
| 위험 명령 차단 hook, `make verify` Stop hook | `Makefile`의 `verify` 내용 (lint / typecheck / test) |
| `/harness`, `/review` | 언어별 hook (예: TDD guard) |
| 빈 골격: `AGENTS.md`, PRD, ARCHITECTURE, ADR | 기준 문서 (예: UI 가이드, 평가 문서) |

지금은 저장소를 복사해서 쓴다. 여러 프로젝트에서 쓰게 되면 복사본끼리 어긋나지 않도록 플러그인/CLI 배포로 전환을 검토한다.

## 원본 대비 변경점

**안전**
- PreToolUse 가드가 동작하지 않던 문제 수정. 원본은 존재하지 않는 `$CLAUDE_TOOL_INPUT` 환경변수를 읽고 `exit 1`(차단 안 됨)로 끝났다. stdin JSON을 `jq`로 읽고 `exit 2`로 차단하도록 바꾸고, `rm -fr`·`git push -f`·`git clean -f` 같은 변형도 잡는다.
- 권한 우회(`--dangerously-skip-permissions`) 대신 `codex exec --sandbox workspace-write`로 실행해 워크스페이스 밖 쓰기를 OS 샌드박스로 막는다.
- Stop hook에 `stop_hook_active` 가드와 `exit 2`를 적용해 실패 시 실제로 수정을 이어가게 하고 무한 루프를 막는다.

**실행기 신뢰성**
- 에이전트의 완료 자기보고를 그대로 믿지 않고 실행기가 `make verify`를 다시 돌려 통과해야 완료로 기록한다.
- 에이전트는 index.json을 직접 고치지 않고 스키마가 고정된 최종 응답만 돌려준다. 상태 기록과 커밋은 실행기만 한다.
- 실패/차단된 step의 코드는 커밋하지 않는다(원본은 실패 코드도 `feat` 커밋).
- 타임아웃을 크래시가 아닌 시도 실패로 처리한다.
- 실행 전 작업 트리 청결 검사로 무관한 변경이 step 커밋에 섞이지 않게 한다.
- `--steps N`으로 일부 step만 실행할 수 있다.
- step별 토큰 사용량과 시도 횟수를 index.json `usage`에 기록한다.
- 매 step마다 CLAUDE.md와 docs 전체를 주입하던 것을 없앴다. 규칙은 Codex가 AGENTS.md로 읽고, 문서는 step 파일이 필요한 것만 지정한다.

**템플릿**
- Next.js 전용 템플릿(UI_GUIDE 등)을 제거하고 스택 중립 골격으로 바꿨다. 스택·도메인별 문서와 hook은 `/harness` 0단계에서 프로젝트가 추가한다.
- 브랜치를 `feature/{task}`로 바꿨다.
- `.gitignore`에 Python 산출물, `.env`, `logs/`를 추가했다.

## 요구 사항

- Codex CLI (`codex login` 완료), Claude Code, `jq`, Python 3.10+
- execute.py는 `--dangerously-bypass-hook-trust`로 저장소에 커밋된 `.codex/config.toml` hook을 사전 신뢰 등록 없이 실행한다. hook 스크립트를 바꿀 때는 직접 검토한다.

## 테스트

```bash
make verify
```
