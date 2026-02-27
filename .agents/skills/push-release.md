---
name: push-release
description: Push to GitHub and optionally bump version to trigger PyPI release
user_invocable: true
---

# Push & Release

Push code to GitHub. If the pending commits contain feature changes, bump the version number so CI auto-publishes to PyPI.

## Workflow

1. **Check working tree**: Ensure no uncommitted changes (prompt user to commit first if dirty).

2. **Determine whether a version bump is needed**:
   - Read current version from `pyproject.toml`
   - Run `git log origin/main..HEAD --oneline` to inspect pending commits
   - If commits include feature changes (feat/fix/refactor, not purely docs/chore/test), a bump is needed
   - If only docs, tests, or CI changes, skip the bump

3. **If bump is needed**:
   - Choose bump level based on change type:
     - **patch** (0.1.4 → 0.1.5): bug fixes, minor improvements
     - **minor** (0.1.4 → 0.2.0): new features
     - **major** (0.1.4 → 1.0.0): breaking changes
   - Update the `version` field in `pyproject.toml`
   - Update `__version__` in `claude_tap/__init__.py`
   - `git commit --amend` to fold the version bump into the last commit (avoids extra commits)

4. **Push**:
   ```bash
   git push origin main
   ```

5. **Confirm CI status**:
   - Inform the user that CI will automatically: lint → test → auto-tag → PyPI publish
   - Provide the GitHub Actions link: https://github.com/liaohch3/claude-tap/actions

## Important: Version Bump = PyPI Release

The CI pipeline works as follows: push to main → auto-tag (only if version changed) → PyPI publish (triggered by new tag).

**A version bump is the ONLY way to trigger a new PyPI release.** If you push without bumping the version, CI will skip tagging and nothing gets published. So whenever commits include meaningful code changes (features, fixes, improvements), you MUST bump the version before pushing.

- Version numbers in `pyproject.toml` and `claude_tap/__init__.py` must stay in sync
- Only skip the bump for pure docs/test/CI changes that don't affect the published package
