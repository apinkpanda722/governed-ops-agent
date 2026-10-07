#!/usr/bin/env bash
# Stop hook: `make verify` 가 실패하면 에이전트가 멈추지 않고 계속 수정하도록 되돌린다.
#
# Claude Code와 Codex 모두 Stop hook 이 exit 2 + stderr(계속할 이유)일 때 세션을 이어간다.
set -uo pipefail

input=$(cat 2>/dev/null || true)
stop_hook_active=$(jq -r '.stop_hook_active // false' <<< "$input" 2>/dev/null)

# 이미 Stop hook 때문에 이어진 세션이면 다시 검증하지 않는다 (무한 루프 방지).
[[ "$stop_hook_active" == "true" ]] && exit 0

cwd=$(jq -r '.cwd // empty' <<< "$input" 2>/dev/null)
[[ -z "$cwd" ]] && cwd="$(pwd)"

# phase 실행 중(execute.py가 .harness/executing에 PID를 적어 둔다)이면 중간 작업 트리를
# 검증하지 않는다. 그 PID가 죽어 있으면(비정상 종료) 잠금은 무효다. 실행기의 자손
# 프로세스(step을 실행하는 Codex 세션)는 execute.py가 환경 변수 HARNESS_EXECUTOR_PID로
# 자신의 PID를 물려 주므로 그 값이 잠금의 PID와 같으면 계속 검증해 스스로 고치게 한다.
# (ps에 의존하지 않는다: Codex 샌드박스는 ps를 막을 수 있다.)
lock="$cwd/.harness/executing"
if [[ -f "$lock" ]]; then
  lock_pid=$(tr -dc '0-9' < "$lock" 2>/dev/null | head -c 10)
  if [[ -n "$lock_pid" ]] && kill -0 "$lock_pid" 2>/dev/null; then
    [[ "${HARNESS_EXECUTOR_PID:-}" == "$lock_pid" ]] || exit 0
  fi
fi

# 검증 진입점이 없거나, 이번 세션에서 바뀐 파일이 없으면(읽기 전용 리뷰 등) 통과시킨다.
[[ -f "$cwd/Makefile" ]] || exit 0
porcelain=$(git -C "$cwd" status --porcelain 2>/dev/null)
[[ -z "$porcelain" ]] && exit 0

# Only exact configured ledger paths may skip verification. Missing jq or an
# invalid contract falls back to the existing verify behavior.
if jq -e --arg status "$porcelain" '
  .ledger_paths as $paths |
  if type != "object" or keys != ["ledger_paths"] or
     ($paths | type) != "array" or ($paths | length) != 1 then false
  elif ($paths | all(.[]; type == "string" and length > 0 and
       (startswith("/") | not) and
       (split("/") | all(.[]; . != "" and . != "." and . != "..")))) | not then false
  else $status | split("\n") | map(select(length > 0) |
    .[3:] | split(" -> ") | last |
    if startswith("\"") then fromjson else . end) |
    all(.[]; . as $path | $paths | index($path) != null)
  end
' "$cwd/scripts/exempt_paths.json" >/dev/null 2>&1; then
  exit 0
fi

output=$(cd "$cwd" && make verify 2>&1)
status=$?

if [[ $status -ne 0 ]]; then
  echo "make verify 가 실패했습니다. 아래 출력을 확인하고 수정을 계속하세요:" >&2
  echo "$output" | tail -n 80 >&2
  exit 2
fi

exit 0
