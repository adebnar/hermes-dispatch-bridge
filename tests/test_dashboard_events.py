from app.dashboard_events import map_event, to_app_frame
from app.push import to_push
from app.sse import is_terminal


def test_message_delta_is_token():
    e = map_event("message.delta", {"text": "po"})
    assert e.type == "token" and e.text == "po"


def test_message_complete_is_terminal():
    assert is_terminal(map_event("message.complete", {"text": "pong"}))


def test_tool_start_maps_name_and_preview():
    e = map_event("tool.start", {"name": "web_search", "context": "cats"})
    assert e.type == "tool" and e.name == "web_search" and e.text == "cats"


def test_reasoning_variants():
    for t in ("reasoning.delta", "thinking.delta", "reasoning.available"):
        assert map_event(t, {"text": "x"}).type == "reasoning"


def test_session_info_and_message_start_are_blank_status():
    assert map_event("session.info", {"model": "m"}).type == "status"
    assert map_event("message.start", None).type == "status"


def test_interrupt_and_error():
    assert is_terminal(map_event("session.interrupt", None))
    e = map_event("agent.error", {"message": "boom"})
    assert e.type == "error" and e.text == "boom"


def test_unknown_event_is_safe():
    assert map_event("brand.new", {}).type == "unknown"


def test_reasoning_and_blank_status_are_dropped_from_push():
    assert to_push(map_event("reasoning.delta", {"text": "x"}), session_id="s") is None
    assert to_push(map_event("session.info", {"model": "m"}), session_id="s") is None
    assert to_push(map_event("tool.complete", {"tool_id": "t"}), session_id="s") is None


def test_token_drops_from_push_but_completed_pushes():
    assert to_push(map_event("message.delta", {"text": "hi"}), session_id="s") is None
    p = to_push(map_event("message.complete", {"text": "done"}), session_id="s")
    assert p is not None and p.done is True


def test_to_app_frame_roundtrips_token():
    name, data = to_app_frame(map_event("message.delta", {"text": "hi"}))
    assert name == "token" and '"text": "hi"' in data
