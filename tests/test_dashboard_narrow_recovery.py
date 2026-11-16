"""Preserve original redaction rules while containing URL parser failures."""

from copy import deepcopy
from json import JSONDecoder, dumps, loads
from urllib.parse import urlencode, urlsplit

import pytest
from aiohttp.test_utils import make_mocked_request

from claude_tap import dashboard
from claude_tap.compact_trace import load_compact_trace, materialize_compact_trace_bundle
from claude_tap.live import LiveViewerServer
from claude_tap.proxy import _build_record
from claude_tap.trace_store import get_trace_store

SECRET = "credentialvalue42"
QUERY_SENTINEL = "query-sentinel-9284"
FRAGMENT_SENTINEL = "fragment-sentinel-7392"
USERINFO_SENTINEL = "userinfo-sentinel-4618"
PATH_SENTINEL = "path-sentinel-1846"
FAILED_LEAF = "https://host：443/path?token=" + SECRET
ORIGINAL_SOURCE = "// Deliverables： only\nconst result = ready ? token === expected : fallback;\n"
AMBIGUOUS_SOURCE = [
    ORIGINAL_SOURCE,
    "// Deliverables： only\n.foo { background: url(/next?token=" + SECRET + "); }",
    "// Deliverables： only\nconst uri = `//host：443/path?token=" + SECRET + "`;",
]


def redact_text(value: str) -> str:
    records = [{"content": value, "neighbor": "keep", "turn": 2}]
    original = deepcopy(records)
    with dashboard.dashboard_redaction_scope():
        result = dashboard.redact_dashboard_records(records)
    assert records == original
    assert result[0]["neighbor"] == "keep"
    assert result[0]["turn"] == 2
    return result[0]["content"]


# These expected values come from the original successful parser path. Some
# preserve inherited recognition limits; URL recovery must not expand them.
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("ordinary public text", "ordinary public text"),
        ("[INFO] build succeeded", "[INFO] build succeeded"),
        ("const obj = {count:1};", "const obj = {count:1};"),
        ("https://h/p?a=1&copy=2", "https://h/p?a=1&copy=2"),
        ("token=fake-secret&%63opy=keep", "token=REDACTED&copy=keep"),
        ("/next ? token = " + SECRET, "/next ?+token+=REDACTED"),
        ("/a b?token =" + SECRET, "/a b?token+=REDACTED"),
        ("//h ?token =" + SECRET, "//h ?token+=REDACTED"),
        ("relative/path ?\ntoken\n=\n" + SECRET, "relative/path ?token=REDACTED"),
        (
            "\x00https://host/path?access_\r\ntoken=" + SECRET + "&public=keep",
            "https://host/path?access_token=REDACTED&public=keep",
        ),
        ("token=" + SECRET + "&public=keep", "token=REDACTED&public=keep"),
        ('{"token":"' + SECRET + '","public":["keep",3]}', '{"token":"REDACTED","public":["keep",3]}'),
        (
            "https://host/path?token=" + SECRET + "&odd%20key=keep",
            "https://host/path?token=REDACTED&odd+key=keep",
        ),
        (
            "<https://host/path?token=" + SECRET + '>; rel="next"',
            "<https://host/path?token=REDACTED",
        ),
        (
            'const uri = "https://host/path?token=' + SECRET + '";',
            'const uri = "https://host/path?token=' + SECRET + '";',
        ),
        (r'const uri = "/next\u003ftoken=' + SECRET + '";', r'const uri = "/next\u003ftoken=' + SECRET + '";'),
        ('[INFO] {"token":"' + SECRET + '"}', '[INFO] {"token":"' + SECRET + '"}'),
        ("payload=api+key%3D" + SECRET, "payload=api+key%3D" + SECRET),
        ("payload=%2574oken%3D" + SECRET, "payload=%2574oken%3D" + SECRET),
        (
            '{"payload":"token=' + SECRET + '","payload":"public"}',
            '{"payload":"token=' + SECRET + '","payload":"public"}',
        ),
    ],
)
def test_successful_normal_parser_outputs_retain_original_contract(value: str, expected: str) -> None:
    assert redact_text(value) == expected
    assert dashboard.redact_dashboard_records([{"content": value}]) == [{"content": expected}]


@pytest.mark.parametrize(
    ("value", "depth", "expected"),
    [
        ("token=" + SECRET, 7, "token=REDACTED"),
        ("token=" + SECRET, 8, "token=" + SECRET),
        ("%74oken=" + SECRET, 0, "%74oken=" + SECRET),
        ("ordinary value", 0, "ordinary value"),
    ],
)
def test_nested_hint_and_depth_gates_retain_original_contract(value: str, depth: int, expected: str) -> None:
    assert dashboard._redact_nested_sensitive_text(value, depth) == expected


def test_sensitive_dictionary_keys_and_nulls_retain_original_contract() -> None:
    original = [{"authorization": "captured-prefix...", "password": None, "public": ["keep", {"api_key": SECRET}]}]
    saved = deepcopy(original)
    assert dashboard.redact_dashboard_records(original) == [
        {"authorization": "REDACTED", "password": None, "public": ["keep", {"api_key": "REDACTED"}]}
    ]
    assert original == saved


@pytest.mark.parametrize("authority", ["https://host：443", "https://[broken", "//host：443"])
@pytest.mark.parametrize(
    ("query", "expected_query"),
    [
        ("token=" + SECRET + "&public=keep", "token=REDACTED&public=keep"),
        ("%74oken=" + SECRET + "&token=&public=&flag", "token=REDACTED&token=REDACTED&public=&flag="),
        ("access_\r\ntoken=" + SECRET + "&public=keep", "access_token=REDACTED&public=keep"),
        (
            urlencode({"payload": "client_secret=" + SECRET + "&public=keep"}),
            urlencode({"payload": "client_secret=REDACTED&public=keep"}),
        ),
        (
            urlencode({"payload": '{"token":"' + SECRET + '","public":["keep",3]}'}),
            urlencode({"payload": '{"token":"REDACTED","public":["keep",3]}'}),
        ),
        (
            urlencode({"redirect_uri": "https://nested.example/path?token=" + SECRET + "&public=keep"}),
            urlencode({"redirect_uri": "https://nested.example/path?token=REDACTED&public=keep"}),
        ),
        ("public=keep&flag", "public=keep&flag"),
    ],
)
def test_failed_authority_masks_leaf_while_normal_parser_keeps_original_rules(
    authority: str, query: str, expected_query: str
) -> None:
    normal = redact_text("https://host/path?" + query + "#fragment")
    failed = redact_text(authority + "/path?" + query + "#fragment")
    assert normal == "https://host/path?" + expected_query + "#fragment"
    assert failed == "REDACTED"
    assert redact_text(failed) == failed


@pytest.mark.parametrize("authority", ["https://host：443", "https://[broken"])
def test_failed_parse_hides_all_components_including_former_placeholder_collisions(authority: str) -> None:
    suffix = "/redaction.invalid?token=" + SECRET + "&public=redaction.invalid#redaction.invalid"
    assert redact_text(authority + suffix) == "REDACTED"


@pytest.mark.parametrize("source", AMBIGUOUS_SOURCE)
def test_failed_parse_of_ambiguous_source_masks_only_its_field(source: str) -> None:
    assert redact_text(source) == "REDACTED"


@pytest.mark.parametrize("delimiter", ["？", "＃", "／", "＠"])
def test_failed_parse_hides_authority_with_new_normalized_component_boundaries(delimiter: str) -> None:
    value = "https://host" + delimiter + "token=" + SECRET + "/path?public=keep"
    assert redact_text(value) == "REDACTED"


@pytest.mark.parametrize(
    "value",
    [
        "https://host：443/path?token=" + QUERY_SENTINEL,
        "https://host：443/path?public=keep#token=" + FRAGMENT_SENTINEL,
        "https://user:" + USERINFO_SENTINEL + "@host：443/path?public=keep",
        "https://host：443/" + PATH_SENTINEL + "?public=keep",
        "https://user:"
        + USERINFO_SENTINEL
        + "@host：443/"
        + PATH_SENTINEL
        + "?token="
        + QUERY_SENTINEL
        + "#password="
        + FRAGMENT_SENTINEL,
        'https://bad：#fragment\nconst u="/next?token=actual-secret";',
        "https://host：443/callback?token=query-secret#access_token=fragment-secret",
        "//TODO：ready? token === expected\nconst keep = 1;",
        r"//TODO：ready? token === expected\nconst keep = 1;",
        "//note：?public=ok#keep;u=/next?api?key=actual-secret;",
        "//note：?token=" + QUERY_SENTINEL + "#password=" + FRAGMENT_SENTINEL,
    ],
)
def test_valueerror_masks_entire_leaf_without_query_fragment_or_authority_survivors(value: str) -> None:
    with pytest.raises(ValueError):
        urlsplit(value)
    with dashboard.dashboard_redaction_scope() as failures:
        assert dashboard._redact_url_query(value) == "REDACTED"
    assert failures == [True]
    assert redact_text(value) == "REDACTED"


@pytest.mark.parametrize("control", ["\r", "\n", "\t", r"\n"])
@pytest.mark.parametrize("component", ["authority", "userinfo", "path", "query_key", "query_value", "fragment"])
def test_valueerror_masks_controls_in_each_uri_component(control: str, component: str) -> None:
    values = {
        "authority": "https://ho" + control + "st：443/path?token=" + QUERY_SENTINEL,
        "userinfo": "https://user" + control + ":" + USERINFO_SENTINEL + "@host：443/path?public=keep",
        "path": "https://host：443/pa" + control + "th-" + PATH_SENTINEL + "?public=keep",
        "query_key": "https://host：443/path?access_" + control + "token=" + QUERY_SENTINEL,
        "query_value": "https://host：443/path?token=" + control + QUERY_SENTINEL,
        "fragment": "https://host：443/path?public=keep#token=" + control + FRAGMENT_SENTINEL,
    }
    value = values[component]
    with pytest.raises(ValueError):
        urlsplit(value)
    assert redact_text(value) == "REDACTED"


@pytest.mark.parametrize("control", ["\r", "\n", "\t"])
def test_valueerror_after_standard_scheme_control_cleanup_masks_the_leaf(control: str) -> None:
    value = "ht" + control + "tps://host：443/path?token=" + QUERY_SENTINEL
    with pytest.raises(ValueError):
        urlsplit(value)
    assert redact_text(value) == "REDACTED"


def test_literal_backslash_n_in_scheme_retains_successful_parser_contract() -> None:
    value = r"ht\ntps://host：443/path?token=" + QUERY_SENTINEL
    assert not urlsplit(value).netloc
    assert redact_text(value) == r"ht\ntps://host：443/path?token=REDACTED"


def wrap_public_container(kind: str, value: str) -> str:
    if kind == "json":
        return dumps({"payload": value, "public": ["keep", 3]}, ensure_ascii=False, separators=(",", ":"))
    form = urlencode({"payload": value, "public": "keep", "flag": ""})
    return "https://outer.example/path?" + form + "#outer" if kind == "uri" else form


@pytest.mark.parametrize(
    "containers",
    [
        ("form",),
        ("json",),
        ("uri",),
        ("form", "json"),
        ("uri", "form"),
        ("json", "uri"),
        ("uri", "form", "json"),
        ("form", "uri", "form"),
    ],
)
def test_nested_failed_leaf_returns_to_original_outer_encoding_and_preserves_siblings(containers: tuple[str]) -> None:
    value = FAILED_LEAF
    expected = "REDACTED"
    for kind in containers:
        value = wrap_public_container(kind, value)
        expected = wrap_public_container(kind, expected)
    assert redact_text(value) == expected
    assert expected != "REDACTED"
    assert redact_text(expected) == expected


@pytest.mark.parametrize(
    ("layers", "depth", "reaches_leaf"),
    [(1, 0, True), (1, 7, True), (1, 8, False), (2, 6, True), (2, 7, False), (3, 5, True), (3, 6, False)],
)
def test_nested_form_depth_gate_still_controls_whether_the_failed_leaf_is_parsed(
    layers: int, depth: int, reaches_leaf: bool
) -> None:
    value = FAILED_LEAF
    expected = "REDACTED"
    for _ in range(layers):
        value = wrap_public_container("form", value)
        expected = wrap_public_container("form", expected)
    with dashboard.dashboard_redaction_scope() as failures:
        assert dashboard._redact_sensitive_text(value, depth) == (expected if reaches_leaf else value)
    assert bool(failures) is reaches_leaf


@pytest.mark.parametrize("query", ["public=keep", "%74oken=" + SECRET])
def test_original_nested_hint_gate_can_skip_a_failed_uri_without_changing_its_policy(query: str) -> None:
    value = "https://host：443/path?" + query
    assert dashboard._redact_nested_sensitive_text(value, 0) == value
    form = urlencode({"payload": value, "public": "keep"})
    assert redact_text(form) == form


def test_failed_url_result_cannot_fall_through_to_form_or_raw_text(monkeypatch) -> None:
    def unexpected_form(*args, **kwargs):
        pytest.fail("A failed URL parse must finish at the current string leaf")

    monkeypatch.setattr(dashboard, "_redact_form_text", unexpected_form)
    with dashboard.dashboard_redaction_scope() as failures:
        assert dashboard._redact_sensitive_text(FAILED_LEAF) == "REDACTED"
    assert failures == [True]


@pytest.mark.parametrize(
    "error",
    [ValueError("query helper failure"), UnicodeEncodeError("utf-8", "\ud800", 0, 1, "injected query failure")],
)
def test_query_helper_errors_after_successful_url_parse_are_not_swallowed(monkeypatch, error: Exception) -> None:
    def fail_query(*args, **kwargs):
        raise error

    monkeypatch.setattr(dashboard, "_redact_query_string", fail_query)
    with pytest.raises(type(error)) as raised:
        dashboard._redact_sensitive_text("https://host/path?token=" + SECRET)
    assert raised.value is error
    with dashboard.dashboard_redaction_scope() as failures:
        with pytest.raises(type(error)) as raised:
            dashboard._redact_sensitive_text("https://host/path?token=" + SECRET)
    assert raised.value is error
    assert failures == []


def test_original_no_question_gate_does_not_parse_or_hide_an_unvisited_uri(monkeypatch) -> None:
    def unexpected_split(*args, **kwargs):
        pytest.fail("The original no-question gate must run before URL parsing")

    value = "https://host：443/path#token=" + SECRET
    monkeypatch.setattr(dashboard, "urlsplit", unexpected_split)
    assert dashboard._redact_url_query(value) is None
    assert dashboard._redact_sensitive_text(value) == value
    with dashboard.dashboard_redaction_scope() as failures:
        assert dashboard._redact_url_query(value) is None
        assert dashboard._redact_sensitive_text(value) == value
    assert failures == []


@pytest.mark.parametrize("serialized", [False, True])
def test_dict_list_and_json_keep_containers_and_siblings_around_failed_string_leaves(serialized: bool) -> None:
    value = {"payload": FAILED_LEAF, "public": ["keep", 3, None, {"nested": FAILED_LEAF, "other": "safe"}]}
    expected = {"payload": "REDACTED", "public": ["keep", 3, None, {"nested": "REDACTED", "other": "safe"}]}
    saved = deepcopy(value)
    if serialized:
        original_json = dumps(value, ensure_ascii=False, separators=(",", ":"))
        assert redact_text(original_json) == dumps(expected, ensure_ascii=False, separators=(",", ":"))
    else:
        with dashboard.dashboard_redaction_scope():
            assert dashboard.redact_dashboard_records([value]) == [expected]
    assert value == saved


@pytest.mark.parametrize("value", [FAILED_LEAF, wrap_public_container("uri", FAILED_LEAF)])
def test_summary_redacts_only_the_failed_string_leaf_and_preserves_public_siblings(value: str) -> None:
    summary = {"first_user": value, "label": "public sibling", "record_count": 4}
    saved = deepcopy(summary)
    expected = "REDACTED" if value == FAILED_LEAF else wrap_public_container("uri", "REDACTED")
    with dashboard.dashboard_redaction_scope():
        assert dashboard.redact_dashboard_summary(summary) == {
            "first_user": expected,
            "label": "public sibling",
            "record_count": 4,
        }
    assert summary == saved


@pytest.mark.parametrize("value", [FAILED_LEAF, ORIGINAL_SOURCE])
def test_failed_url_recovery_is_limited_to_explicit_display_scope(value: str) -> None:
    records = [{"content": value, "neighbor": "keep"}]
    saved = deepcopy(records)
    with pytest.raises(ValueError):
        dashboard.redact_dashboard_records(records)
    with dashboard.dashboard_redaction_scope() as failures:
        assert dashboard.redact_dashboard_records(records) == [{"content": "REDACTED", "neighbor": "keep"}]
    assert failures == [True]
    with pytest.raises(ValueError):
        dashboard.redact_dashboard_records(records)
    assert records == saved


def test_display_scope_is_reset_when_its_caller_raises() -> None:
    sentinel = RuntimeError("caller failure")
    with pytest.raises(RuntimeError) as raised:
        with dashboard.dashboard_redaction_scope() as failures:
            assert dashboard._redact_sensitive_text(FAILED_LEAF) == "REDACTED"
            raise sentinel
    assert raised.value is sentinel
    assert failures == [True]
    with pytest.raises(ValueError):
        dashboard._redact_sensitive_text(FAILED_LEAF)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (ORIGINAL_SOURCE, "REDACTED"),
        (AMBIGUOUS_SOURCE[1], "REDACTED"),
        (
            "https://host：443/path?token=" + SECRET + "&public=keep",
            "REDACTED",
        ),
        (
            "https://outer.example/path?" + urlencode({"payload": FAILED_LEAF, "public": "keep"}),
            "https://outer.example/path?" + urlencode({"payload": "REDACTED", "public": "keep"}),
        ),
    ],
)
@pytest.mark.asyncio
async def test_display_recovery_preserves_captured_auth_and_raw_downloads(trace_db, text: str, expected: str) -> None:
    auth_tail = "capture-auth-hidden-tail-9284"
    original_authorization = "Bearer abcdefghij-" + auth_tail
    captured = _build_record(
        "req_narrow_recovery",
        1,
        10,
        "POST",
        "/v1/messages",
        {"Authorization": original_authorization, "Cookie": "session=" + auth_tail, "X-Public": "keep"},
        {
            "system": text,
            "public": "keep",
            "messages": [{"role": "user", "content": text}, {"role": "user", "content": "public sibling"}],
        },
        200,
        {"Set-Cookie": "session=" + auth_tail},
        {"content": [{"type": "text", "text": "Checked."}]},
    )
    assert captured["request"]["headers"]["Authorization"] == "***"
    assert captured["request"]["headers"]["Cookie"] == "***"
    assert captured["response"]["headers"]["Set-Cookie"] == "***"
    assert auth_tail not in dumps(captured)
    saved = deepcopy(captured)
    store = get_trace_store()
    session_id = store.create_session(client="claude", proxy_mode="reverse")
    seed = deepcopy(captured)
    seed["request"]["body"]["system"] = "public seed"
    seed["request"]["body"]["messages"][0]["content"] = "public seed"
    store.append_record(session_id, seed)
    connection = store._connect()
    connection.execute("UPDATE records SET payload_json=? WHERE session_id=?", (dumps(captured), session_id))
    connection.commit()
    raw_payload = connection.execute("SELECT payload_json FROM records WHERE session_id=?", (session_id,)).fetchone()[0]
    cached_summary = connection.execute("SELECT summary_json FROM sessions WHERE id=?", (session_id,)).fetchone()[0]
    raw_jsonl = store.export_jsonl(session_id)
    raw_compact = store.export_compact(session_id)
    server = LiveViewerServer(host="127.0.0.1", port=0)

    def request(suffix: str):
        return make_mocked_request("GET", "/api/sessions/" + session_id + suffix, match_info={"session_id": session_id})

    records_response = await server._handle_session_records(request("/records"))
    assert records_response.status == 200
    records_payload = loads(records_response.text)
    displayed = records_payload["records"][0]
    assert records_payload["session"]["first_user"] == "REDACTED"
    assert records_payload["session"]["last_response"] == "REDACTED"
    assert records_payload["session"]["error"] == ""
    assert displayed["request"]["body"]["system"] == expected
    assert displayed["request"]["body"]["messages"][0]["content"] == expected
    assert displayed["request"]["body"]["messages"][1]["content"] == "public sibling"
    assert displayed["request"]["body"]["public"] == "keep"
    assert displayed["request"]["headers"]["Authorization"] == "REDACTED"
    assert displayed["request"]["headers"]["Cookie"] == "REDACTED"
    assert displayed["request"]["headers"]["X-Public"] == "keep"
    html_response = await server._handle_session_html_compat(request("/html"))
    assert html_response.status == 200
    assert SECRET not in html_response.text
    assert auth_tail not in html_response.text
    assert "REDACTED" in html_response.text
    metadata_json = html_response.text.partition("const EMBEDDED_TRACE_META = ")[2]
    metadata, _ = JSONDecoder().raw_decode(metadata_json)
    assert len(metadata) == 1
    assert metadata[0]["sys_hint"] == "REDACTED"
    assert metadata[0]["session_user_text"] == "REDACTED"
    assert metadata[0]["error_message"] == ""
    assert metadata[0]["path"] == "/v1/messages"
    assert metadata[0]["message_count"] == 2

    jsonl_response = await server._handle_export_jsonl(request("/export/jsonl"))
    assert jsonl_response.status == 200
    assert jsonl_response.text == raw_jsonl
    assert loads(jsonl_response.text) == saved
    compact_response = await server._handle_export_compact(request("/export/compact"))
    assert compact_response.status == 200
    assert compact_response.text == raw_compact
    assert load_compact_trace(compact_response.text) == [saved]
    download_response = await server._handle_export_html(request("/export/html"))
    assert download_response.status == 200
    embedded = download_response.text.partition("const EMBEDDED_TRACE_COMPACT_DATA = ")[2]
    bundle, _ = JSONDecoder().raw_decode(embedded)
    assert materialize_compact_trace_bundle(bundle) == [saved]
    assert auth_tail not in download_response.text
    assert captured == saved
    assert store.load_records(session_id) == [saved]
    assert (
        connection.execute("SELECT payload_json FROM records WHERE session_id=?", (session_id,)).fetchone()[0]
        == raw_payload
    )
    assert (
        connection.execute("SELECT summary_json FROM sessions WHERE id=?", (session_id,)).fetchone()[0]
        == cached_summary
    )
