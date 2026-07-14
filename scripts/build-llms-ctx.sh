#!/usr/bin/env bash
# Concatenate the MkDocs navigation pages into one context file for AI agents.
set -euo pipefail

DOCS_DIR="$(cd "$(dirname "$0")/../docs" && pwd)"
OUT="$DOCS_DIR/llms-ctx.txt"

echo "# DiffGuard — Published Guide Context" > "$OUT"
echo "" >> "$OUT"

for f in \
  index.md \
  quickstart.md \
  real-world-catches.md \
  how-it-works.md \
  agent-integration.md \
  agents-md-snippet.md \
  claude-md-snippet.md \
  github-copilot-instructions.md \
  cursor-rule-snippet.md \
  schema.md \
  architecture.md \
  validation.md \
  roadmap.md \
  adoption-kit.md; do
  if [ -f "$DOCS_DIR/$f" ]; then
    echo "---" >> "$OUT"
    echo "# Source: $f" >> "$OUT"
    echo "" >> "$OUT"
    cat "$DOCS_DIR/$f" >> "$OUT"
    echo "" >> "$OUT"
  fi
done

echo "Built $OUT"
