# Skill: Demo Video Generation

Generate demo assets from a real tmux E2E run and viewer screenshots.

## Proven Workflow

### 1) Record terminal session in tmux with asciinema

```bash
tmux new-session -d -s demo -x 160 -y 46
tmux send-keys -t demo "asciinema rec /tmp/claude-tap-recordings/demo.cast" Enter
tmux send-keys -t demo "cd /path/to/claude-tap && scripts/run_real_e2e_tmux.sh" Enter
# ... wait until run finishes ...
tmux send-keys -t demo "exit" Enter
```

Notes:

- `Enter` is the submit key for Claude Code TUI in tmux.
- Use tool-triggering first prompt text so trace includes `tool_use`.

### 2) Convert `.cast` to GIF with `agg`

```bash
agg /tmp/claude-tap-recordings/demo.cast /tmp/claude-tap-recordings/demo.gif
```

### 3) Convert GIF to MP4 with ffmpeg

```bash
ffmpeg -y -i /tmp/claude-tap-recordings/demo.gif -movflags +faststart -pix_fmt yuv420p docs/demo.mp4
```

## Browser Screenshots (HTML Viewer)

Use Playwright CDP to attach to a running Chrome/Chromium instance on port `9222`.

```python
browser = playwright.chromium.connect_over_cdp("http://127.0.0.1:9222")
page = browser.contexts[0].pages[0]
```

### Reliable UI interactions

- Click entries by visible text content, for example:

```python
page.query_selector('text="轮次 22"').click()
```

- Open diff view by clicking button text:

```python
page.query_selector('text="对比上次"').click()
```

- For SPA/overflow layouts, scroll actual scrollable containers (not `window`):

```python
page.evaluate("""
() => {
  for (const el of document.querySelectorAll('*')) {
    if (el.scrollHeight > el.clientHeight) {
      el.scrollTop += 300;
    }
  }
}
""")
```

## Output Targets

- `docs/demo.gif`
- `docs/demo.mp4`
- Optional localized variants (`docs/demo_zh.gif`, `docs/demo_zh.mp4`)

## Avoid

- Do not reference non-existent scripts such as `cast_to_gif_ultra.py` or `make_final_demo_v2.py`.
- Do not hardcode selectors like `.detail` unless verified in the current viewer build.
