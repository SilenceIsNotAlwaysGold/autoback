from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ReplyRuleCreateRequest(BaseModel):
    platform: str = Field(min_length=1, max_length=32)
    account_id: str = Field(min_length=1, max_length=64)
    keywords: str = Field(default="", max_length=1000)
    reply_text: str = Field(default="", max_length=5000)
    trigger_type: str = Field(default="keyword", max_length=16)
    reply_mode: str = Field(default="fixed", max_length=16)
    replies_json: str = Field(default="[]", max_length=10000)
    is_default: bool = Field(default=False)
    enabled: bool = Field(default=True)


class ReplyRuleResponse(BaseModel):
    id: str
    tenant_id: str
    platform: str
    account_id: str
    keywords: str
    reply_text: str
    trigger_type: str = "keyword"
    reply_mode: str = "fixed"
    replies_json: str = "[]"
    is_default: bool = False
    enabled: bool = True
    status: str
    created_at: datetime
    updated_at: datetime


class ReplyRuleListResponse(BaseModel):
    items: list[ReplyRuleResponse]
    total: int
