---
owner: claude-tap-maintainers
last_reviewed: 2026-03-03
source_of_truth: AGENTS.md
---

# Standards Metadata

All files in `docs/standards/*.md` must include frontmatter with:

- `owner`: team or maintainer responsible for updates.
- `last_reviewed`: ISO date `YYYY-MM-DD` of the last policy review.
- `source_of_truth`: canonical policy source reference.

# Maintenance Workflow

1. Update the affected standards file and refresh `last_reviewed`.
2. Keep `AGENTS.md` as a concise index that links to the updated file.
3. Run `python scripts/check_legibility.py` locally.
4. If policy behavior changed, record rationale in PR description.
