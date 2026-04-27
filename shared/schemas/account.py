from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PlatformAccountCreateRequest(BaseModel):
    platform: str = Field(min_length=1, max_length=32)
    account_name: str = Field(min_length=1, max_length=128)
    cookie: str | None = None
    group_name: str | None = None
    login_mode: str = Field(default="browser", max_length=32)
    auth_mode: str = Field(default="cookie", max_length=32)
    phone: str | None = None
    api_key: str | None = None
    api_secret: str | None = None


class PlatformAccountUpdateRequest(BaseModel):
    """通用账号信息更新"""
    account_name: str | None = None
    group_name: str | None = None
    phone: str | None = None
    proxy_url: str | None = None
    daily_limit: int | None = None
    status: str | None = None


class PlatformAccountAuthUpdateRequest(BaseModel):
    """认证信息更新"""
    auth_mode: str | None = None
    login_mode: str | None = None
    cookie: str | None = None
    api_key: str | None = None
    api_secret: str | None = None
    token: str | None = None
    refresh_token: str | None = None


class PlatformAccountAIConfigRequest(BaseModel):
    """AI 客服配置"""
    ai_enabled: bool = False
    ai_config: dict | None = None  # {"model", "experts_enabled", "price_floor", "custom_prompts", ...}


class PlatformAccountResponse(BaseModel):
    id: str
    tenant_id: str
    platform: str
    account_name: str
    status: str
    cookie: str | None
    login_mode: str = "browser"
    auth_mode: str = "cookie"
    group_name: str | None = None
    phone: str | None = None
    api_key: str | None = None
    api_secret: str | None = None
    token: str | None = None
    refresh_token: str | None = None
    token_expires_at: datetime | None = None
    ai_enabled: bool = False
    ai_config: str | None = None
    proxy_url: str | None = None
    fail_count: int = 0
    cooldown_until: datetime | None = None
    daily_limit: int = 0
    daily_used: int = 0
    last_active_at: datetime | None
    login_checked_at: datetime | None = None
    login_expires_hint: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PlatformAccountListResponse(BaseModel):
    items: list[PlatformAccountResponse]
    total: int


class AccountLoginCheckResponse(BaseModel):
    account_id: str
    platform: str
    account_name: str
    is_logged_in: bool
    status: str
    checked_at: datetime
    detail: str | None = None


class AccountHealthResponse(BaseModel):
    account_id: str
    platform: str
    account_name: str
    status: str
    auth_mode: str
    login_mode: str
    is_logged_in: bool
    token_valid: bool
    token_expires_at: datetime | None = None
    fail_count: int = 0
    cooldown_until: datetime | None = None
    daily_used: int = 0
    daily_limit: int = 0
    last_active_at: datetime | None = None
    login_checked_at: datetime | None = None


class AccountMessageItem(BaseModel):
    message_id: str
    sender: str
    content: str
    created_at: datetime


class AccountMessageListResponse(BaseModel):
    account_id: str
    platform: str
    total: int
    items: list[AccountMessageItem]
