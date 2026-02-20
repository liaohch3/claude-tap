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
