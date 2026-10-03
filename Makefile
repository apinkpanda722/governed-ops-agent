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
