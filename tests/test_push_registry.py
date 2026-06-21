import httpx
import pytest

from app.models import PushMessage
from app.push import Pusher
from app.registry import EndpointRegistry


def test_registry_add_is_idempotent_and_trims():
    r = EndpointRegistry()
    r.add("https://ntfy.sh/abc")
    r.add("  https://ntfy.sh/abc  ")
    r.add("")
    assert r.all() == ["https://ntfy.sh/abc"]
    assert len(r) == 1


def test_registry_remove():
    r = EndpointRegistry()
    r.add("https://ntfy.sh/a")
    r.add("https://ntfy.sh/b")
    r.remove("https://ntfy.sh/a")
    assert r.all() == ["https://ntfy.sh/b"]


def test_pusher_targets_merge_registry_and_static():
    r = EndpointRegistry()
    r.add("https://ntfy.sh/device1")
    p = Pusher(client=None, registry=r, static_endpoint="https://ntfy.sh/topic")
    assert p.targets() == ["https://ntfy.sh/device1", "https://ntfy.sh/topic"]


def test_pusher_targets_dedupe_static_already_registered():
    r = EndpointRegistry()
    r.add("https://ntfy.sh/topic")
    p = Pusher(client=None, registry=r, static_endpoint="https://ntfy.sh/topic")
    assert p.targets() == ["https://ntfy.sh/topic"]


@pytest.mark.asyncio
async def test_pusher_posts_to_every_target():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        r = EndpointRegistry()
        r.add("https://ntfy.sh/d1")
        r.add("https://ntfy.sh/d2")
        pusher = Pusher(client=client, registry=r, static_endpoint=None)
        await pusher.publish(PushMessage(status="Complete", done=True))

    assert sorted(seen) == ["https://ntfy.sh/d1", "https://ntfy.sh/d2"]


@pytest.mark.asyncio
async def test_pusher_swallows_delivery_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        r = EndpointRegistry()
        r.add("https://ntfy.sh/d1")
        pusher = Pusher(client=client, registry=r, static_endpoint=None)
        # Must not raise.
        await pusher.publish(PushMessage(status="x"))
