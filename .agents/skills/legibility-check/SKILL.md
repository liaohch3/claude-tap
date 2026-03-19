---
name: legibility-check
description: Validate docs structure, standards freshness, manifest paths, and plan state. Run this after modifying any file under docs/standards/, docs/plans/, docs/architecture/, or AGENTS.md — it catches stale metadata, broken manifest paths, and plan state drift before CI does.
user_invocable: true
---

# Legibility Check

Run deterministic legibility checks that mirror what CI enforces via `.github/workflows/legibility.yml`. Catching these locally saves a round-trip to CI.

## What it checks

1. **Standards freshness** — every `docs/standards/*.md` must have frontmatter with `owner`, `last_reviewed` (ISO date), and `source_of_truth`. Files reviewed more than 60 days ago produce a warning.
2. **Architecture manifest** — every path listed in `docs/architecture/manifest.yaml` under `expected_paths:` must exist in the repo.
3. **Plan state drift** — every `docs/plans/**/*.md` must have a `status` frontmatter field (`active`, `completed`, or `cancelled`). Completed plans must not contain unchecked `- [ ]` checkboxes (outside fenced code blocks).

## Run

```bash
uv run python scripts/check_legibility.py
```

Options:
- `--freshness-days N` — change the staleness threshold (default: 60)
- `--strict-freshness` — promote stale warnings to failures
- `--repo-root PATH` — override repo root (default: cwd)

## Fixing common failures

| Failure | Fix |
|---------|-----|
| `missing frontmatter key 'X'` | Add the missing key to the YAML frontmatter block at the top of the file |
| `last_reviewed must be ISO date` | Use `YYYY-MM-DD` format |
| `last_reviewed ... is stale` | Update `last_reviewed` to today's date after reviewing the content |
| `expected path missing: X` | Either create the file or remove the stale entry from `manifest.yaml` |
| `status must be one of [...]` | Add `status: active` (or `completed`/`cancelled`) to plan frontmatter |
| `completed plan still contains unchecked TODO` | Check off remaining items or change status back to `active` |

## After fixing

Re-run the check to confirm all issues are resolved before committing:

```bash
uv run python scripts/check_legibility.py && echo "All clear"
```
