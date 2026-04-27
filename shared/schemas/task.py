from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from shared.enums.task import ExecutionMode, TaskStatus, TaskType


class TaskCreateRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    task_type: TaskType
    payload: dict[str, Any] = Field(default_factory=dict)
    execution_mode: ExecutionMode = ExecutionMode.SAAS
    legacy_options: dict[str, Any] = Field(default_factory=dict)
    schedule_time: datetime | None = None


class TaskResponse(BaseModel):
    id: str
    tenant_id: str
    platform: str
    account_id: str
    task_type: TaskType
    status: TaskStatus
    execution_mode: ExecutionMode = ExecutionMode.SAAS
    legacy_options: dict[str, Any] = Field(default_factory=dict)
    schedule_time: datetime | None
    retry_count: int = 0
    max_attempts: int = 1
    result: dict[str, Any] | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    items: list[TaskResponse]
    total: int
    limit: int
    offset: int


class TaskLogResponse(BaseModel):
    id: str
    task_id: str
    tenant_id: str
    platform: str
    account_id: str
    task_type: TaskType
    status: str
    result: dict[str, Any] | None = None
    error_message: str | None = None
    error_code: str | None = None
    retryable: bool | None = None
    attempt: int | None = None
    max_attempts: int | None = None
    created_at: datetime


class TaskLogListResponse(BaseModel):
    items: list[TaskLogResponse]
    total: int
    limit: int
    offset: int
