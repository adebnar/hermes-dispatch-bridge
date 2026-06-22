from fastapi.testclient import TestClient

from app.auth import require_auth
from app.main import app, get_hermes, get_hub, get_registry
from app.registry import EndpointRegistry


class FakeHermes:
    """Stands in for the dashboard REST client."""

    def __init__(self):
        self.created_cron = None
        self.cron_actions = []
        self.sessions_profile = "unset"

    async def sessions(self, profile=None):
        self.sessions_profile = profile
        return [{"id": "s1", "title": "Move research", "model": "m", "updated_at": 1.0}]

    async def profiles(self):
        return [
            {"name": "default", "model": "gemma", "is_default": True},
            {"name": "personal", "model": "claude", "is_default": False},
        ]

    async def active_profile(self):
        return "personal"

    async def crons(self):
        return [{
            "id": "c1", "name": "Daily digest", "schedule_display": "0 9 * * *",
            "state": "paused", "paused_at": "2026-06-20T00:00:00-05:00", "enabled": False,
        }]

    async def mcp_servers(self):
        return [{"name": "gmail", "enabled": True, "tools": ["a", "b", "c"]}]

    async def create_cron(self, name, prompt, schedule, profile=None):
        self.created_cron = (name, prompt, schedule)
        self.cron_profile = profile
        return {"id": "c2", "name": name, "schedule": schedule}

    async def messages(self, session_id, profile=None):
        return [
            {"role": "system", "content": "ignore me"},
            {"role": "user", "content": "hi there"},
            {"role": "assistant", "content": [{"type": "text", "text": "hello back"}]},
        ]

    async def cron_action(self, action, cron_id):
        self.cron_actions.append((action, cron_id))


class FakeHub:
    """Stands in for the WS StreamHub."""

    def __init__(self):
        self.started = None
        self.cancelled = None
        self.steered = None

    async def start_run(self, session_id, message, profile=None, images=None):
        self.started = (session_id, message, profile, images or [])
        return "sess-1"

    async def cancel(self, stream_id):
        self.cancelled = stream_id

    async def steer(self, stream_id, message):
        self.steered = (stream_id, message)


def make_client():
    fake_hermes = FakeHermes()
    fake_hub = FakeHub()
    registry = EndpointRegistry()
    app.dependency_overrides[get_hermes] = lambda: fake_hermes
    app.dependency_overrides[get_hub] = lambda: fake_hub
    app.dependency_overrides[get_registry] = lambda: registry
    # Hermetic: don't depend on ambient .env auth config in tests.
    app.dependency_overrides[require_auth] = lambda: None
    return TestClient(app), fake_hermes, fake_hub, registry


def teardown_function():
    app.dependency_overrides.clear()


def test_health():
    client, _, _, _ = make_client()
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_list_tasks_maps_dashboard_sessions():
    client, _, _, _ = make_client()
    r = client.get("/v1/tasks")
    assert r.status_code == 200
    body = r.json()[0]
    assert body["id"] == "s1" and body["title"] == "Move research" and body["model"] == "m"


def test_list_schedules_maps_paused():
    client, _, _, _ = make_client()
    body = client.get("/v1/schedules").json()[0]
    assert body["cron"] == "0 9 * * *" and body["paused"] is True


def test_start_oneshot_opens_ws_run():
    client, _, hub, _ = make_client()
    r = client.post("/v1/tasks", json={"message": "compare 3 moving companies"})
    body = r.json()
    assert body["kind"] == "oneshot"
    assert body["stream_id"] == "sess-1" and body["session_id"] == "sess-1"
    assert hub.started == (None, "compare 3 moving companies", None, [])


def test_profiles_list_with_active():
    client, _, _, _ = make_client()
    body = client.get("/v1/profiles").json()
    assert body["active"] == "personal"
    assert [p["name"] for p in body["profiles"]] == ["default", "personal"]
    assert body["profiles"][0]["is_default"] is True


def test_profile_header_forwarded_to_run():
    client, _, hub, _ = make_client()
    client.post("/v1/tasks", json={"message": "do a thing"},
                headers={"X-Hermes-Profile": "dito"})
    assert hub.started == (None, "do a thing", "dito", [])


def test_profile_header_scopes_task_list():
    client, hermes, _, _ = make_client()
    client.get("/v1/tasks", headers={"X-Hermes-Profile": "odos"})
    assert hermes.sessions_profile == "odos"


def test_start_recurring_creates_cron():
    client, hermes, hub, _ = make_client()
    r = client.post("/v1/tasks", json={"message": "every Monday at 9am send a digest"})
    body = r.json()
    assert body["kind"] == "cron" and body["cron"] == "0 9 * * 1"
    assert hermes.created_cron == (
        "every Monday at 9am send a digest",
        "every Monday at 9am send a digest",
        "0 9 * * 1",
    )
    assert hub.started is None  # no run for a cron


def test_schedule_action_rejects_unknown():
    client, _, _, _ = make_client()
    assert client.post("/v1/schedules/c1/frobnicate").status_code == 400
    assert client.post("/v1/schedules/c1/pause").status_code == 200


def test_mcp_list_maps_dashboard_shape():
    client, _, _, _ = make_client()
    row = client.get("/v1/mcp").json()[0]
    assert row["name"] == "gmail" and row["status"] == "enabled" and row["tools_count"] == 3


def test_cancel_calls_hub():
    client, _, hub, _ = make_client()
    assert client.post("/v1/tasks/sess-9/cancel").status_code == 200
    assert hub.cancelled == "sess-9"


def test_steer_calls_hub():
    client, _, hub, _ = make_client()
    r = client.post("/v1/tasks/sess-9/steer", json={"message": "use Gmail"})
    assert r.status_code == 200
    assert hub.steered == ("sess-9", "use Gmail")


def test_push_register_and_unregister():
    client, _, _, registry = make_client()
    r = client.post("/v1/push/register", json={"endpoint": "https://ntfy.sh/dev1"})
    assert r.status_code == 200 and r.json()["count"] == 1
    assert "https://ntfy.sh/dev1" in registry.all()
    r = client.post("/v1/push/unregister", json={"endpoint": "https://ntfy.sh/dev1"})
    assert r.json()["count"] == 0


def test_start_with_images_forwarded():
    client, _, hub, _ = make_client()
    img = "data:image/png;base64,AAAA"
    client.post("/v1/tasks", json={"message": "what is this?", "images": [img]})
    assert hub.started == (None, "what is this?", None, [img])


def test_schedules_handles_dashboard_dict_schedule_and_iso_dates():
    client, hermes, _, _ = make_client()

    async def real_crons():
        return [{
            "id": "c9", "name": "Digest",
            "schedule": {"kind": "cron", "expr": "0 9 * * *"},
            "schedule_display": "0 9 * * *", "state": "scheduled", "enabled": True,
            "next_run_at": "2026-06-22T09:00:00-05:00",
            "last_run_at": "2026-06-21T09:00:05-05:00",
        }]

    hermes.crons = real_crons
    r = client.get("/v1/schedules")
    assert r.status_code == 200
    body = r.json()[0]
    assert body["cron"] == "0 9 * * *"
    assert body["paused"] is False
    assert body["next_run"] is not None


def test_schedules_scoped_by_profile():
    client, hermes, _, _ = make_client()

    async def crons_multi():
        return [
            {"id": "a", "name": "p1", "schedule_display": "0 9 * * *", "profile_name": "personal"},
            {"id": "b", "name": "d1", "schedule_display": "0 9 * * *", "profile_name": "dito"},
        ]

    hermes.crons = crons_multi
    rows = client.get("/v1/schedules", headers={"X-Hermes-Profile": "dito"}).json()
    assert [r["id"] for r in rows] == ["b"]


def test_task_messages_maps_roles_and_multipart():
    client, _, _, _ = make_client()
    rows = client.get("/v1/tasks/abc/messages").json()
    assert [m["role"] for m in rows] == ["user", "assistant"]  # system dropped
    assert rows[1]["text"] == "hello back"                      # multipart joined


def test_recurring_cron_created_under_active_profile():
    client, hermes, _, _ = make_client()
    client.post("/v1/tasks", json={"message": "every Monday at 9am digest"},
                headers={"X-Hermes-Profile": "personal"})
    assert hermes.cron_profile == "personal"
