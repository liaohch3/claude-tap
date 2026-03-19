---
name: playwright-screen-recording
description: Record browser test videos with Playwright for PR review and bug fix verification
user_invocable: false
---

# Playwright Screen Recording for Test Verification

Use Playwright's video recording to capture headless browser operations as .webm videos for PR review or bug fix verification.

## Core Usage

```python
from playwright.sync_api import sync_playwright
import tempfile
from pathlib import Path

video_dir = Path(tempfile.mkdtemp())

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        viewport={"width": 1400, "height": 900},
        record_video_dir=str(video_dir),
        record_video_size={"width": 1400, "height": 900},
    )
    page = context.new_page()
    page.goto(f"file:///path/to/test.html")

    # ... perform test actions, add pauses for readability ...
    page.wait_for_timeout(800)  # pause so viewers can see the current state

    page.close()
    context.close()  # video is finalized after context.close()
    browser.close()

# Retrieve the recorded video
videos = list(video_dir.glob("*.webm"))
if videos:
    videos[0].rename("demo.webm")
```

## Use Cases

- **Bug fix verification**: record before/after comparisons showing button state changes, UI behavior differences
- **PR Review**: attach .webm video so reviewers can visually understand the change
- **Regression test evidence**: record critical interaction paths as visual proof of passing tests

## Recording Tips

### Add pauses between actions

```python
page.click(".some-button")
page.wait_for_timeout(800)   # let viewers see the click effect

page.keyboard.press("ArrowRight")
page.wait_for_timeout(600)   # let viewers see the navigation result
```

### Combine assertions with terminal logging

```python
state = get_nav_state(page)
print(f"[1] Title: {state['title']}")
print(f"    prev disabled: {state['prevDisabled']}  (expected: True)")
assert state["prevDisabled"] is True
print("    PASS")
```

Terminal output paired with the recorded video provides dual verification.

### Prefer real data

Use existing real trace data from the project rather than synthetic data for more convincing demos:

```python
# Build test HTML from a real trace file
records = []
with open(".traces/trace_xxx.jsonl") as f:
    for line in f:
        # Escape </script> to prevent breaking the HTML script block
        records.append(line.strip().replace("</script>", '</scr" + "ipt>'))
```

## Notes

- Video format is `.webm` (VP8 codec), supported by most players and browsers
- Each `page` produces a separate video file
- `record_video_size` controls video resolution — keep it consistent with `viewport`
- Recording works in headless mode, no display required
- Video files are typically a few hundred KB, suitable for attaching to PRs or chat
