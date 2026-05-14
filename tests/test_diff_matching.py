#!/usr/bin/env python3
"""Tests for diff pair matching logic.

The viewer's "diff" feature compares consecutive API requests to show what changed.
Current behavior: find the previous request with the same model + isMainTurn flag.
Problem: when Claude Code spawns subagents (parallel LLM calls), requests from
different conversation threads interleave. Time/model-based matching pairs unrelated
requests, producing meaningless diffs.

Correct behavior: match by **message prefix** — if request B's messages[:N] == request A's
messages, then B extends A's conversation thread and they should be diffed together.
"""

import hashlib
import json

# ── Test data: real trace from trace_20260218_083822.jsonl ──
# Simplified to essential fields for testing

TRACE_ENTRIES = [
    {  # idx 0: initial opus request
        "model": "claude-opus-4-6",
        "system": "",
        "messages": [{"role": "user", "content": "foo"}],
    },
    {  # idx 1: haiku subagent (different task)
        "model": "claude-haiku-4-5-20251001",
        "system": "",
        "messages": [{"role": "user", "content": "quota"}],
    },
    {  # idx 2: haiku subagent (web search)
        "model": "claude-haiku-4-5-20251001",
        "system": [{"type": "text", "text": "You are a web search agent"}],
        "messages": [{"role": "user", "content": "search the web for latest Claude Code news"}],
    },
    {  # idx 3: opus main thread continues
        "model": "claude-opus-4-6",
        "system": [{"type": "text", "text": "You are Claude Code"}],
        "messages": [{"role": "user", "content": "system-reminder skills"}],
    },
    {  # idx 4: haiku subagent A (search query 1)
        "model": "claude-haiku-4-5-20251001",
        "system": [{"type": "text", "text": "Search agent v1"}],
        "messages": [{"role": "user", "content": "Perform a web search for Claude Code updates"}],
    },
    {  # idx 5: haiku subagent B (same user msg, different system prompt)
        "model": "claude-haiku-4-5-20251001",
        "system": [{"type": "text", "text": "Search agent v2"}],
        "messages": [{"role": "user", "content": "Perform a web search for Claude Code updates"}],
    },
    {  # idx 6: opus main thread turn 2 (extends idx 3)
        "model": "claude-opus-4-6",
        "system": [{"type": "text", "text": "You are Claude Code v2"}],
        "messages": [
            {"role": "user", "content": "system-reminder skills"},
            {"role": "assistant", "content": "I found some news..."},
            {"role": "user", "content": "tell me more"},
        ],
    },
    {  # idx 7: opus main thread turn 3 (extends idx 6)
        "model": "claude-opus-4-6",
        "system": [{"type": "text", "text": "You are Claude Code v3"}],
        "messages": [
            {"role": "user", "content": "system-reminder skills"},
            {"role": "assistant", "content": "I found some news..."},
            {"role": "user", "content": "tell me more"},
            {"role": "assistant", "content": "Here are the details..."},
            {"role": "user", "content": "suggestion mode"},
        ],
    },
    {  # idx 8: haiku subagent (unrelated)
        "model": "claude-haiku-4-5-20251001",
        "system": [{"type": "text", "text": "Haiku writer"}],
        "messages": [{"role": "user", "content": "now write a haiku"}],
    },
    {  # idx 9: opus main thread (extends idx 6, parallel branch from idx 7)
        "model": "claude-opus-4-6",
        "system": [{"type": "text", "text": "You are Claude Code v4"}],
        "messages": [
            {"role": "user", "content": "system-reminder skills"},
            {"role": "assistant", "content": "I found some news..."},
            {"role": "user", "content": "tell me more"},
            {"role": "assistant", "content": "Here are the details v2..."},
            {"role": "user", "content": "different follow up"},
        ],
    },
    {  # idx 10: opus main thread (extends idx 6, longer chain)
        "model": "claude-opus-4-6",
        "system": [{"type": "text", "text": "You are Claude Code v5"}],
        "messages": [
            {"role": "user", "content": "system-reminder skills"},
            {"role": "assistant", "content": "I found some news..."},
            {"role": "user", "content": "tell me more"},
            {"role": "assistant", "content": "Here are the details..."},
            {"role": "user", "content": "ok summarize"},
            {"role": "assistant", "content": "Summary: ..."},
            {"role": "user", "content": "suggestion mode"},
        ],
    },
]

# ── Expected results ──
# For each entry, what's the correct "previous request" to diff against?
# None means it's a new thread with no parent.

EXPECTED_DIFF_PARENT = {
    0: None,  # new thread
    1: None,  # new thread (different content from idx 0)
    2: None,  # new thread (subagent)
    3: None,  # new thread (main agent start)
    4: None,  # new thread (subagent A)
    5: 4,  # same user message as idx 4 (but note: different system prompt)
    6: 3,  # extends idx 3 (messages[0] matches)
    7: 6,  # extends idx 6 (messages[:3] match)
    8: None,  # new thread (unrelated subagent)
    9: 6,  # extends idx 6 (messages[:3] match, diverges after)
    10: 6,  # extends idx 6 (messages[:3] match)
}

# What the CURRENT naive logic would produce (previous same-model request):
NAIVE_SAME_MODEL_PARENT = {
    0: None,  # first opus
    1: None,  # first haiku
    2: 1,  # prev haiku = idx 1 ❌ (unrelated)
    3: 0,  # prev opus = idx 0 ❌ (unrelated)
    4: 2,  # prev haiku = idx 2 ❌ (unrelated)
    5: 4,  # prev haiku = idx 4 ✅ (happens to be right)
    6: 3,  # prev opus = idx 3 ✅ (happens to be right)
    7: 6,  # prev opus = idx 6 ✅
    8: 5,  # prev haiku = idx 5 ❌ (unrelated)
    9: 7,  # prev opus = idx 7 ❌ (should be 6!)
    10: 9,  # prev opus = idx 9 ❌ (should be 6!)
}


def _msg_hash(msg: dict) -> str:
    """Hash a message by role + content for comparison."""
    content = msg.get("content", "")
    if isinstance(content, list):
        content = json.dumps(content, sort_keys=True)
    return hashlib.md5(f"{msg.get('role', '')}:{content}".encode()).hexdigest()[:8]


def _get_msg_hashes(entry: dict) -> list[str]:
    """Get message hashes for an entry."""
    return [_msg_hash(m) for m in entry.get("messages", [])]


def _is_prefix_of(shorter: list[str], longer: list[str]) -> bool:
    """Check if shorter is a prefix of longer."""
    if not shorter or len(longer) < len(shorter):
        return False
    return shorter == longer[: len(shorter)]


def find_diff_parent_by_prefix(entries: list[dict], idx: int) -> int | None:
    """Find the best diff parent for entry at idx using message prefix matching.

    Returns the index of the entry whose messages are the longest prefix
    of entries[idx]'s messages. Returns None if no prefix match found.
    """
    target = entries[idx]
    target_hashes = [_msg_hash(m) for m in target.get("messages", [])]

    best_parent = None
    best_match_len = 0

    for j in range(idx):
        candidate = entries[j]
        candidate_hashes = [_msg_hash(m) for m in candidate.get("messages", [])]

        if not candidate_hashes:
            continue

        # Check if candidate's messages are a prefix of target's messages
        if len(target_hashes) >= len(candidate_hashes):
            if target_hashes[: len(candidate_hashes)] == candidate_hashes:
                if len(candidate_hashes) > best_match_len:
                    best_match_len = len(candidate_hashes)
                    best_parent = j

    return best_parent


def find_next_by_prefix(entries: list[dict], idx: int) -> int | None:
    """Find the next entry whose messages start with entries[idx]'s messages as prefix.

    Mirrors the JS findNextSameModel() — picks the closest (smallest) extension.
    Returns None if no match found.
    """
    current_hashes = _get_msg_hashes(entries[idx])
    if not current_hashes:
        return None

    best_idx = None
    best_len = float("inf")

    for i in range(idx + 1, len(entries)):
        candidate_hashes = _get_msg_hashes(entries[i])
        if _is_prefix_of(current_hashes, candidate_hashes):
            if len(candidate_hashes) < best_len:
                best_len = len(candidate_hashes)
                best_idx = i

    return best_idx


def compute_nav_button_states(entries: list[dict], cur_idx: int) -> tuple[bool, bool]:
    """Compute whether prev/next nav buttons should be enabled for the diff at cur_idx.

    Mirrors the JS updateNavButtons() logic after the bug fix.
    Returns (prev_enabled, next_enabled).
    """
    prev_idx = find_diff_parent_by_prefix(entries, cur_idx)
    if prev_idx is None:
        # No diff can be shown for this entry
        return (False, False)

    # prev button: enabled if prevIdx itself has a diff parent
    prev_of_prev = find_diff_parent_by_prefix(entries, prev_idx)
    prev_enabled = prev_of_prev is not None

    # next button: enabled if there's a next entry that has a valid diff parent
    next_idx = find_next_by_prefix(entries, cur_idx)
    if next_idx is not None:
        next_prev = find_diff_parent_by_prefix(entries, next_idx)
        next_enabled = next_prev is not None
    else:
        next_enabled = False

    return (prev_enabled, next_enabled)


def _response_id(entry: dict) -> str:
    return entry.get("response_id", "")


def _previous_response_id(entry: dict) -> str:
    return entry.get("previous_response_id", "")


def _codex_thread_key(entry: dict) -> str:
    metadata = entry.get("client_metadata") or {}
    turn_metadata = metadata.get("x-codex-turn-metadata") or {}
    if isinstance(turn_metadata, str):
        turn_metadata = json.loads(turn_metadata)
    return f"{turn_metadata.get('session_id', '')}:{turn_metadata.get('thread_id', '')}"


def find_diff_parent_with_response_context(entries: list[dict], idx: int) -> int | None:
    """Mirror viewer parent matching: response id, Codex thread, then prefix."""
    previous_id = _previous_response_id(entries[idx])
    if previous_id:
        for candidate_idx in range(idx - 1, -1, -1):
            if _response_id(entries[candidate_idx]) == previous_id:
                return candidate_idx

    thread_key = _codex_thread_key(entries[idx])
    if thread_key != ":":
        for candidate_idx in range(idx - 1, -1, -1):
            if _codex_thread_key(entries[candidate_idx]) == thread_key:
                return candidate_idx

    return find_diff_parent_by_prefix(entries, idx)


class TestDiffParentMatching:
    """Test that message-prefix-based matching produces correct diff pairs."""

    def test_new_threads_have_no_parent(self):
        """Entries that start a new conversation thread should have no parent."""
        for idx in [0, 1, 2, 3, 4, 8]:
            result = find_diff_parent_by_prefix(TRACE_ENTRIES, idx)
            assert result is None, f"idx {idx} should be a new thread (no parent), got parent={result}"

    def test_continuation_finds_correct_parent(self):
        """Entries that continue a thread should find their correct parent."""
        # idx 6 extends idx 3
        assert find_diff_parent_by_prefix(TRACE_ENTRIES, 6) == 3
        # idx 7 extends idx 6 (longest prefix match)
        assert find_diff_parent_by_prefix(TRACE_ENTRIES, 7) == 6
        # idx 9 extends idx 6 (diverges from idx 7 at msg[3])
        assert find_diff_parent_by_prefix(TRACE_ENTRIES, 9) == 6
        # idx 10 extends idx 6
        assert find_diff_parent_by_prefix(TRACE_ENTRIES, 10) == 6

    def test_same_content_different_system_prompt(self):
        """idx 5 has same user message as idx 4 — prefix match should work."""
        result = find_diff_parent_by_prefix(TRACE_ENTRIES, 5)
        assert result == 4

    def test_prefers_longest_prefix(self):
        """When multiple entries could be parents, pick the longest prefix match."""
        # idx 7's messages[:3] match idx 6, and messages[:1] match idx 3
        # Should pick idx 6 (longer match)
        result = find_diff_parent_by_prefix(TRACE_ENTRIES, 7)
        assert result == 6, "Should prefer idx 6 (3 msgs match) over idx 3 (1 msg match)"

    def test_naive_model_matching_is_wrong(self):
        """Demonstrate that naive same-model matching produces wrong results."""
        wrong_cases = []
        for idx, expected in EXPECTED_DIFF_PARENT.items():
            find_diff_parent_by_prefix(TRACE_ENTRIES, idx)
            naive = NAIVE_SAME_MODEL_PARENT[idx]
            if naive != expected and expected is not None:
                wrong_cases.append(idx)

        # At least 2 cases where naive is wrong (idx 9, 10)
        assert len(wrong_cases) >= 2, f"Expected at least 4 wrong naive matches, got {len(wrong_cases)}: {wrong_cases}"

    def test_all_expected_parents(self):
        """Verify all expected diff parents match."""
        for idx, expected in EXPECTED_DIFF_PARENT.items():
            actual = find_diff_parent_by_prefix(TRACE_ENTRIES, idx)
            assert actual == expected, f"idx {idx}: expected parent={expected}, got {actual}"


class TestEdgeCases:
    """Edge cases for diff matching."""

    def test_empty_messages(self):
        """Entries with empty messages should not match anything."""
        entries = [
            {"model": "opus", "messages": []},
            {"model": "opus", "messages": [{"role": "user", "content": "hi"}]},
        ]
        assert find_diff_parent_by_prefix(entries, 0) is None
        assert find_diff_parent_by_prefix(entries, 1) is None

    def test_single_entry(self):
        """Single entry has no parent."""
        entries = [{"model": "opus", "messages": [{"role": "user", "content": "hi"}]}]
        assert find_diff_parent_by_prefix(entries, 0) is None

    def test_exact_same_messages(self):
        """If two entries have identical messages, the earlier one is parent."""
        entries = [
            {"model": "opus", "messages": [{"role": "user", "content": "hi"}]},
            {"model": "opus", "messages": [{"role": "user", "content": "hi"}]},
        ]
        assert find_diff_parent_by_prefix(entries, 1) == 0

    def test_cross_model_prefix_match(self):
        """Prefix matching should work across different models (same thread, model upgrade)."""
        entries = [
            {"model": "haiku", "messages": [{"role": "user", "content": "hi"}]},
            {
                "model": "opus",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"},
                    {"role": "user", "content": "more"},
                ],
            },
        ]
        assert find_diff_parent_by_prefix(entries, 1) == 0


class TestFindNextByPrefix:
    """Test findNextSameModel logic (find the next entry in the conversation chain)."""

    def test_finds_next_in_chain(self):
        """idx 3 -> idx 6 (next entry whose messages extend idx 3)."""
        assert find_next_by_prefix(TRACE_ENTRIES, 3) == 6

    def test_finds_closest_extension(self):
        """idx 6 has multiple successors (7, 9, 10); pick the closest (smallest msg count)."""
        # idx 7 has 5 msgs, idx 9 has 5 msgs, idx 10 has 7 msgs
        # Both 7 and 9 have 5 msgs — should pick whichever comes first (7)
        result = find_next_by_prefix(TRACE_ENTRIES, 6)
        assert result == 7

    def test_no_next_for_leaf_node(self):
        """Entries at the end of a chain with no successors return None."""
        # idx 10 has 7 messages — no entry after it extends this chain
        assert find_next_by_prefix(TRACE_ENTRIES, 10) is None

    def test_no_next_for_isolated_entry(self):
        """Isolated entries (no successors) return None."""
        # idx 8 is an unrelated haiku subagent
        assert find_next_by_prefix(TRACE_ENTRIES, 8) is None

    def test_no_next_for_empty_messages(self):
        """Entry with empty messages cannot have a next."""
        entries = [
            {"model": "opus", "messages": []},
            {"model": "opus", "messages": [{"role": "user", "content": "hi"}]},
        ]
        assert find_next_by_prefix(entries, 0) is None

    def test_chain_traversal(self):
        """Can traverse a full chain: 3 -> 6 -> 7."""
        assert find_next_by_prefix(TRACE_ENTRIES, 3) == 6
        assert find_next_by_prefix(TRACE_ENTRIES, 6) == 7


class TestResponsesDiffParentMatching:
    """Responses traces can carry stronger parent hints than message prefixes."""

    def test_direct_previous_response_id_wins(self):
        entries = [
            {
                "messages": [{"role": "user", "content": "first"}],
                "response_id": "resp_1",
            },
            {
                "messages": [{"role": "user", "content": "not a prefix"}],
                "previous_response_id": "resp_1",
                "response_id": "resp_2",
            },
        ]

        assert find_diff_parent_with_response_context(entries, 1) == 0

    def test_codex_hidden_prefetch_uses_nearest_visible_same_thread(self):
        metadata = {
            "x-codex-turn-metadata": json.dumps(
                {"session_id": "session-a", "thread_id": "thread-a"}, separators=(",", ":")
            )
        }
        entries = [
            {
                "messages": [{"role": "user", "content": "shared initial prompt"}],
                "client_metadata": metadata,
                "response_id": "resp_1_1",
            },
            {
                "messages": [
                    {"role": "user", "content": "shared initial prompt"},
                    {"role": "assistant", "content": "earlier final text"},
                    {"role": "user", "content": "limit compare"},
                    {"role": "assistant", "content": [{"type": "tool_use", "name": "tool_search"}]},
                    {"role": "tool", "content": [{"type": "tool_result", "content": "github tools"}]},
                ],
                "client_metadata": metadata,
                "response_id": "resp_3_2",
            },
            {
                "messages": [
                    {"role": "user", "content": "shared initial prompt"},
                    {"role": "assistant", "content": "earlier final text"},
                    {"role": "user", "content": "figma domain"},
                    {"role": "tool", "content": [{"type": "tool_result", "content": "older github tools"}]},
                ],
                "client_metadata": metadata,
                "previous_response_id": "resp_hidden_prefetch",
                "response_id": "resp_4_1",
            },
        ]

        assert find_diff_parent_by_prefix(entries, 2) == 0
        assert find_diff_parent_with_response_context(entries, 2) == 1

    def test_codex_thread_fallback_does_not_cross_threads(self):
        entries = [
            {
                "messages": [{"role": "user", "content": "shared"}],
                "client_metadata": {"x-codex-turn-metadata": {"session_id": "session-a", "thread_id": "thread-a"}},
                "response_id": "resp_a",
            },
            {
                "messages": [{"role": "user", "content": "different"}],
                "client_metadata": {"x-codex-turn-metadata": {"session_id": "session-b", "thread_id": "thread-b"}},
                "previous_response_id": "resp_hidden",
                "response_id": "resp_b",
            },
        ]

        assert find_diff_parent_with_response_context(entries, 1) is None


class TestNavButtonStates:
    """Test diff navigation button enabled/disabled states.

    This tests the logic that was buggy: updateNavButtons() compared the object
    returned by findPrevSameModel() directly to a number instead of using .idx,
    causing the right button to always be disabled and the left button to never
    be disabled.
    """

    def test_first_diff_in_chain_has_prev_disabled(self):
        """When viewing turn 3→6 (first diff pair), prev should be disabled."""
        # cur_idx=6, prev_idx=3. prev of 3 is None, so prev button disabled.
        prev_enabled, next_enabled = compute_nav_button_states(TRACE_ENTRIES, 6)
        assert not prev_enabled, "prev should be disabled at start of chain"
        assert next_enabled, "next should be enabled (can go to 6→7)"

    def test_middle_of_chain_prev_enabled(self):
        """When viewing turn 6→7, prev should be enabled (can go back to 3→6)."""
        prev_enabled, next_enabled = compute_nav_button_states(TRACE_ENTRIES, 7)
        assert prev_enabled, "prev should be enabled (can go back to 3→6)"
        # next is disabled because no entry after 7 extends its exact 5-message prefix
        # (idx 10 diverges at msg[4]: "ok summarize" vs "suggestion mode")
        assert not next_enabled

    def test_end_of_chain_has_next_disabled(self):
        """At the last entry in a chain, next should be disabled."""
        prev_enabled, next_enabled = compute_nav_button_states(TRACE_ENTRIES, 10)
        assert prev_enabled, "prev should be enabled (can go back)"
        assert not next_enabled, "next should be disabled at end of chain"

    def test_isolated_entry_with_parent_has_both_nav_correct(self):
        """idx 5 (extends idx 4): no next, prev depends on idx 4 having a parent."""
        prev_enabled, next_enabled = compute_nav_button_states(TRACE_ENTRIES, 5)
        # idx 4 has no parent (new thread), so prev disabled
        assert not prev_enabled, "prev should be disabled (idx 4 has no parent)"
        assert not next_enabled, "next should be disabled (no successor extends idx 5)"

    def test_simple_two_entry_chain(self):
        """Simple chain of 2 entries: prev disabled on first diff, next disabled too."""
        entries = [
            {"model": "opus", "messages": [{"role": "user", "content": "hi"}]},
            {
                "model": "opus",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"},
                    {"role": "user", "content": "more"},
                ],
            },
        ]
        prev_enabled, next_enabled = compute_nav_button_states(entries, 1)
        assert not prev_enabled, "prev disabled (entry 0 has no parent)"
        assert not next_enabled, "next disabled (no entry after 1)"

    def test_three_entry_chain(self):
        """Chain of 3: A→B→C. At B, both enabled; at A→B, prev disabled; at B→C, next disabled."""
        entries = [
            {"model": "opus", "messages": [{"role": "user", "content": "hi"}]},
            {
                "model": "opus",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"},
                ],
            },
            {
                "model": "opus",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "hello"},
                    {"role": "user", "content": "more"},
                ],
            },
        ]
        # At entry 1 (diff 0→1): prev disabled (0 has no parent), next enabled (2 exists)
        p, n = compute_nav_button_states(entries, 1)
        assert not p
        assert n

        # At entry 2 (diff 1→2): prev enabled (1 has parent 0), next disabled (no entry 3)
        p, n = compute_nav_button_states(entries, 2)
        assert p
        assert not n
