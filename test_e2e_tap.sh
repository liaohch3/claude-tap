#!/bin/bash
#
# claude-tap E2E test suite
#
# Uses tmux to drive real Claude Code sessions through claude-tap,
# verifying JSONL/LOG real-time writing, HTML generation, and graceful exit.
#
# Usage:
#   ./test_e2e_tap.sh              # Run all tests
#   ./test_e2e_tap.sh --test normal   # Run single test
#   ./test_e2e_tap.sh --test live
#   ./test_e2e_tap.sh --test ctrlc
#
# Dependencies: tmux (brew install tmux), claude-tap (pip install -e .)

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEST_OUTPUT_DIR="/tmp/claude-tap-e2e-$(date +%s)"
SESSION_PREFIX="claude-tap-e2e"
PASS_COUNT=0
FAIL_COUNT=0
TOTAL_COUNT=0
SELECTED_TEST=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --test)
            SELECTED_TEST="$2"
            shift 2
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Usage: $0 [--test normal|live|ctrlc]"
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

report() {
    local test_name="$1"
    local check="$2"
    local result="$3"  # PASS or FAIL
    TOTAL_COUNT=$((TOTAL_COUNT + 1))
    if [ "$result" = "PASS" ]; then
        PASS_COUNT=$((PASS_COUNT + 1))
        echo "  [$test_name]  PASS: $check"
    else
        FAIL_COUNT=$((FAIL_COUNT + 1))
        echo "  [$test_name]  FAIL: $check"
    fi
}

wait_for_idle() {
    # Wait until tmux pane content stops changing for $required_idle seconds.
    local session="$1"
    local required_idle="${2:-5}"
    local max_wait="${3:-120}"
    local idle_seconds=0
    local elapsed=0
    local prev_content=""

    while [ "$elapsed" -lt "$max_wait" ]; do
        local current_content
        current_content=$(tmux capture-pane -t "$session" -p 2>/dev/null || true)

        if [ "$current_content" = "$prev_content" ]; then
            idle_seconds=$((idle_seconds + 1))
            if [ "$idle_seconds" -ge "$required_idle" ]; then
                return 0
            fi
        else
            idle_seconds=0
            prev_content="$current_content"
        fi
        sleep 1
        elapsed=$((elapsed + 1))
    done
    echo "    WARNING: wait_for_idle timed out after ${max_wait}s"
    return 1
}

send_and_wait() {
    # Send a message to Claude via tmux and wait for reply to finish.
    local session="$1"
    local message="$2"
    local idle_time="${3:-8}"

    echo "    >>> Sending: $message"
    tmux send-keys -t "$session" -l "$message"
    sleep 0.3
    tmux send-keys -t "$session" Enter
    echo "    Waiting for reply..."
    wait_for_idle "$session" "$idle_time" 180
    echo "    Reply complete."
}

assert_file_exists() {
    local test_name="$1"
    local pattern="$2"
    local description="$3"
    local found
    found=$(find "$4" -name "$pattern" 2>/dev/null | head -1)
    if [ -n "$found" ] && [ -s "$found" ]; then
        report "$test_name" "$description" "PASS"
        echo "$found"  # Return the path for later use
    else
        report "$test_name" "$description" "FAIL"
        echo ""
    fi
}

assert_file_grows() {
    # Assert that a file's size increased after an interaction.
    local test_name="$1"
    local file_path="$2"
    local size_before="$3"
    local description="$4"

    if [ ! -f "$file_path" ]; then
        report "$test_name" "$description" "FAIL"
        return
    fi

    local size_after
    size_after=$(wc -c < "$file_path" | tr -d ' ')

    if [ "$size_after" -gt "$size_before" ]; then
        report "$test_name" "$description" "PASS"
    else
        report "$test_name" "$description (before=$size_before, after=$size_after)" "FAIL"
    fi
}

get_file_size() {
    if [ -f "$1" ]; then
        wc -c < "$1" | tr -d ' '
    else
        echo "0"
    fi
}

cleanup_session() {
    local session="$1"
    tmux kill-session -t "$session" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# Core test runner
# ---------------------------------------------------------------------------

run_test() {
    local test_name="$1"
    local use_live="$2"       # "yes" or "no"
    local exit_method="$3"    # "exit" or "ctrlc"

    echo ""
    echo "=== Running: $test_name ==="

    local session="${SESSION_PREFIX}-${test_name}"
    local trace_dir="${TEST_OUTPUT_DIR}/${test_name}"
    mkdir -p "$trace_dir"

    # Clean up any leftover session
    cleanup_session "$session"

    # Build claude-tap command
    local tap_cmd="claude-tap --tap-output-dir '$trace_dir'"
    if [ "$use_live" = "yes" ]; then
        tap_cmd="$tap_cmd --tap-live"
    fi
    # Pass a simple prompt to make the interaction short
    tap_cmd="$tap_cmd -p 'say exactly: hello world. nothing else.'"

    # Launch in tmux
    echo "    Starting: $tap_cmd"
    tmux new-session -d -s "$session" -x 200 -y 50 "cd '$SCRIPT_DIR' && unset CLAUDECODE && unset CLAUDE_CODE_SSE_PORT && $tap_cmd; echo '=== CLAUDE-TAP EXITED ==='; sleep 30"

    # Wait for startup (claude-tap proxy + claude boot)
    echo "    Waiting for startup..."
    sleep 5

    # Wait for Claude to fully start (look for output stabilization)
    wait_for_idle "$session" 5 90

    # Step 1: Check .jsonl and .log files created on startup
    local jsonl_file
    jsonl_file=$(find "$trace_dir" -name "*.jsonl" 2>/dev/null | head -1)
    local log_file
    log_file=$(find "$trace_dir" -name "*.log" 2>/dev/null | head -1)

    if [ -n "$jsonl_file" ]; then
        report "$test_name" ".jsonl created on startup" "PASS"
    else
        report "$test_name" ".jsonl created on startup" "FAIL"
    fi

    if [ -n "$log_file" ]; then
        report "$test_name" ".log created on startup" "PASS"
    else
        report "$test_name" ".log created on startup" "FAIL"
    fi

    # Step 2: Wait for the -p prompt to be processed and get a reply
    # With -p flag, Claude will automatically process the prompt
    echo "    Waiting for Claude to process prompt..."
    wait_for_idle "$session" 10 180

    # Step 3: If -p was used, Claude already replied. Check jsonl grew.
    # Re-find jsonl in case it was created after initial check
    jsonl_file=$(find "$trace_dir" -name "*.jsonl" 2>/dev/null | head -1)
    if [ -n "$jsonl_file" ]; then
        local jsonl_size
        jsonl_size=$(get_file_size "$jsonl_file")
        if [ "$jsonl_size" -gt 0 ]; then
            report "$test_name" ".jsonl has content after interaction" "PASS"
        else
            report "$test_name" ".jsonl has content after interaction" "FAIL"
        fi
    else
        report "$test_name" ".jsonl has content after interaction" "FAIL"
    fi

    # Step 4: Exit Claude
    echo "    Exiting Claude ($exit_method)..."
    if [ "$exit_method" = "ctrlc" ]; then
        tmux send-keys -t "$session" C-c
        sleep 2
        # May need a second Ctrl+C
        tmux send-keys -t "$session" C-c
    else
        # For -p mode, Claude shows the result and waits. Send /exit.
        tmux send-keys -t "$session" "/exit" Enter
    fi

    # Step 5: Wait for claude-tap to fully exit and generate HTML
    echo "    Waiting for claude-tap to exit..."
    local wait_count=0
    while [ "$wait_count" -lt 60 ]; do
        local pane_content
        pane_content=$(tmux capture-pane -t "$session" -p 2>/dev/null || true)
        if echo "$pane_content" | grep -q "CLAUDE-TAP EXITED"; then
            echo "    claude-tap exited."
            break
        fi
        sleep 2
        wait_count=$((wait_count + 2))
    done

    if [ "$wait_count" -ge 60 ]; then
        echo "    WARNING: Timed out waiting for claude-tap to exit"
    fi

    # Give a moment for file writes to flush
    sleep 2

    # Step 6: Verify HTML generation
    local html_file
    html_file=$(find "$trace_dir" -name "*.html" 2>/dev/null | head -1)
    if [ -n "$html_file" ] && [ -s "$html_file" ]; then
        report "$test_name" ".html generated after exit" "PASS"
    else
        report "$test_name" ".html generated after exit" "FAIL"
    fi

    # Step 7: Validate JSONL content is valid JSON
    if [ -n "$jsonl_file" ] && [ -s "$jsonl_file" ]; then
        local bad_lines=0
        while IFS= read -r line; do
            if [ -n "$line" ] && ! echo "$line" | python3 -m json.tool > /dev/null 2>&1; then
                bad_lines=$((bad_lines + 1))
            fi
        done < "$jsonl_file"

        if [ "$bad_lines" -eq 0 ]; then
            report "$test_name" ".jsonl content is valid JSON" "PASS"
        else
            report "$test_name" ".jsonl content is valid JSON ($bad_lines bad lines)" "FAIL"
        fi
    else
        report "$test_name" ".jsonl content is valid JSON" "FAIL"
    fi

    # Clean up
    cleanup_session "$session"

    echo "=== Done: $test_name ==="
}

# ---------------------------------------------------------------------------
# Test scenarios
# ---------------------------------------------------------------------------

test_normal() {
    run_test "test_normal" "no" "exit"
}

test_live() {
    run_test "test_live" "yes" "exit"
}

test_ctrlc() {
    run_test "test_ctrlc" "no" "ctrlc"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

echo "=========================================="
echo " claude-tap E2E Test Suite"
echo "=========================================="
echo "Output dir: $TEST_OUTPUT_DIR"
echo ""

# Check dependencies
if ! command -v tmux &> /dev/null; then
    echo "ERROR: tmux is required. Install with: brew install tmux"
    exit 1
fi

if ! command -v claude-tap &> /dev/null; then
    echo "ERROR: claude-tap not found. Install with: pip install -e ."
    exit 1
fi

# Run selected or all tests
if [ -n "$SELECTED_TEST" ]; then
    case "$SELECTED_TEST" in
        normal)  test_normal ;;
        live)    test_live ;;
        ctrlc)   test_ctrlc ;;
        *)
            echo "Unknown test: $SELECTED_TEST"
            echo "Available: normal, live, ctrlc"
            exit 1
            ;;
    esac
else
    test_normal
    test_live
    test_ctrlc
fi

# Summary
echo ""
echo "=========================================="
echo " Results: ${PASS_COUNT}/${TOTAL_COUNT} passed"
if [ "$FAIL_COUNT" -gt 0 ]; then
    echo " FAILURES: $FAIL_COUNT"
fi
echo "=========================================="
echo "Test artifacts: $TEST_OUTPUT_DIR"

# Exit with failure if any test failed
if [ "$FAIL_COUNT" -gt 0 ]; then
    exit 1
fi
exit 0
