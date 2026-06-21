from app.push import to_push
from app.sse import SseEvent


def test_tool_event_becomes_using_status():
    p = to_push(SseEvent("tool", name="gmail"), session_id="s1")
    assert p is not None and p.status == "Using gmail" and p.sessionId == "s1"


def test_status_event_passthrough():
    p = to_push(SseEvent("status", text="Searching Web"), session_id=None)
    assert p is not None and p.status == "Searching Web"


def test_completed_sets_done():
    p = to_push(SseEvent("completed"), session_id="s1")
    assert p is not None and p.done is True and p.status == "Complete"


def test_error_is_done():
    p = to_push(SseEvent("error", text="boom"), session_id="s1")
    assert p is not None and p.done is True and "boom" in p.status


def test_token_is_dropped():
    assert to_push(SseEvent("token", text="hello"), session_id="s1") is None


def test_reasoning_is_dropped():
    assert to_push(SseEvent("reasoning", text="thinking"), session_id="s1") is None
