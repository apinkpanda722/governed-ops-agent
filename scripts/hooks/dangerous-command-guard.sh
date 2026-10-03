#!/usr/bin/env bash
# PreToolUse guard (Bash): 되돌리기 어려운 명령을 차단한다.
#
# Claude Code와 Codex 모두 hook 입력을 stdin JSON으로 넘기고(tool_input.command),
# exit 2 + stderr 일 때만 도구 실행을 막는다. (환경변수 $CLAUDE_TOOL_INPUT 은 존재하지 않고,
# exit 1 은 경고만 남기고 실행을 계속한다.)
# 정규식은 마지막 방어선일 뿐이다. 실제 격리는 execute.py 의 Codex 샌드박스(workspace-write)가 맡는다.
set -euo pipefail

input=$(cat)
command_str=$(jq -r '.tool_input.command // empty' <<< "$input")

[[ -z "$command_str" ]] && exit 0

patterns=(
  'rm[[:space:]]+-[a-zA-Z]*(r[a-zA-Z]*f|f[a-zA-Z]*r)'   # rm -rf, rm -fr, rm -Rf ...
  'rm[[:space:]]+(-[a-zA-Z]+[[:space:]]+)*--recursive'
  'rm[[:space:]]+-r[[:space:]]+-f|rm[[:space:]]+-f[[:space:]]+-r'
  'git[[:space:]]+push[[:space:]].*(--force|-f([[:space:]]|$))'
  'git[[:space:]]+reset[[:space:]]+--hard'
  'git[[:space:]]+clean[[:space:]]+-[a-zA-Z]*f'
  'DROP[[:space:]]+(TABLE|DATABASE)'
)

for pattern in "${patterns[@]}"; do
  if grep -qiE "$pattern" <<< "$command_str"; then
    echo "BLOCKED: 위험한 명령어가 감지되었습니다: $command_str" >&2
    exit 2
  fi
done

exit 0
