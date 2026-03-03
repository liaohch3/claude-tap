#!/usr/bin/env bash
# check_screenshots.sh - Validate screenshot quality before commit
#
# Usage:
#   scripts/check_screenshots.sh [path...]
#
# If no paths given, checks all staged PNG/JPG files in git.
# Exit code 0 = all pass, 1 = failures found.

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

PASS=0
FAIL=0
WARN=0

MIN_WIDTH=1000
MIN_FILE_SIZE=10240  # 10KB in bytes

check_file() {
    local file="$1"
    local name
    name=$(basename "$file")
    local issues=()
    local warnings=()

    # Check file exists
    if [[ ! -f "$file" ]]; then
        echo -e "${RED}FAIL${NC} $file: file not found"
        ((FAIL++))
        return
    fi

    # Check file size
    local size
    size=$(stat -f%z "$file" 2>/dev/null || stat -c%s "$file" 2>/dev/null || echo 0)
    if (( size < MIN_FILE_SIZE )); then
        issues+=("file size ${size} bytes < ${MIN_FILE_SIZE} (likely blank/error page)")
    fi

    # Check dimensions (requires sips on macOS or identify from ImageMagick)
    local width=0
    local height=0
    if command -v sips &>/dev/null; then
        width=$(sips -g pixelWidth "$file" 2>/dev/null | awk '/pixelWidth/{print $2}')
        height=$(sips -g pixelHeight "$file" 2>/dev/null | awk '/pixelHeight/{print $2}')
    elif command -v identify &>/dev/null; then
        local dims
        dims=$(identify -format "%w %h" "$file" 2>/dev/null || echo "0 0")
        width=$(echo "$dims" | awk '{print $1}')
        height=$(echo "$dims" | awk '{print $2}')
    else
        warnings+=("cannot check dimensions (install sips or ImageMagick)")
    fi

    if (( width > 0 && width < MIN_WIDTH )); then
        issues+=("width ${width}px < ${MIN_WIDTH}px (likely mobile viewport)")
    fi

    if (( width > 0 && height > 0 )); then
        # Check aspect ratio - very tall narrow images are suspicious
        local ratio
        ratio=$(( height * 100 / width ))
        if (( ratio > 300 )); then
            warnings+=("aspect ratio ${width}x${height} is very tall/narrow — verify layout")
        fi
    fi

    # Check for common error page indicators in filename
    if [[ "$name" == *"error"* ]] || [[ "$name" == *"404"* ]]; then
        warnings+=("filename suggests error page")
    fi

    # Report
    if (( ${#issues[@]} > 0 )); then
        echo -e "${RED}FAIL${NC} $file"
        for issue in "${issues[@]}"; do
            echo "      - $issue"
        done
        ((FAIL++))
    elif (( ${#warnings[@]} > 0 )); then
        echo -e "${YELLOW}WARN${NC} $file (${width}x${height}, ${size} bytes)"
        for warning in "${warnings[@]}"; do
            echo "      - $warning"
        done
        ((WARN++))
    else
        echo -e "${GREEN}PASS${NC} $file (${width}x${height}, ${size} bytes)"
        ((PASS++))
    fi
}

# Collect files to check
files=()
if (( $# > 0 )); then
    files=("$@")
else
    # Check staged image files
    while IFS= read -r f; do
        [[ -n "$f" ]] && files+=("$f")
    done < <(git diff --cached --name-only --diff-filter=ACM 2>/dev/null | grep -iE '\.(png|jpg|jpeg|webp)$' || true)

    if (( ${#files[@]} == 0 )); then
        # Fallback: check all images in docs/evidence/
        while IFS= read -r f; do
            [[ -n "$f" ]] && files+=("$f")
        done < <(find docs/evidence -type f -name '*.png' -o -name '*.jpg' 2>/dev/null || true)
    fi
fi

if (( ${#files[@]} == 0 )); then
    echo "No screenshot files to check."
    exit 0
fi

echo "Checking ${#files[@]} screenshot(s)..."
echo ""

for f in "${files[@]}"; do
    check_file "$f"
done

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed, ${WARN} warnings"

if (( FAIL > 0 )); then
    echo -e "${RED}Screenshot quality gate FAILED${NC}"
    exit 1
fi

if (( WARN > 0 )); then
    echo -e "${YELLOW}Passed with warnings — review manually${NC}"
fi

exit 0
