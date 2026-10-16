#!/bin/bash
set -euo pipefail

# 只在 Claude Code 雲端工作階段執行
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# 全域安裝 opencode CLI（已安裝則略過）
if ! command -v opencode >/dev/null 2>&1; then
  npm install -g @opencode/cli
fi
