from pydantic import BaseModel, Field


class Task(BaseModel):
    id: str
    title: str = ""
    status: str = ""
    model: str | None = None
    updated_at: float = 0.0


class Schedule(BaseModel):
    id: str
    name: str = ""
    cron: str = ""
    paused: bool = False
    next_run: float | None = None
    last_run: float | None = None


class McpServer(BaseModel):
    name: str
    status: str | None = None
    tools_count: int | None = None


class ProfileInfo(BaseModel):
    name: str
    model: str | None = None
    is_default: bool = False


class ProfilesResponse(BaseModel):
    profiles: list[ProfileInfo]
    active: str | None = None


class StartTaskRequest(BaseModel):
    message: str = Field(min_length=1)
    session_id: str | None = None
    model: str | None = None
    # Base64 image data (optionally a data:image/...;base64, prefix) to attach.
    images: list[str] = Field(default_factory=list)


class SteerRequest(BaseModel):
    message: str = Field(min_length=1)


class PushRegisterRequest(BaseModel):
    endpoint: str = Field(min_length=1)


class StartTaskResponse(BaseModel):
    kind: str  # "oneshot" | "cron"
    session_id: str | None = None
    stream_id: str | None = None
    cron: str | None = None


class PushMessage(BaseModel):
    """Wire shape POSTed to the device's push topic. Mirrors the Android client."""

    sessionId: str | None = None  # noqa: N815 - matches client JSON
    title: str = "Hermes"
    status: str = ""
    done: bool = False
