#!/bin/sh
set -eu

usage() {
  cat <<'EOF'
Usage: scripts/check_pr.sh <pr_number> [--repo owner/repo] [--no-tests]

Options:
  --repo OWNER/REPO  Override repository (default: current gh repo)
  --no-tests         Skip local test gates
  -h, --help         Show this help
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "error: required command not found: $1" >&2
    exit 1
  fi
}

pr_number=""
repo=""
run_tests=1

while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo)
      if [ "$#" -lt 2 ]; then
        echo "error: --repo requires a value" >&2
        usage
        exit 1
      fi
      repo="$2"
      shift 2
      ;;
    --no-tests)
      run_tests=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "error: unknown option: $1" >&2
      usage
      exit 1
      ;;
    *)
      if [ -n "$pr_number" ]; then
        echo "error: unexpected extra argument: $1" >&2
        usage
        exit 1
      fi
      pr_number="$1"
      shift
      ;;
  esac
done

if [ -z "$pr_number" ]; then
  echo "error: missing <pr_number>" >&2
  usage
  exit 1
fi

require_cmd gh
if [ "$run_tests" -eq 1 ]; then
  require_cmd uv
fi

if [ -z "$repo" ]; then
  repo="$(gh repo view --json nameWithOwner --template '{{.nameWithOwner}}')"
  if [ -z "$repo" ]; then
    echo "error: failed to detect repository; use --repo owner/repo" >&2
    exit 1
  fi
fi

metadata="$(gh pr view "$pr_number" --repo "$repo" \
  --json title,state,isDraft,mergeStateStatus,headRefName,baseRefName,url \
  --template '{{.title}}{{"\n"}}{{.state}}{{"\n"}}{{.isDraft}}{{"\n"}}{{.mergeStateStatus}}{{"\n"}}{{.headRefName}}{{"\n"}}{{.baseRefName}}{{"\n"}}{{.url}}')"

pr_title=$(printf '%s\n' "$metadata" | sed -n '1p')
pr_state=$(printf '%s\n' "$metadata" | sed -n '2p')
pr_draft=$(printf '%s\n' "$metadata" | sed -n '3p')
pr_merge_status=$(printf '%s\n' "$metadata" | sed -n '4p')
pr_head=$(printf '%s\n' "$metadata" | sed -n '5p')
pr_base=$(printf '%s\n' "$metadata" | sed -n '6p')
pr_url=$(printf '%s\n' "$metadata" | sed -n '7p')

pr_body="$(gh pr view "$pr_number" --repo "$repo" --json body --template '{{.body}}')"

if [ -z "$pr_title" ] || [ -z "$pr_state" ] || [ -z "$pr_merge_status" ]; then
  echo "error: failed to parse PR metadata from gh" >&2
  exit 1
fi

check_lines=""
if check_lines="$(gh pr checks "$pr_number" --repo "$repo" --json bucket,name,state \
  --template '{{range .}}{{.bucket}}{{"\t"}}{{.name}}{{"\t"}}{{.state}}{{"\n"}}{{end}}')"; then
  :
else
  checks_exit=$?
  if [ "$checks_exit" -ne 8 ]; then
    echo "error: failed to fetch PR checks (gh exit $checks_exit)" >&2
    exit 1
  fi
fi

pass_count=0
fail_count=0
pending_count=0
check_total=0

if [ -n "$check_lines" ]; then
  tab_char=$(printf '\t')
  while IFS=$tab_char read -r bucket check_name check_state; do
    [ -n "$bucket" ] || continue
    check_total=$((check_total + 1))
    case "$bucket" in
      pass|skipping)
        pass_count=$((pass_count + 1))
        ;;
      fail|cancel)
        fail_count=$((fail_count + 1))
        ;;
      pending)
        pending_count=$((pending_count + 1))
        ;;
      *)
        pending_count=$((pending_count + 1))
        ;;
    esac
  done <<EOF
$check_lines
EOF
fi

printf 'PR #%s (%s)\n' "$pr_number" "$repo"
printf 'Title: %s\n' "$pr_title"
printf 'URL: %s\n' "$pr_url"
printf 'State: %s | Draft: %s | Merge State: %s\n' "$pr_state" "$pr_draft" "$pr_merge_status"
printf 'Branch: %s -> %s\n' "$pr_head" "$pr_base"
printf 'Checks: pass=%s fail=%s pending=%s total=%s\n' "$pass_count" "$fail_count" "$pending_count" "$check_total"

gate_failed=0
if [ "$run_tests" -eq 1 ]; then
  echo 'Local gates:'

  if uv run ruff check .; then
    echo '  PASS uv run ruff check .'
  else
    gate_failed=1
    echo '  FAIL uv run ruff check .'
  fi

  if uv run ruff format --check .; then
    echo '  PASS uv run ruff format --check .'
  else
    gate_failed=1
    echo '  FAIL uv run ruff format --check .'
  fi

  if uv run pytest tests/ -x --timeout=60; then
    echo '  PASS uv run pytest tests/ -x --timeout=60'
  else
    gate_failed=1
    echo '  FAIL uv run pytest tests/ -x --timeout=60'
  fi
else
  echo 'Local gates: skipped (--no-tests)'
fi

# Screenshot / evidence check
has_screenshot=0
if printf '%s' "$pr_body" | grep -qiE '!\[.*\]\(.*\.(png|jpg|jpeg|gif|svg|webp)'; then
  has_screenshot=1
fi
if printf '%s' "$pr_body" | grep -qiE '\.(png|jpg|jpeg|gif|svg|webp)\)'; then
  has_screenshot=1
fi
if printf '%s' "$pr_body" | grep -qiE '<img '; then
  has_screenshot=1
fi

if [ "$has_screenshot" -eq 1 ]; then
  echo 'Screenshots: found in PR body'
else
  cat <<'SCREENSHOT_ERR'
Screenshots: MISSING

  Every PR must include screenshots of the real running system.

  Our screenshot standard (docs/standards/screenshot-standards.md):
  - Screenshots must show the trace viewer HTML at desktop viewport (>=1280px)
  - Must capture the exact feature/fix claimed (e.g. WEBSOCKET 101, not unrelated GET)
  - Use ASCII-safe characters, no raw Unicode arrows that may garble
  - Run `python3 scripts/check_screenshots.py docs/evidence/` before commit

  What to do:
  1. Run the feature, open the trace viewer HTML in a browser (>=1280px wide)
  2. Screenshot the specific row/panel proving the fix works
  3. Add image to docs/evidence/ and link in PR body with ![description](url)
  4. Verify: no garbled text, correct content shown, legible at normal zoom
SCREENSHOT_ERR
fi

ready=1
reasons=""

append_reason() {
  if [ -n "$reasons" ]; then
    reasons="$reasons; $1"
  else
    reasons="$1"
  fi
}

if [ "$pr_state" != "OPEN" ]; then
  ready=0
  append_reason "PR state is $pr_state"
fi

if [ "$pr_draft" = "true" ]; then
  ready=0
  append_reason 'PR is draft'
fi

case "$pr_merge_status" in
  CLEAN|HAS_HOOKS)
    ;;
  *)
    ready=0
    append_reason "mergeStateStatus is $pr_merge_status"
    ;;
esac

if [ "$fail_count" -gt 0 ]; then
  ready=0
  append_reason "$fail_count CI check(s) failing"
fi

if [ "$pending_count" -gt 0 ]; then
  ready=0
  append_reason "$pending_count CI check(s) pending"
fi

if [ "$gate_failed" -eq 1 ]; then
  ready=0
  append_reason 'local gates failed'
fi
if [ "$has_screenshot" -eq 0 ]; then
  ready=0
  append_reason 'no screenshots in PR body'
fi

if [ "$ready" -eq 1 ]; then
  echo 'VERDICT: READY - all merge-readiness checks passed'
  exit 0
fi

echo "VERDICT: NOT_READY - $reasons"
exit 2
