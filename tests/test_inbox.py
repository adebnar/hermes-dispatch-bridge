import asyncio
import base64

from fastapi.testclient import TestClient

from app import crypto
from app.auth import require_auth
from app.inbox import InboxService, InboxWatcher
from app.main import app, get_inbox, get_watcher
from app.models import PushMessage
from app.push import Pusher
from app.registry import EndpointRegistry
from app.store import StateStore

SAMPLE = """# Cron Job: Daily digest

**Job ID:** abc123
**Run Time:** 2026-06-22 09:00:25
**Schedule:** 0 9 * * *

---

Top priority today: ship the inbox feature. Second: review PRs.
"""

FAILED = """# Cron Job: Email triage (FAILED)

**Job ID:** def456
**Run Time:** 2026-06-21 20:01:22

## Error

```
ValueError: model too small
```
"""


def _seed(root):
    # default profile: one ok deliverable
    d = root / "cron" / "output" / "abc123"
    d.mkdir(parents=True)
    (d / "2026-06-22_09-00-25.md").write_text(SAMPLE)
    # personal profile: one failed deliverable
    p = root / "profiles" / "personal" / "cron" / "output" / "def456"
    p.mkdir(parents=True)
    (p / "2026-06-21_20-01-22.md").write_text(FAILED)


def teardown_function():
    app.dependency_overrides.clear()


def _client(tmp_path):
    _seed(tmp_path)
    inbox = InboxService(str(tmp_path))
    store = StateStore(str(tmp_path / "state.json"))
    watcher = InboxWatcher(inbox, store, pusher=None, poll_seconds=999)
    app.dependency_overrides[get_inbox] = lambda: inbox
    app.dependency_overrides[get_watcher] = lambda: watcher
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app), inbox, watcher


def test_list_inbox_parses_header_and_status(tmp_path):
    client, _, _ = _client(tmp_path)
    rows = client.get("/v1/inbox").json()
    assert len(rows) == 2
    # newest first (2026-06-22 > 2026-06-21)
    assert rows[0]["job_name"] == "Daily digest" and rows[0]["status"] == "ok"
    assert "ship the inbox feature" in rows[0]["snippet"]
    failed = next(r for r in rows if r["job_id"] == "def456")
    assert failed["status"] == "failed" and failed["job_name"] == "Email triage"


def test_list_inbox_scoped_by_profile(tmp_path):
    client, _, _ = _client(tmp_path)
    rows = client.get("/v1/inbox", headers={"X-Hermes-Profile": "personal"}).json()
    assert [r["job_id"] for r in rows] == ["def456"]


def test_read_inbox_returns_content(tmp_path):
    client, _, _ = _client(tmp_path)
    item_id = client.get("/v1/inbox").json()[0]["id"]
    body = client.get("/v1/inbox/item", params={"id": item_id}).json()
    assert "ship the inbox feature" in body["content"]
    assert body["job_name"] == "Daily digest"


def test_read_inbox_rejects_path_traversal(tmp_path):
    client, _, _ = _client(tmp_path)

    def status(item_id):
        return client.get("/v1/inbox/item", params={"id": item_id}).status_code

    assert status("default/abc123/../../x.md") == 404
    assert status("default/abc123/secret.txt") == 404


def test_alerts_round_trip(tmp_path):
    client, _, _ = _client(tmp_path)
    assert client.get("/v1/inbox/alerts").json()["job_ids"] == []
    r = client.put("/v1/inbox/alerts", json={"job_ids": ["abc123", "abc123", " "]})
    assert r.json()["job_ids"] == ["abc123"]
    assert client.get("/v1/inbox/alerts").json()["job_ids"] == ["abc123"]


class _RecordingPusher:
    def __init__(self):
        self.sent = []

    async def publish(self, message):
        self.sent.append(message)


def test_watcher_pushes_only_for_subscribed_new_files(tmp_path):
    _seed(tmp_path)
    inbox = InboxService(str(tmp_path))
    store = StateStore(str(tmp_path / "state.json"))
    pusher = _RecordingPusher()
    watcher = InboxWatcher(inbox, store, pusher, poll_seconds=0.01)

    async def scenario():
        watcher.set_alerts(["abc123"])
        watcher.start()
        # Seed marks existing files seen; now add a NEW deliverable for abc123.
        newd = tmp_path / "cron" / "output" / "abc123"
        (newd / "2026-06-23_09-00-00.md").write_text(SAMPLE)
        # And one for an UNsubscribed job — must not alert.
        other = tmp_path / "cron" / "output" / "zzz999"
        other.mkdir(parents=True)
        (other / "2026-06-23_10-00-00.md").write_text(SAMPLE)
        await asyncio.sleep(0.1)
        await watcher.stop()

    asyncio.run(scenario())
    assert len(pusher.sent) == 1
    assert "Daily digest" in pusher.sent[0].title


def test_crypto_round_trip():
    key = b"k" * 32
    wire = crypto.encrypt(b'{"hello":"world"}', key)
    assert wire.startswith("HDX1.")
    prefix, nonce_b64, ct_b64 = wire.split(".", 2)

    def unb64(s):
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))

    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    pt = AESGCM(key).decrypt(unb64(nonce_b64), unb64(ct_b64), None)
    assert pt == b'{"hello":"world"}'


def test_decode_key_rejects_bad_length():
    assert crypto.decode_key(base64.b64encode(b"short").decode()) is None
    assert crypto.decode_key(base64.b64encode(b"k" * 32).decode()) == b"k" * 32


def test_pusher_encrypts_device_endpoints_when_key_set():
    posts = []

    class FakeClient:
        async def post(self, url, content=None, headers=None, timeout=None):
            posts.append((url, content, headers))

    reg = EndpointRegistry()
    reg.add("https://ntfy.sh/device-abc")
    key = b"k" * 32
    pusher = Pusher(FakeClient(), reg, static_endpoint=None, get_key=lambda: key)
    asyncio.run(pusher.publish(PushMessage(title="T", status="done", done=True)))
    assert len(posts) == 1
    body = posts[0][1]
    assert body.decode().startswith("HDX1.")
