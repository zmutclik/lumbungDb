from typing import Literal

from pydantic import BaseModel, Field


class RunnerHeartbeatIn(BaseModel):
    server: str | None = Field(default=None, max_length=255)
    machine_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32,64}$")
    version: str | None = Field(default=None, max_length=40)
    scratch_total: int | None = Field(default=None, ge=0)
    scratch_free: int | None = Field(default=None, ge=0)


class ProgressIn(BaseModel):
    step: str = Field(min_length=1, max_length=200)


class TestResultIn(BaseModel):
    kind: Literal["full", "dump", "users", "binlog"]
    database: str | None = Field(default=None, max_length=64)
    backup_id: int | None = None
    status: Literal["Lolos", "Gagal", "Tidak terlaksana"]
    failed_step: str | None = Field(default=None, max_length=200)
    duration_seconds: int | None = Field(default=None, ge=0)
    log: str | None = Field(default=None, max_length=8000)


class JobResultIn(BaseModel):
    status: Literal["Lolos", "Gagal", "Tidak terlaksana"]
    note: str | None = Field(default=None, max_length=500)
    results: list[TestResultIn] = Field(default=[], max_length=500)
