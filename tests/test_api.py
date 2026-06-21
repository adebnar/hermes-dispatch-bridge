from fastapi.testclient import TestClient

from app.main import app, get_hermes, get_hub


class FakeHermes:
    def __init__(self):
        self.created_cron = None
        self.started = None

    async def sessions(self):
        return [{"session_id": "s1", "title": "Move research", "updated_at": 1.0}]

    async def crons(self):
        return [{"id": "c1", "name": "Daily digest", "schedule": "0 9 * * *", "enabled": False}]

    async def mcp_servers(self):
        return [{"name": "gmail", "status": "ok", "tools_count": 4}]

    async def create_session(self):
        return "new-sess"

    async def start_chat(self, session_id, message, model=None):
        self.started = (session_id, message)
        return "stream-1"

    async def create_cron(self, name, schedule):
        self.created_cron = (name, schedule)
        return {"id": "c2", "name": name, "schedule": schedule}

    async def cron_action(self, action, cron_id):
        return None


class FakeHub:
    def __init__(self):
        self.consumed = None

    def ensure_consumer(self, stream_id, session_id):
        self.consumed = (stream_id, session_id)


def make_client():
    fake_hermes = FakeHermes()
    fake_hub = FakeHub()
    app.dependency_overrides[get_hermes] = lambda: fake_hermes
    app.dependency_overrides[get_hub] = lambda: fake_hub
    return TestClient(app), fake_hermes, fake_hub


def teardown_function():
    app.dependency_overrides.clear()


def test_health():
    client, _, _ = make_client()
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_list_tasks_maps_sessions():
    client, _, _ = make_client()
    r = client.get("/v1/tasks")
    assert r.status_code == 200
    assert r.json()[0] == {
        "id": "s1", "title": "Move research", "status": "", "model": None, "updated_at": 1.0,
    }


def test_list_schedules_maps_enabled_to_paused():
    client, _, _ = make_client()
    r = client.get("/v1/schedules")
    body = r.json()[0]
    assert body["cron"] == "0 9 * * *" and body["paused"] is True


def test_start_oneshot_starts_run_and_holds_it():
    client, hermes, hub = make_client()
    r = client.post("/v1/tasks", json={"message": "compare 3 moving companies"})
    body = r.json()
    assert body["kind"] == "oneshot" and body["stream_id"] == "stream-1"
    assert hub.consumed == ("stream-1", "new-sess")
    assert hermes.started == ("new-sess", "compare 3 moving companies")


def test_start_recurring_creates_cron_instead():
    client, hermes, _ = make_client()
    r = client.post("/v1/tasks", json={"message": "every Monday at 9am send a digest"})
    body = r.json()
    assert body["kind"] == "cron" and body["cron"] == "0 9 * * 1"
    assert hermes.created_cron == ("every Monday at 9am send a digest", "0 9 * * 1")


def test_schedule_action_rejects_unknown():
    client, _, _ = make_client()
    assert client.post("/v1/schedules/c1/frobnicate").status_code == 400
    assert client.post("/v1/schedules/c1/pause").status_code == 200


def test_mcp_list():
    client, _, _ = make_client()
    assert client.get("/v1/mcp").json()[0]["name"] == "gmail"
