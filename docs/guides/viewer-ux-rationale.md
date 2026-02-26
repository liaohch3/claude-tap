# Viewer UX Rationale

This guide captures stable design decisions that were previously tracked in one-off implementation specs.

## Navigation Order

Keyboard and touch navigation must follow the same visual order shown in the sidebar.

- Sidebar grouping and sorting define the user's expected navigation order.
- `j/k` and arrow-key navigation should move through visible items in DOM order.
- Mobile previous/next controls should use the same order as desktop keyboard navigation.

## Mobile-First Constraints

The viewer must remain usable on narrow screens without horizontal overflow.

- Mobile layout should prioritize one primary pane at a time.
- Detail view actions need touch-friendly controls and clear boundaries.
- Diff and content-heavy views should switch to stacked layouts on mobile.

## Diff Matching Semantics

Diff quality depends on comparing related turns.

- Prefer history-aware matching (shared message prefix or equivalent thread signal).
- If fallback matching is approximate, show an explicit warning in the UI.
- Allow manual target selection so users can override automatic matching.

## Internationalization

New user-visible UI text must be localized consistently.

- Route all strings through the translation layer.
- Keep language packs complete when adding new keys.

## Testing and Scope Discipline

Viewer UX work should remain focused and verifiable.

- Prefer constrained changes in `claude_tap/viewer.html` when possible.
- Keep desktop behavior stable while improving mobile UX.
- Validate behavior through tests and manual trace-viewer verification.
