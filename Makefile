.PHONY: verify

# 하네스의 단일 검증 진입점. Stop hook, scripts/execute.py, step AC가 모두 이 타깃을 호출한다.
# Python lint·포맷 검사와 프로젝트·하네스 테스트를 실행한다.
# web 검사(tsc·eslint·vitest)는 step 7에서 추가한다.
verify:
	uv run ruff check . && uv run ruff format --check . && uv run pytest -q
