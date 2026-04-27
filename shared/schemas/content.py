from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ContentCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    body: str = Field(default="", max_length=10000)
    content_type: str = Field(default="text", min_length=1, max_length=32)


class ContentResponse(BaseModel):
    id: str
    tenant_id: str
    title: str
    body: str
    content_type: str
    status: str
    created_at: datetime
    updated_at: datetime


class ContentListResponse(BaseModel):
    items: list[ContentResponse]
    total: int
