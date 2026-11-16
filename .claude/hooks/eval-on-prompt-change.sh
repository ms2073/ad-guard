#!/usr/bin/env bash
# PostToolUse hook: when the agent edits a reviewer prompt or rules.md, run the eval gate automatically.
# Exit 2 sends the failure back to the agent so it has to deal with the regression.
input=$(cat)
file=$(printf '%s' "$input" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))' 2>/dev/null)

case "$file" in
  */adguard/prompts/*|*/rules.md) ;;
  *) exit 0 ;;
esac

cd "$CLAUDE_PROJECT_DIR" || exit 0
[ -f evals/run.py ] || exit 0   # eval harness not built yet

if ! out=$(python3 -m evals.run 2>&1); then
  echo "EVAL GATE FAILED after editing $file:" >&2
  echo "$out" | tail -25 >&2
  exit 2
fi
echo "eval gate passed after editing $file"
exit 0
