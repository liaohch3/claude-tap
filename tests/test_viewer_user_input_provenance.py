"""Provenance classification and session titling, on the Python side.

The viewer decides these twice: `sidebar.js` runs in the browser for small
captures, `viewer.py` precomputes them for lazy metadata above LAZY_THRESHOLD.
The two must agree, or a group's title and badge change as a capture grows past
the threshold. `tests/test_viewer_js_units.py` covers the same cases against the
JS; the assertions here are deliberately the mirror image of those.
"""

from __future__ import annotations

from claude_tap.viewer import (
    _block_input_text,
    _classify_user_input_origin,
    _clean_session_user_text,
    _eligible_user_text_blocks,
    _preferred_user_text_for_message,
    _session_user_text,
    _session_user_title,
)

# Openers a harness wraps a user-role message in. Not prose the user typed, so
# they must neither title a session nor read as human in the detail pane.
INJECTED_OPENERS = [
    "<system-reminder>\nBudget remaining: 40%\n</system-reminder>",
    "<environment_context>\ncwd: /tmp\n</environment_context>",
    "<session_context>\nid: abc\n</session_context>",
    "<local-command-caveat>\nOutput below\n</local-command-caveat>",
    "<additional_metadata>\nrepo: claude-tap\n</additional_metadata>",
    "# AGENTS.md instructions\n\nRun ruff before committing.",
    "# Files mentioned by the user:\n\n- viewer.py",
]


def _user(*blocks: dict | str) -> dict:
    return {"role": "user", "content": list(blocks)}


def _blocks(content: object) -> list[str]:
    return _eligible_user_text_blocks(content)


def _text(value: str) -> dict:
    return {"type": "text", "text": value}


def test_injected_openers_classify_as_harness_and_clean_to_nothing() -> None:
    for opener in INJECTED_OPENERS:
        assert _classify_user_input_origin(opener) == "harness", opener
        assert _clean_session_user_text(opener) == "", opener


def test_wrapper_tags_match_whole_tags_not_prefixes() -> None:
    """`<skillsets>` starts with `<skills` but is not the injected `<skills>` tag."""
    assert _classify_user_input_origin("<skillsets> are what I need here") == "human"


def test_injection_beside_a_tool_result_is_still_seen() -> None:
    """Tool output comes first on the wire, so joined text read as human prose."""
    message = _user(
        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"},
        _text("[SUGGESTION MODE: Suggest what the user might naturally type next.]"),
    )
    text, origin = _preferred_user_text_for_message(message)
    assert origin == "harness"
    # Blank on purpose: the badge is kept, the title is left to an older turn.
    assert text == ""


def test_bom_prefixed_payload_is_still_classified_as_payload() -> None:
    """JS trim() drops U+FEFF; the Python cleaner must do the same."""
    raw = "\ufeffimport os\n"
    assert _clean_session_user_text(raw) == "import os"
    text, origin = _preferred_user_text_for_message(_user(_text(raw)))
    assert origin == "payload"
    assert text == "import os"
    padded = "  \ufeffimport os\n"
    assert _clean_session_user_text(padded) == "import os"


def test_pasted_payload_followed_by_prose_is_titled_by_the_prose() -> None:
    message = _user(
        _text("import os\nimport sys\n\ndef main():\n    return 0"),
        _text("Does this look right?"),
    )
    assert _preferred_user_text_for_message(message) == ("Does this look right?", "human")


def test_whitespace_only_blocks_do_not_become_the_title() -> None:
    message = _user(_text("  \n  "), _text("Ship it."))
    assert _preferred_user_text_for_message(message) == ("Ship it.", "human")


def test_a_bom_only_block_is_skipped_like_javascript_skips_it() -> None:
    """A leading BOM-only block must not install the fallback origin.

    ``str.strip()`` leaves U+FEFF truthy where JS ``String.trim()`` removes it, so
    this block used to survive as an empty ``human`` fallback that the pasted diff
    behind it then inherited -- payload in the browser, human in lazy metadata.
    """
    message = _user(_text("﻿"), _text("diff --git a/a b/a\n+line"))
    assert _eligible_user_text_blocks(message["content"]) == ["diff --git a/a b/a\n+line"]
    assert _preferred_user_text_for_message(message) == ("diff --git a/a b/a\n+line", "payload")


def test_a_bom_only_text_field_still_yields_to_output() -> None:
    """A BOM in ``text`` must not hide the ``output`` beside it.

    Responses blocks put the readable text under ``output`` when ``text`` is blank.
    ``str.strip()`` leaves U+FEFF truthy, so this block returned the BOM,
    ``_eligible_user_text_blocks`` then dropped it, and the injection lost its title
    and badge above ``LAZY_THRESHOLD`` -- JavaScript reads ``output`` here.
    """
    block = {"type": "input_text", "text": "﻿", "output": "Perform a web search for the query: pricing"}
    assert _block_input_text(block) == "Perform a web search for the query: pricing"
    assert _preferred_user_text_for_message(_user(block)) == (
        "Perform a web search for the query: pricing",
        "harness",
    )


def test_a_message_with_no_eligible_blocks_yields_no_title() -> None:
    assert _preferred_user_text_for_message({"role": "user", "content": []}) == ("", "human")


def test_session_title_comes_from_the_newest_human_turn() -> None:
    """A cumulative request carries its history; the newest prompt is the query."""
    messages = [
        _user(_text("Human turn A")),
        {"role": "assistant", "content": [_text("...")]},
        _user(_text("Human turn B")),
    ]
    assert _session_user_text(messages) == "Human turn B"


def test_injected_newest_turn_does_not_take_the_session_title() -> None:
    """An injected-only follow-up belongs to the query it follows, not its own group."""
    messages = [
        _user(_text("Split the pull request into two.")),
        {"role": "assistant", "content": [_text("...")]},
        _user(_text("[SUGGESTION MODE: Suggest what the user might naturally type next.]")),
    ]
    assert _session_user_text(messages) == "Split the pull request into two."


def test_eligible_blocks_tolerate_every_content_shape_captures_arrive_in() -> None:
    """Clients disagree about how a user message is shaped, hence this helper."""
    assert _blocks(None) == []
    assert _blocks("plain string") == ["plain string"]
    assert _blocks(42) == []

    # A bare block instead of a list.
    assert _blocks(_text("solo block")) == ["solo block"]
    assert _blocks({"type": "tool_result", "content": "output"}) == []
    assert _blocks({"type": "function_call_output", "output": "output"}) == []
    assert _blocks({"type": "message", "content": [_text("nested")]}) == ["nested"]
    assert _blocks({"type": "text"}) == []

    # Lists mixing strings, blocks, junk, and Responses-style nesting.
    assert _blocks(["loose", _text("keep"), 7, None]) == ["loose", "keep"]
    assert _blocks([{"type": "message", "content": [_text("a"), _text("b")]}]) == ["a", "b"]
    assert _blocks([{"type": "input_text", "output": "from output key"}]) == ["from output key"]

    # Blank blocks would title a group with an empty string.
    assert _blocks([_text("   "), _text("real")]) == ["real"]


def test_session_wrapped_prose_survives_cleaning() -> None:
    """A `<session>` wrapper holds the prompt, unlike the injected wrappers."""
    assert _clean_session_user_text("<session>[Image #1] what does this show?</session>") == "what does this show?"
    assert _clean_session_user_text("<session>[Image #1]</session>") == "[Image #1]"


def test_blank_and_unrecognized_text_reads_as_human() -> None:
    assert _classify_user_input_origin("") == "human"
    assert _classify_user_input_origin("Just a normal question.") == "human"


def test_an_import_is_payload_only_when_the_statement_ends_the_line() -> None:
    """`import pandas and plot it` is someone talking, not a pasted module."""
    for pasted in (
        "import json\nimport sys\n",
        "import os.path as p\n",
        "import json, sys\n",
        "from collections import defaultdict\n",
        "from typing import (\n    Any,\n)",
        "from claude_tap.viewer import *\n",
        "from __future__ import annotations\n\nimport json",
    ):
        assert _classify_user_input_origin(pasted) == "payload", pasted

    for prose in (
        "import pandas and plot the data",
        "from the import list, drop numpy",
        "import the trace into the viewer for me",
    ):
        assert _classify_user_input_origin(prose) == "human", prose


def test_cleaner_discarded_forms_are_classified_as_injected() -> None:
    """Cleaning and provenance read the same list, so they cannot disagree.

    A form the cleaner blanks but the classifier calls prose renders as an empty
    human turn: no title, and no badge to say where the text went.
    """
    for discarded in (
        "Web page content:\n\nLorem ipsum from a fetched page.",
        "Page content: the rest of a scraped article",
        "网页内容：抓取到的正文",
        "[SUGGESTION MODE: Suggest what the user might type next.]",
        "[Image: source: /tmp/shot.png]",
        "[Image: original 2880x1800, displayed at 2000x1250.]",
        "<image_input>",
    ):
        assert _clean_session_user_text(discarded) == "", discarded
        assert _classify_user_input_origin(discarded) == "harness", discarded


def test_non_ascii_identifiers_are_payload_on_both_sides() -> None:
    """`def 处理():` is a declaration, and the JS mirror has to agree.

    Python's ``\\w`` is Unicode-aware while JavaScript's is ASCII-only, so the
    patterns spell the class out on both sides. Left to the escapes, the same
    paste reads as prose in the browser and as payload here, changing its badge,
    its title and its grouping as a capture crosses LAZY_THRESHOLD.
    """
    for pasted in (
        "def 处理():\n    pass",
        "function 计算(x) {\n  return x;\n}",
        "const λ = 1",
        "let Ünïcode = {",
        "import 模块\n",
        "from 包 import 东西\n",
        "import 包.子模块 as 别名\n",
    ):
        assert _classify_user_input_origin(pasted) == "payload", pasted

    for prose in ("def 处理 should be renamed?", "把 import 模块 改成绝对导入"):
        assert _classify_user_input_origin(prose) == "human", prose


def test_digit_patterns_stay_ascii_on_both_sides() -> None:
    """The same trap in reverse: this ``\\d`` matches Arabic-Indic digits, JS's does not."""
    assert _classify_user_input_origin("@@ -12,3 +12,4 @@\n ctx") == "payload"
    assert _classify_user_input_origin("   1\tfirst line") == "payload"
    assert _classify_user_input_origin("@@ -١٢ لا يوجد") == "human"
    assert _classify_user_input_origin("  ١\tArabic-Indic digit prose") == "human"


def test_a_badge_only_first_block_does_not_lock_in_an_empty_title() -> None:
    """Otherwise the turn renders untitled and merges into the group before it."""
    message = _user(
        _text("<system-reminder>\nBackground.\n</system-reminder>"),
        _text("diff --git a/x b/x\n+line"),
    )
    # The first block still owns the provenance; only the title comes from later.
    assert _preferred_user_text_for_message(message) == ("diff --git a/x b/x\n+line", "harness")

    both_blank = _user(_text("<system-reminder>\nOne.\n</system-reminder>"), _text("<image_input>"))
    assert _preferred_user_text_for_message(both_blank) == ("", "harness")

    first_wins = _user(_text("diff --git a/a b/a\n+one"), _text("diff --git a/b b/b\n+two"))
    assert _preferred_user_text_for_message(first_wins) == ("diff --git a/a b/a\n+one", "payload")


def test_lazy_metadata_carries_the_chosen_origin_not_just_the_title() -> None:
    """The title alone cannot reproduce the decision, so the origin travels with it.

    A blanked harness block followed by a pasted diff is deliberately kept as
    harness while taking its title from the diff. Classifying that title again in
    the browser says payload, so without this the same group changes its badge as
    a capture crosses LAZY_THRESHOLD.
    """
    messages = [
        _user(
            _text("<system-reminder>\nBackground.\n</system-reminder>"),
            _text("diff --git a/x b/x\n+line"),
        )
    ]
    text, origin = _session_user_title(messages)
    assert (text, origin) == ("diff --git a/x b/x\n+line", "harness")
    # What the browser would infer from the serialized title on its own.
    assert _classify_user_input_origin(text) == "payload"


def test_session_title_origin_defaults_to_human() -> None:
    """The common case stays absent from metadata, so the default has to agree."""
    assert _session_user_title([_user(_text("Human turn"))]) == ("Human turn", "human")
    assert _session_user_title([]) == ("", "human")


def test_json_prompt_payloads_unwrap_before_classification() -> None:
    """The browser cleaner extracts {"prompt":"..."} before classifying.

    Leaving the JSON intact here titles a lazy-metadata group with the raw
    object and badges it as human, while the same capture below LAZY_THRESHOLD
    shows the extracted prompt as harness input.
    """
    websearch = "Perform a web search for the query: token pricing"
    for raw in (
        '{"prompt":"Perform a web search for the query: token pricing"}',
        '[{"prompt":"Perform a web search for the query: token pricing"}]',
        '{"title":"Perform a web search for the query: token pricing"}',
    ):
        assert _clean_session_user_text(raw) == websearch, raw
        text, origin = _preferred_user_text_for_message(_user(_text(raw)))
        assert text == websearch, raw
        assert origin == "harness", raw


def test_image_original_metadata_does_not_title_a_session() -> None:
    """The classifier already treats the original form as an attachment.

    Without blanking it, an attachment-only newest turn starts its own group
    titled with image dimensions instead of staying under the human query.
    """
    messages = [
        _user(_text("What does this screenshot show?")),
        {"role": "assistant", "content": [_text("...")]},
        _user(_text("[Image: original 2880x1800, displayed at 2000x1250.]")),
    ]
    assert _session_user_text(messages) == "What does this screenshot show?"


def test_tool_result_only_turns_never_title_a_session() -> None:
    messages = [
        _user(_text("Run the tests.")),
        {"role": "assistant", "content": [_text("...")]},
        _user({"type": "tool_result", "tool_use_id": "toolu_1", "content": "1102 passed"}),
    ]
    assert _session_user_text(messages) == "Run the tests."


def test_an_empty_text_field_yields_to_output() -> None:
    """A block can leave `text` empty and carry the readable text in `output`.

    Reading `text` because it merely is a string kept the empty one, so this
    message titled itself with the raw JSON and badged it human above
    LAZY_THRESHOLD while the browser showed the extracted harness text below it.
    """
    websearch = "Perform a web search for the query: pricing"
    block = {"type": "input_text", "text": "", "output": websearch}
    assert _blocks([block]) == [websearch]
    assert _blocks(block) == [websearch]
    assert _preferred_user_text_for_message(_user(block)) == (websearch, "harness")
    # A non-empty `text` still wins; `output` is the fallback, not an override.
    assert _blocks([{"type": "input_text", "text": "what I typed", "output": "ignored"}]) == ["what I typed"]


def test_a_base_less_class_declaration_is_payload() -> None:
    """`class Foo:` has no parenthesis, so the suffix set has to accept the colon.

    Reading it as human let a pasted class body win the title over the question
    the same message went on to ask.
    """
    pasted = "class Foo:\n    def run(self):\n        return 1\n"
    assert _classify_user_input_origin(pasted) == "payload"
    text, origin = _preferred_user_text_for_message(_user(_text(pasted), _text("Why is this slow?")))
    assert (text, origin) == ("Why is this slow?", "human")


def test_an_async_declaration_is_payload_like_its_sync_form() -> None:
    """`async` has to be a prefix, not one spelled-out alternative per keyword.

    Listing only `async function` left a pasted coroutine reading as prose, so it
    won the title over the question beside it while `def fetch():` did not.
    """
    for pasted in (
        "async def fetch_data():\n    return await client.get(url)\n",
        "async function fetchData() {\n  return fetch(url);\n}\n",
    ):
        assert _classify_user_input_origin(pasted) == "payload", pasted
        assert _classify_user_input_origin(pasted.replace("async ", "", 1)) == "payload", pasted

    text, origin = _preferred_user_text_for_message(
        _user(_text("async def fetch_data():\n    return 1\n"), _text("Why does this hang?"))
    )
    assert (text, origin) == ("Why does this hang?", "human")


def test_prose_with_a_colon_after_a_keyword_is_not_a_declaration() -> None:
    """A bare colon after keyword-plus-word matches English as readily as code.

    Accepting `:` into the general suffix set alongside `(`, `{` and `=` badged plain
    questions as pasted code, which cost them their title and merged the turn into
    the group above. The colon forms are spelled out separately instead, each with
    the syntax a declaration carries and prose does not.
    """
    for typed in (
        "class action: can I join the settlement?",
        "function calls: why are they slow?",
        "def parse: what is this?",
        "let me know: does the retry back off?",
        "const rate: is that per request or per minute?",
    ):
        assert _classify_user_input_origin(typed) == "human", typed
        assert _preferred_user_text_for_message(_user(_text(typed))) == (typed, "human")

    # The declarations those forms exist for stay payload.
    for pasted in (
        "class Foo:\n    pass\n",
        "class Foo(Base):\n    pass\n",
        'const value: string = "a";\n',
        "let count: number;\n",
    ):
        assert _classify_user_input_origin(pasted) == "payload", pasted


def test_a_json_prompt_array_prefers_the_human_item() -> None:
    """Array unwrapping is human-first, like the rule across separate blocks.

    Returning the first readable item let a leading injection title and badge the
    whole message while the question after it went unread, so the turn was filed
    under the harness and gave the sidebar nothing the user had typed.
    """
    websearch = "Perform a web search for the query: pricing"
    both = f'[{{"prompt":"{websearch}"}},{{"prompt":"What does that cost?"}}]'
    assert _clean_session_user_text(both) == "What does that cost?"
    assert _preferred_user_text_for_message(_user(_text(both))) == ("What does that cost?", "human")
    # Order does not matter, and an array with no human item still reads harness.
    reversed_order = f'[{{"prompt":"What does that cost?"}},{{"prompt":"{websearch}"}}]'
    assert _clean_session_user_text(reversed_order) == "What does that cost?"
    assert _clean_session_user_text(f'[{{"prompt":"{websearch}"}}]') == websearch
    assert _classify_user_input_origin(_clean_session_user_text(f'[{{"prompt":"{websearch}"}}]')) == "harness"
    # An item that cleans to nothing is skipped rather than ending the search.
    assert _clean_session_user_text('[{"prompt":""},{"prompt":"Only one speaks"}]') == "Only one speaks"


def test_ordinary_prose_beginning_with_analyze_reads_as_human() -> None:
    """A harness opener has to be unmistakable template text, not a plain English stem.

    `^Analyze if this message indicates` matched a real subagent template, but it
    also matches someone asking a normal question, and the pattern claimed the
    whole sentence. That turn was badged harness-injected and yielded no group
    title, so it merged into the query before it -- the user's own words relabelled
    as something the harness wrote.
    """
    typed = "Analyze if this message indicates fraud or a billing mistake"
    assert _classify_user_input_origin(typed) == "human"
    assert _preferred_user_text_for_message(_user(_text(typed))) == (typed, "human")
    # The openers that are unmistakable stay recognized.
    assert _classify_user_input_origin("CRITICAL: Respond with TEXT ONLY, no tool calls") == "harness"
    assert _classify_user_input_origin("Briefly inform the user about the task result") == "harness"


def test_command_wrapper_tags_need_a_tag_boundary() -> None:
    """A longer tag that starts the same way is the user's own, not the harness's."""
    assert _classify_user_input_origin("<local-command-caveats> are my own notes") == "human"
    assert _classify_user_input_origin("<command-nameplate>Deploy</command-nameplate>") == "human"
    assert _classify_user_input_origin("<local-command-caveat>\nOutput below\n") == "harness"
    assert _classify_user_input_origin('<command-name status="ok">/cost</command-name>') == "harness"
