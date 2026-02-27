---
name: js-in-html-testing
description: Test JS logic embedded in HTML using two-layer strategy - Python unit tests + Playwright browser integration tests
user_invocable: false
---

# JS-in-HTML Two-Layer Testing Strategy

For JavaScript logic embedded in HTML files (e.g., diff navigation in viewer.html), use a two-layer testing approach.

## Layer 1: Python Unit Tests (fast algorithm verification)

Replicate core JS algorithms in Python and verify correctness via pytest.

Best for: pure computation logic, state decisions, matching algorithms — anything that doesn't depend on the DOM.

**Example**: `tests/test_diff_matching.py`

```python
# Replicate JS findPrevSameModel / findNextSameModel
def find_diff_parent_by_prefix(entries, idx):
    ...

def find_next_by_prefix(entries, idx):
    ...

# Replicate JS updateNavButtons state computation
def compute_nav_button_states(entries, cur_idx):
    prev_idx = find_diff_parent_by_prefix(entries, cur_idx)
    ...
    return (prev_enabled, next_enabled)
```

Advantages: fast (0.02s), no browser dependency, integrates with existing pytest setup.

## Layer 2: Playwright Browser Integration Tests (DOM interaction verification)

Generate HTML with test data embedded, open it in real Chromium via Playwright, and verify DOM state and user interactions.

Best for: button disabled states, overlay show/hide, keyboard events, click navigation, etc.

**Example**: `tests/test_nav_browser.py`

### Building test HTML

```python
def _build_test_html():
    template = Path("claude_tap/viewer.html").read_text()
    records = [json.dumps(e) for e in TEST_ENTRIES]
    data_js = "const EMBEDDED_TRACE_DATA = [\n" + ",\n".join(records) + "\n];\n"
    # Inject data into template
    return template.replace(
        "<script>\nconst $ = s =>",
        f"<script>\n{data_js}</script>\n<script>\nconst $ = s =>",
        1,
    )
```

### Playwright fixture

```python
@pytest.fixture(scope="module")
def browser_page(html_file):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto(f"file://{html_file}")
    page.wait_for_selector(".sidebar-item", timeout=5000)
    yield page
    browser.close()
    pw.stop()
```

### Verifying DOM state

```python
def _get_nav_state(page):
    return page.evaluate("""() => {
        const overlay = document.querySelector('.diff-overlay');
        if (!overlay) return { overlayExists: false };
        return {
            overlayExists: true,
            prevDisabled: overlay.querySelector('.diff-nav-prev')?.disabled,
            nextDisabled: overlay.querySelector('.diff-nav-next')?.disabled,
            title: overlay.querySelector('.diff-title')?.textContent,
        };
    }""")
```

### Simulating user interaction

```python
# Click buttons
page.click(".diff-nav-next")
# Keyboard navigation
page.keyboard.press("ArrowRight")
# Call internal JS functions
page.evaluate("selectEntry(2)")
page.evaluate("showDiff()")
```

## Notes

- Trace data may contain `</script>` text (e.g., when Claude discusses code), which breaks `<script>` block parsing. Escape it: `line.replace("</script>", '</scr" + "ipt>')`
- Test data must match the viewer's expected JSONL format (including `turn`, `duration_ms`, `request_id`, `request.path`, etc.)
- Playwright requires `uv pip install playwright`

## Running

```bash
# Fast unit tests
uv run pytest tests/test_diff_matching.py -v

# Browser integration tests
uv run pytest tests/test_nav_browser.py -v

# All (excluding slow e2e)
uv run pytest tests/ --ignore=tests/test_e2e.py -v
```
