# Skill: Demo Video Generation

Generate demo videos/GIFs for claude-tap README from real trace data.

## Overview

The demo video is a **sequential** composition:
1. **Intro title card** — project name + tagline
2. **Terminal TUI section** — real claude-tap session recorded via asciinema/tmux, rendered to PNG frames with pyte
3. **Transition card** — announces the HTML viewer section
4. **Browser viewer section** — Playwright screenshots of the HTML trace viewer with scrolling
5. **Outro title card** — install command + GitHub URL

## Pipeline

### Step 1: Record TUI Session

```bash
# Start tmux session with asciinema recording
tmux new-session -d -s demo -x 120 -y 36
tmux send-keys -t demo "asciinema rec demo.cast" Enter
sleep 2
tmux send-keys -t demo "claude-tap -- claude" Enter
# Wait for Claude Code to load, then send prompts via tmux send-keys
# ...
# Stop recording
tmux send-keys -t demo "exit" Enter
```

### Step 2: Render .cast to PNG Frames

Use `cast_to_gif_ultra.py` with **pyte** terminal emulator:
- Parse asciinema v2 .cast file (JSON lines with timestamps)
- Feed output through pyte.Screen to get terminal state at each frame
- Render terminal state to PNG using Pillow (24px monospace font for HD)
- Output: `tui_frames_ultra/frame_NNNN.png` at 2fps

Key settings:
- Resolution: 1600x1002 (120 cols × 36 rows × 24px font)
- Font: DejaVu Sans Mono 24px
- Background: #18181b (zinc-900)
- Foreground: #e4e4e7 (zinc-200)

### Step 3: Record Browser Viewer

Use Playwright headless Chrome:
- Serve trace HTML via `python -m http.server`
- Navigate through tabs: Turn N → Tools → Diff
- **Scroll the `.detail` container** (not `window`!) — the viewer is a SPA with overflow containers
- Screenshot each state

Critical scrolling code:
```python
# RIGHT: scroll the content panel
await page.evaluate("document.querySelector('.detail').scrollBy(0, 300)")

# WRONG: window.scrollBy has no effect in this SPA
await page.evaluate("window.scrollBy(0, 300)")  # ← doesn't work!
```

### Step 4: Compose Final Video

`make_final_demo_v2.py` handles:
1. **Title cards**: Pillow-rendered text on dark background with accent bar
2. **TUI frame dedup**: Remove consecutive identical frames (stalls during Claude thinking), keep max 3 same frames (~0.5s pause at 6fps)
3. **Browser frames**: Direct from Playwright screenshots
4. **Encoding**: ffmpeg libx264 CRF 18 at 6fps

### Step 5: Generate GIFs

`make_demo_gifs.py`:
- EN GIF: frames as-is
- ZH GIF: replace title card text with Chinese (using NotoSansCJK font)
- Two-pass ffmpeg palettegen for quality GIFs
- Also outputs MP4 versions

## Key Lessons Learned

1. **SPA scrolling**: HTML viewer uses `overflow-y: auto` on `.detail` and `.sidebar` divs. Must scroll those specific elements, not `window`.
2. **Frame dedup is essential**: Claude's thinking time creates 60-80 identical consecutive frames. Cap at 3 to keep ~0.5s visual pause.
3. **Full pixel hash for dedup**: Thumbnail-based hash is too lossy — misses subtle terminal changes. Use `hashlib.md5(img.tobytes())`.
4. **MP4 > GIF for Telegram**: Telegram heavily compresses GIFs. MP4 preserves quality.
5. **GIF for GitHub README**: GitHub renders GIFs inline but not MP4. Use GIF in README, provide MP4 link as alternative.
6. **CJK fonts**: NotoSansCJK-Bold.ttc at `/usr/share/fonts/opentype/noto/` for Chinese title cards.
7. **pyte for cast rendering**: asciinema .cast → pyte.Screen → PIL Image. Handles ANSI escape codes properly.

## Output Files

```
docs/demo.gif        # EN demo GIF (for README.md)
docs/demo_zh.gif     # ZH demo GIF (for README_zh.md)
docs/demo.mp4        # EN demo MP4 (higher quality alternative)
docs/demo_zh.mp4     # ZH demo MP4
```

## Scripts

```
cast_to_gif_ultra.py   # .cast → PNG frames (pyte renderer)
make_final_demo_v2.py  # Compose sequential video from frames
make_demo_gifs.py      # Generate EN/ZH GIF + MP4
record_browser_v2.py   # Record browser viewer with scrolling
```
