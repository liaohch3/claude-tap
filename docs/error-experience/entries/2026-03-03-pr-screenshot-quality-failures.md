# PR Screenshot Quality Failures (2026-03-03)

## What Happened

PR #22 (WebSocket proxy fix) needed screenshot evidence. Three separate quality failures occurred before producing acceptable screenshots:

### Failure 1: Mobile Viewport Layout
- **Symptom**: Trace viewer rendered as mobile layout — cramped, single-column, unreadable
- **Root cause**: OpenClaw built-in browser defaults to a narrow viewport (~750px), triggering responsive mobile breakpoints
- **Impact**: Screenshot showed mobile UI that didn't match what users actually see

### Failure 2: Unicode Arrow Corruption
- **Symptom**: Log file arrows `→` and `←` rendered as garbled characters `鉞@` in the screenshot
- **Root cause**: The log file contained Unicode arrows. The browser or font rendering in the headless environment corrupted multi-byte characters. The raw `.log` file was served directly without charset handling.
- **Impact**: Screenshot was unreadable for the key evidence (WS direction indicators)

### Failure 3: Wrong Content in Screenshot
- **Symptom**: Trace viewer showed `/v1/models` request detail instead of the WebSocket `/v1/responses` request
- **Root cause**: Didn't click into the correct trace entry before taking the screenshot. Assumed the default view would show the WS request.
- **Impact**: Screenshot didn't prove what the PR claimed

### Meta-Failure: No Pre-Commit Review
- All three bad screenshots were committed and pushed to the PR without being reviewed first
- User had to manually inspect and flag each issue
- Multiple round-trips to fix what should have been caught before commit

## How It Was Fixed

1. **Viewport**: `browser act resize width=1440 height=900` before taking screenshots
2. **Unicode**: Created a custom HTML card (`ws-log-clean.html`) using HTML entities (`&gt;` `&lt;` `->`) instead of raw Unicode arrows
3. **Content**: Navigated to the correct trace entry (Turn 2 WEBSOCKET) and verified the content before capturing
4. **Review**: Visually inspected each screenshot before committing

## Standards Derived

### Screenshot Pre-Commit Checklist
1. **Viewport**: Set to desktop width (≥1280px) before capture
2. **Content**: Verify the screenshot shows exactly what you claim it shows
3. **Encoding**: Check for garbled/corrupted characters — especially Unicode symbols, CJK text, emoji
4. **Layout**: Confirm desktop layout rendered (not mobile/responsive breakpoint)
5. **Readability**: Key evidence text must be legible at 1x zoom
6. **Review**: View the actual PNG file before `git add` — never blind-commit screenshots

### Automation Opportunities
- `scripts/check_screenshots.sh`: Automated checks for image dimensions (reject <1000px wide as likely mobile), file size (reject <10KB as likely error pages), and basic sanity
- PR body template could include a screenshot checklist section
- Consider generating evidence screenshots programmatically with fixed viewport settings

## Prevention

- Added screenshot quality gate to `docs/standards/e2e-and-evidence.md`
- Created `scripts/check_screenshots.sh` for automated pre-commit validation
- AGENTS.md should reference the screenshot standard for any PR with visual evidence

## Key Takeaway

Screenshots are evidence. Evidence must be verified before submission. "I took a screenshot" is not the same as "I verified the screenshot proves what I claim."
