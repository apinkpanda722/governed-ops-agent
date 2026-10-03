# Step 7: verify-web

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/AGENTS.md` ("개발 프로세스": `make verify`가 유일한 검증 진입점, "명령어")
- `/docs/PRD.md` (M0 행과 표 아래 `make verify` 문단)
- `/Makefile` (step 0에서 python 검사로 교체된 상태)
- `/web/package.json` (step 6에서 추가된 `typecheck`·`lint`·`test` scripts)
- `/tests/test_repo_rules.py` (테스트 작성 스타일 참고)
- `/scripts/hooks/verify-before-stop.sh` (Stop hook이 `make verify`를 호출한다)

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

`make verify`에 web 검사를 추가한다. PRD가 정한 순서는 "ruff 추가 후 web 검사 추가"이며 ruff는 step 0에서 끝났다. TDD로 테스트를 먼저 쓴다.

1. **테스트 먼저**: `tests/test_makefile.py`. `Makefile`을 텍스트로 읽어 `verify` 타깃의 recipe에 아래 명령이 **이 순서대로** 모두 들어 있는지 검사한다: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest -q`, `pnpm -C web typecheck`, `pnpm -C web lint`, `pnpm -C web test`. 먼저 실행해 실패하는 것을 확인한다.
2. **`Makefile`의 `verify` 타깃 확장**: 위 6개를 순서대로 실행한다. 하나라도 실패하면 즉시 멈추고 non-zero로 끝난다(make 기본 동작). 각 단계 앞에 `@echo "==> <단계>"`를 두어 실패한 단계가 출력에 보이게 한다. `pnpm`이 PATH에 없으면 `command -v pnpm >/dev/null || { echo "pnpm이 필요합니다 (사용자가 설치)"; exit 1; }`로 분명한 메시지를 내고 실패한다. 상단 주석을 현재 상태에 맞게 갱신한다. `verify` 외에 다른 공개 타깃(서브 타깃)을 만들지 마라.

핵심 규칙:
- `make verify`는 API 키·네트워크 없이 통과해야 한다(AGENTS.md CRITICAL). pnpm 명령은 이미 설치된 `node_modules`만 쓴다.
- 이 파일이 Stop hook, `execute.py`, CI의 공통 진입점이다. 실행 순서를 바꾸거나 단계를 빼면 하네스가 무검증으로 통과시키게 된다.

## Acceptance Criteria

```bash
make verify   # ruff check + ruff format --check + pytest + web typecheck + eslint + vitest
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - 단계 순서가 ruff → pytest → tsc → eslint → vitest인가?
   - 실패 시 어느 단계에서 멈췄는지 출력에 보이는가?
   - AGENTS.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과를 최종 응답(JSON)으로 보고한다. `phases/` 아래 파일은 수정하지 않고 git commit도 하지 않는다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 해결할 수 없는 실패 → `"status": "error"`, `"reason": "구체적 에러 내용"`
   - 사용자 개입 필요 → `"status": "blocked"`, `"reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- `pnpm install`·`pnpm add` 등 설치 명령을 Makefile에 넣거나 실행하지 마라. 이유: `verify`는 네트워크 없이 통과해야 하고 설치는 사용자가 한다.
- 네트워크·API 키가 필요한 명령(docker, 실제 모델 호출)을 `verify`에 넣지 마라. 이유: AGENTS.md CRITICAL.
- Playwright e2e를 추가하지 마라. 이유: 승인 화면이 생기는 M5 이후 범위다.
- `.env`와 `data/interim/mcp/tokens.json`을 열거나 출력하지 마라.
- `scripts/`, `phases/`, `web/`, `src/`, `infra/`를 수정하지 마라.
- 기존 테스트를 깨뜨리지 마라.
