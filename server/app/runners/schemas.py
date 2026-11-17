from typing import Literal

from pydantic import BaseModel, Field


class ManagedState(BaseModel):
    name: str = Field(max_length=64)
    state: str = Field(max_length=20)       # running | exited | restarting | paused | created | dead
    status: str = Field(default="", max_length=100)
    image: str = Field(default="", max_length=200)


class RunnerHeartbeatIn(BaseModel):
    server: str | None = Field(default=None, max_length=255)
    machine_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32,64}$")
    version: str | None = Field(default=None, max_length=40)
    scratch_total: int | None = Field(default=None, ge=0)
    scratch_free: int | None = Field(default=None, ge=0)
    cpu_percent: float | None = Field(default=None, ge=0, le=100)
    cpu_count: int | None = Field(default=None, ge=1, le=100_000)
    load1: float | None = Field(default=None, ge=0, le=1_000_000)
    mem_total: int | None = Field(default=None, ge=0)
    mem_available: int | None = Field(default=None, ge=0)
    containers: list[ManagedState] | None = Field(default=None, max_length=200)


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


class StandbyResultIn(BaseModel):
    full_backup_id: int | None = None          # full yang dipulihkan; kosong untuk penerapan binlog saja
    applied_binlog_no: int | None = Field(default=None, ge=0)
    port: int | None = Field(default=None, ge=1, le=65535)


class JobResultIn(BaseModel):
    status: Literal["Lolos", "Gagal", "Tidak terlaksana"]
    note: str | None = Field(default=None, max_length=500)
    results: list[TestResultIn] = Field(default=[], max_length=500)
    standby: StandbyResultIn | None = None
