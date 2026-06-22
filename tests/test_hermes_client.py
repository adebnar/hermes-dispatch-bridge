"""Unit tests for HermesClient request shaping (no live dashboard)."""

from app.hermes_client import HermesClient


class FakeResp:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"id": "c1"}


class FakeHttp:
    def __init__(self):
        self.calls = []

    async def post(self, url, headers=None, json=None):
        self.calls.append((url, json))
        return FakeResp()

    async def patch(self, url, headers=None, json=None):
        self.calls.append((url, json))
        return FakeResp()


async def test_create_cron_passes_profile_as_query_param():
    http = FakeHttp()
    client = HermesClient("http://x", "tok", http)  # token set → no HTML fetch
    await client.create_cron("n", "p", "every 10m", profile="personal")
    url, body = http.calls[0]
    # Profile must be on the URL (dashboard ignores a body profile).
    assert "?profile=personal" in url
    assert "profile" not in body
    assert body == {"name": "n", "prompt": "p", "schedule": "every 10m"}


async def test_set_session_archived_patches_with_profile():
    http = FakeHttp()
    client = HermesClient("http://x", "tok", http)
    await client.set_session_archived("s1", True, profile="personal")
    url, body = http.calls[0]
    assert url.endswith("/api/sessions/s1")
    assert body == {"archived": True, "profile": "personal"}
