.PHONY: verify

# 하네스의 단일 검증 진입점. Stop hook, scripts/execute.py, step AC가 모두 이 타깃을 호출한다.
# Python lint·포맷·pytest 이후 web tsc·eslint·vitest를 순서대로 실행한다.
# API 키·네트워크 없이 설치된 의존성만 사용하며, 실패한 단계에서 즉시 중단한다.
verify:
	@echo "==> ruff check"
	uv run ruff check .
	@echo "==> ruff format --check"
	uv run ruff format --check .
	@echo "==> pytest"
	uv run pytest -q
	@echo "==> web typecheck (tsc)"
	@command -v pnpm >/dev/null || { echo "pnpm이 필요합니다 (사용자가 설치)"; exit 1; }
	pnpm -C web typecheck
	@echo "==> web lint (eslint)"
	pnpm -C web lint
	@echo "==> web test (vitest)"
	pnpm -C web test

A_ROOT ?= $(HOME)/PycharmProjects/ko-docs-agentic-rag
SMOKE_USER ?= demo-platform-g3

.PHONY: mock-tokens seed-extract record-a smoke-contract smoke-identity smoke-visibility smoke-mock smoke-host-header

# users 시드 필요; mock 인증 토큰·해시 파일 생성.
mock-tokens:
	uv run python -m govops.mocks.tokens

# 읽기 전용 A 시드 필요; 신원·권한 검증 후 결정론 추출.
seed-extract:
	uv run python -m govops.mocks.seed_extract --a-root $(A_ROOT)

# live A·사용자 토큰 필요; 계약 일치와 합성 응답만 기록.
record-a:
	uv run --env-file .env python -m govops.tools.record_a --requests data/replay/requests.json --out data/replay/a

# live A·사용자 토큰 필요; 고정 계약 일치.
smoke-contract:
	uv run --env-file .env python -m govops.tools.smoke contract

# live A·mock-jira·시드·토큰 필요; platform 가시·finance 비가시.
smoke-identity:
	uv run --env-file .env python -m govops.tools.smoke identity

# live A·mock-jira·시드·토큰 필요; 38명 × 40건 불일치 0건.
smoke-visibility:
	uv run --env-file .env python -m govops.tools.smoke visibility

# compose mock·시드·토큰 필요; 채널 9개·타 팀 403·잘못된 토큰 401.
smoke-mock:
	uv run --env-file .env python -m govops.tools.smoke mock

# live A·사용자 토큰 필요; 인증된 위조 Host 요청 421 또는 403 거절.
smoke-host-header:
	uv run --env-file .env python -m govops.tools.smoke host-header --user $(SMOKE_USER)

.PHONY: costs-init
costs-init:
	uv run python -m govops.costs init

.PHONY: agent-key
# compose 기동 후 실행. 키는 출력되지 않고 .env에 기록된다
agent-key:
	uv run --env-file .env python -m govops.gateway.agent_key

.PHONY: db-init
# compose 기동 후 실행, 이미 있으면 변경 없음
db-init:
	docker compose --env-file .env -f infra/docker-compose.yml exec -T postgres sh -c 'printf "%s\n" "SELECT '\''CREATE DATABASE govops'\'' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '\''govops'\'');" "\gexec" | psql --username "$$POSTGRES_USER" --dbname postgres --set ON_ERROR_STOP=1'

.PHONY: serve ask
# compose·live A 기동 후, DEMO_LOGIN=true 필요
serve:
	uv run --env-file .env python -m govops.api

# 예시: make ask AS=demo-platform-g3 Q="티켓을 요약해 주세요"
ask:
	uv run --env-file .env python -m govops.api.cli ask --as $(AS) "$(Q)"

.PHONY: smoke-checkpointer smoke-agent
# compose·make db-init 필요, 쓰기·읽기 왕복
smoke-checkpointer:
	uv run --env-file .env python -m govops.api.smoke checkpointer

# live A·LiteLLM·make agent-key 필요, 실제 호출 수 센트, 기대값: 두 사용자 completed·platform만 syn-incident-022 관찰·인용·원장 증가
smoke-agent:
	uv run --env-file .env python -m govops.api.smoke agent
