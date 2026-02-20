#!/bin/bash
# Ralph Loop for claude-tap diff matching improvement
set -uo pipefail
cd "$(dirname "$0")"

MAX_ITER="${1:-8}"
ITER=0
LOG_DIR="/tmp/ralph-logs-$(date +%s)"
mkdir -p "$LOG_DIR"

echo "=== Ralph Loop starting (max $MAX_ITER iterations) ==="
echo "=== Logs: $LOG_DIR ==="

while [ "$ITER" -lt "$MAX_ITER" ]; do
    ITER=$((ITER + 1))
    echo ""
    echo "============================================"
    echo "=== Iteration $ITER / $MAX_ITER ==="
    echo "============================================"

    # Core Ralph: pipe prompt into Claude Code
    claude --dangerously-skip-permissions --model claude-sonnet-4-6 -p "$(cat PROMPT_build.md)" 2>&1 | tee "$LOG_DIR/iter-${ITER}.log"

    echo ""
    echo "=== Backpressure: running tests ==="
    if python3 -m pytest tests/ -v 2>&1 | tee "$LOG_DIR/test-${ITER}.log"; then
        echo "=== ✅ Tests PASSED ==="
    else
        echo "=== ❌ Tests FAILED ==="
    fi

    # Check completion marker
    if [ -f IMPLEMENTATION_PLAN.md ] && grep -q "ALL TASKS COMPLETE" IMPLEMENTATION_PLAN.md; then
        echo ""
        echo "=== 🎉 ALL TASKS COMPLETE ==="
        break
    fi

    sleep 2
done

echo ""
echo "=== Ralph Loop ended after $ITER iterations ==="
echo "=== Logs: $LOG_DIR ==="
