from app.sse import format_frame, is_terminal, parse


def test_token_by_event_name():
    e = parse("token", '{"text":"hi"}')
    assert e.type == "token" and e.text == "hi"


def test_tool_inferred_from_type_field():
    e = parse(None, '{"type":"tool","name":"gmail"}')
    assert e.type == "tool" and e.name == "gmail"


def test_status():
    assert parse("status", '{"text":"Searching Web"}').text == "Searching Web"


def test_completed_is_terminal():
    assert is_terminal(parse("completed", "{}"))


def test_error_carries_message():
    e = parse("error", '{"message":"boom"}')
    assert e.type == "error" and e.text == "boom"


def test_cancelled_maps_to_interrupted_and_terminal():
    e = parse("cancelled", "{}")
    assert e.type == "interrupted" and is_terminal(e)


def test_unknown_is_not_terminal():
    e = parse("brand_new", "{}")
    assert e.type == "unknown" and not is_terminal(e)


def test_malformed_json_does_not_raise():
    assert parse("token", "not-json").text == "not-json"


def test_format_frame_roundtrip():
    assert format_frame("tool", '{"name":"x"}') == 'event: tool\ndata: {"name":"x"}\n\n'
    assert format_frame(None, "{}") == "data: {}\n\n"
