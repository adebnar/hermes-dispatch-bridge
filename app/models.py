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
    prompt: str = ""
    paused: bool = False
    next_run: float | None = None
    last_run: float | None = None


class ScheduleUpdateRequest(BaseModel):
    """Editable fields of a cron job. Unset fields are left unchanged."""

    name: str | None = None
    prompt: str | None = None
    # Natural-language or cron expr, e.g. "every 10m" or "0 9 * * *".
    schedule: str | None = None


class Message(BaseModel):
    role: str
    text: str


class McpServer(BaseModel):
    name: str
    status: str | None = None
    tools_count: int | None = None


class ModelOption(BaseModel):
    provider: str
    model: str


class ModelsResponse(BaseModel):
    models: list[ModelOption]
    current: str | None = None


class SetModelRequest(BaseModel):
    model: str = Field(min_length=1)
    provider: str | None = None


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


class ApproveRequest(BaseModel):
    choice: str = "deny"  # once | always | deny
    all: bool = False


class ClarifyRequest(BaseModel):
    answer: str = Field(min_length=1)


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


class InboxItem(BaseModel):
    """A local cron deliverable (saved-to-disk markdown result)."""

    id: str
    profile: str = ""
    job_id: str = ""
    job_name: str = ""
    title: str = ""  # display title — derived from the result's first heading
    status: str = "ok"  # ok | failed | silent
    created_at: float | None = None
    size: int = 0
    snippet: str = ""


class InboxContent(InboxItem):
    output: str = ""  # result only (the agent's output), for the reader
    content: str = ""  # full raw markdown, for "Details"


class AlertsRequest(BaseModel):
    job_ids: list[str] = Field(default_factory=list)
    alert_on_failures: bool | None = None


class AlertsResponse(BaseModel):
    job_ids: list[str] = Field(default_factory=list)
    alert_on_failures: bool = False


class PushKeyRequest(BaseModel):
    # Base64 (standard) 32-byte AES-256 key, or "" to disable encryption.
    key: str = ""


class ArchiveRequest(BaseModel):
    archived: bool = True


class TranscribeResponse(BaseModel):
    text: str = ""
