#!/usr/bin/env bash
# Concatenate all docs into a single context file for AI agents
set -euo pipefail

DOCS_DIR="$(cd "$(dirname "$0")/../docs" && pwd)"
OUT="$DOCS_DIR/llms-ctx.txt"

echo "# DiffGuard — Full Documentation Context" > "$OUT"
echo "" >> "$OUT"

for f in index.md quickstart.md schema.md agent-integration.md architecture.md validation.md; do
  if [ -f "$DOCS_DIR/$f" ]; then
    echo "---" >> "$OUT"
    echo "# Source: $f" >> "$OUT"
    echo "" >> "$OUT"
    cat "$DOCS_DIR/$f" >> "$OUT"
    echo "" >> "$OUT"
  fi
done

echo "Built $OUT"
