from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from shared.enums.rbac import Role


class TenantCreateRequest(BaseModel):
    tenant_id: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    password: Optional[str] = None
    expires_at: Optional[str] = None
    enabled_platforms: Optional[list[str]] = None


class TenantResponse(BaseModel):
    tenant_id: str
    name: str
    status: str
    created_at: datetime


class TenantListResponse(BaseModel):
    items: list[TenantResponse]
    total: int


class UserCreateRequest(BaseModel):
    user_id: str = Field(min_length=2, max_length=64)
    username: str = Field(min_length=1, max_length=128)
    password: Optional[str] = None


class UserResponse(BaseModel):
    user_id: str
    username: str
    status: str
    created_at: datetime


class MembershipUpsertRequest(BaseModel):
    user_id: str = Field(min_length=2, max_length=64)
    role: Role


class MembershipResponse(BaseModel):
    tenant_id: str
    user_id: str
    role: Role
    updated_at: datetime


class MembershipListResponse(BaseModel):
    items: list[MembershipResponse]
    total: int
