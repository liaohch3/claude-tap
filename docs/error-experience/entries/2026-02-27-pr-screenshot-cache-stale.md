# PR Screenshot Cache Staleness on GitHub Raw URLs

**Date:** 2026-02-27
**Tags:** github, pr, screenshot, cache, review

## Problem

After pushing updated screenshots to a PR branch, the PR description still displayed old images, making it appear that changes were not applied.

## Root Cause

PR descriptions referenced stable `raw.githubusercontent.com` image paths with the same filenames.
GitHub/CDN caching can keep serving stale content for those URLs for a period of time.

## Impact

- Review confusion: reviewers believe PR evidence was not updated.
- Extra communication overhead and repeated manual refresh attempts.

## Fix Applied

1. Generated new image files with versioned names (`*-v2.png`).
2. Updated PR description image links to point to the new filenames.
3. Confirmed PR references moved to the versioned URLs.

## Preventive Rule

When updating PR-embedded screenshots, prefer immutable image URLs by changing filenames
(`before-v2.png`, `after-v3.png`) instead of reusing existing names.

## Verification Checklist

- New files appear in `Files changed`.
- PR markdown links point to versioned filenames.
- Reviewer can view updated images without hard-refresh dependency.
