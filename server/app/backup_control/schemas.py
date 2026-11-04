from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReportedSchedule(BaseModel):
    version: int = Field(ge=0)
    source: str = Field(default="bawaan", max_length=10)
    full: str = Field(max_length=120)
    dump: str = Field(max_length=120)
    binlog: str = Field(max_length=120)
    error: dict | None = None


class MetricsSizes(BaseModel):
    databases: list[dict] = Field(default_factory=list, max_length=50)
    tables: list[dict] = Field(default_factory=list, max_length=10)


class MetricsIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: dict[str, int] = Field(max_length=20)
    max_connections: int = Field(ge=0)
    performance_schema: bool = False
    sizes: MetricsSizes | None = None
    digests: list[dict] | None = Field(default=None, max_length=10)
    indexes: list[dict] | None = Field(default=None, max_length=100)


class HeartbeatIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    server: str | None = Field(default=None, max_length=255)
    machine_id: str | None = Field(default=None, max_length=64)
    agent_version: str | None = Field(default=None, max_length=40)
    mariadb_version: str | None = Field(default=None, max_length=40)
    disk: dict | None = None
    files: dict | None = None
    log: list[str] = Field(default_factory=list, max_length=20)
    pending: dict | None = None
    last_upload_ok: int | None = None
    upload_error: dict | None = None
    schedule: ReportedSchedule | None = None
    metrics: MetricsIn | None = None


class RequestStatusIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Literal["Berjalan", "Mengirim", "Selesai", "Gagal", "Sudah berjalan", "Tidak selesai"]
    note: str = Field(default="", max_length=500)
    log: str = Field(default="", max_length=8000)
