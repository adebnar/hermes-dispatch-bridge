import pytest

from app.hub import StreamHub


def _hub() -> StreamHub:
    # __init__ only stores client/publisher (unused by buffer/subscribe paths).
    return StreamHub(client=None, publisher=None)


@pytest.mark.asyncio
async def test_late_subscriber_replays_completed_run():
    hub = _hub()
    hub._buffers["s"] = [("token", '{"text":"hi"}'), ("completed", '{"text":"hi"}')]
    hub._done["s"] = True
    frames = [f async for f in hub.subscribe("s")]
    assert any(f.startswith("event: token") and "hi" in f for f in frames)
    assert any(f.startswith("event: completed") for f in frames)


@pytest.mark.asyncio
async def test_unknown_stream_yields_nothing():
    frames = [f async for f in _hub().subscribe("does-not-exist")]
    assert frames == []


@pytest.mark.asyncio
async def test_live_subscriber_gets_buffered_then_streamed():
    hub = _hub()
    hub._buffers["s"] = [("token", '{"text":"a"}')]  # already buffered
    hub._done["s"] = False

    received: list[str] = []

    async def consume():
        async for f in hub.subscribe("s"):
            received.append(f)

    import asyncio
    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)            # let it replay the buffer + register
    hub._emit("s", ("token", '{"text":"b"}'))  # live frame after subscribe
    hub._done["s"] = True
    hub._broadcast_sentinel("s")
    await asyncio.wait_for(task, timeout=2)

    assert sum('"text":"a"' in f for f in received) == 1   # buffered frame, once
    assert any('"text":"b"' in f for f in received)         # live frame after subscribe
