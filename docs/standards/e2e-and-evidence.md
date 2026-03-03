---
owner: claude-tap-maintainers
last_reviewed: 2026-03-03
source_of_truth: AGENTS.md
---

# E2E Validation Requirements

If a change affects proxying, trace capture, CLI session flow, auth handling, or other end-to-end behavior, run real E2E validation before opening a PR.

Preferred commands:

```bash
uv run pytest tests/e2e/ --run-real-e2e --timeout=300
uv run pytest tests/e2e/test_real_proxy.py::TestRealProxy::test_single_turn --run-real-e2e --timeout=180
```

Manual alternatives:

```bash
scripts/run_real_e2e.sh
scripts/run_real_e2e_tmux.sh
```

If real E2E cannot run (for example, missing auth/token), document reason and residual risk in the PR body.

# E2E Conversation Rule

Each E2E run must include at least one complete multi-turn conversation.
For conversation validation and screenshot evidence, use tmux interactive flow (`scripts/run_real_e2e_tmux.sh`).
Do not use `claude -p` one-shot runs as proof of conversation completeness.

# UI Evidence Requirements

For PRs changing UI layout, styles, interaction flow, or rendered content:

- Include at least one screenshot per changed screen/state.
- Include before/after screenshots when a visual diff matters.
- Include mobile screenshots when mobile behavior is affected.
- Use real trace artifacts from `.traces/trace_*.jsonl` or real run outputs.
- For E2E-related UI changes, screenshots must come from a run that completed at least one full multi-turn conversation.
