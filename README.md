# governed-ops-agent

A의 MCP 도구를 쓰는 사내 운영 에이전트(티켓 조회·요약 → 메신저 발송)를 LLM 게이트웨이 뒤에서 안전하게 운영하는 프로덕션급 Tool-use Agent.

- 설계 문서: `docs/` (PRD·ARCHITECTURE·ADR·EVAL·THREAT_MODEL)
- 하네스 사용법: `scripts/README.md`

## 개발 환경

Python 3.12와 uv를 사용한다(`pyproject.toml`, `.python-version`). 에이전트 서비스는 호스트에서 `uv run`으로 실행한다(서비스 진입점은 M1에서 구현). web은 호스트에서 `cd web && pnpm dev`로 실행하며 compose에 넣지 않는다. Node 26.4.0과 pnpm 12.8.1을 사용한다(`web/.nvmrc`, `web/package.json`).

`make verify`는 ruff check → ruff format --check → pytest → web typecheck(tsc) → eslint → vitest를 실행한다. 설치된 의존성으로 API 키·네트워크 없이 검증한다.

핵심 스택은 `docker compose -f infra/docker-compose.yml up`, Langfuse v3 관측 스택을 포함하려면 `docker compose -f infra/docker-compose.yml --profile obs up`으로 실행한다(ADR-016).

사용자가 직접 할 일:

- B 전용 API 키 3개를 발급하고 `.env.example`의 변수 이름에 맞춰 키·설정 값을 `.env`에 입력한다. `.env.example`에는 값을 넣지 않는다.
- Python·uv·고정 버전 Node·pnpm과 의존성을 설치한다.
- 첫 기동 전에 compose와 mock Dockerfile 이미지 태그의 공식 릴리스 노트·호환성을 확인한다. 이미지 태그는 아직 확인 필요 상태다.
- compose를 처음 기동하고 `docker stats`로 메모리를 실측한다. 부족하면 Docker 메모리를 늘린다.
