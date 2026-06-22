import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Bridge configuration, loaded from environment / .env.

    The bridge holds the Hermes webui password server-side and exposes a single
    `bridge_token` to the mobile app, so the phone never stores the webui
    password.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Upstream hermes-agent dashboard this bridge fronts (REST + JSON-RPC WS).
    hermes_url: str = "http://127.0.0.1:9119"
    # Dashboard session token (X-Hermes-Session-Token). Optional: if empty and the
    # dashboard runs in loopback mode, the bridge auto-reads the live token from
    # the dashboard HTML on startup and re-reads it if it rotates. Set explicitly
    # only when the dashboard requires a fixed/remote token.
    hermes_token: str = ""

    # Shared secret the mobile app presents as `Authorization: Bearer <token>`.
    # If empty, auth is disabled (development only) — a warning is logged.
    bridge_token: str = ""

    # ntfy / UnifiedPush: where to publish background progress. If topic is
    # empty, push is disabled and the bridge only serves foreground SSE.
    ntfy_base_url: str = "https://ntfy.sh"
    ntfy_topic: str = ""

    # Where the bridge persists state that must survive a restart (push
    # registrations, E2EE key, Inbox alert subscriptions).
    state_dir: str = "state"

    # Root of the hermes-agent install — used to locate local cron deliverables
    # under {hermes_dir}/cron/output and {hermes_dir}/profiles/*/cron/output.
    hermes_dir: str = "~/.hermes"

    # How often the Inbox watcher scans for new local cron deliverables.
    inbox_poll_seconds: float = 30.0

    request_timeout: float = 60.0

    @property
    def push_enabled(self) -> bool:
        return bool(self.ntfy_topic)

    @property
    def auth_enabled(self) -> bool:
        return bool(self.bridge_token)

    @property
    def state_path(self) -> str:
        return os.path.join(os.path.expanduser(self.state_dir), "bridge-state.json")

    @property
    def hermes_root(self) -> str:
        return os.path.expanduser(self.hermes_dir)


settings = Settings()
