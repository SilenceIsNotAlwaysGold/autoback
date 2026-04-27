"""Cookie 格式转换工具

支持三种格式之间的互转：
1. Playwright storage_state JSON (浏览器模式)
2. 原始 Cookie 字符串 "k1=v1; k2=v2" (WebSocket / HTTP API 模式)
3. 闲管家凭证 JSON (闲管家 API 模式)
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def storage_state_to_cookie_string(state: str | dict, domain_filter: str | None = None) -> str:
    """Playwright storage_state → 原始 Cookie 字符串

    Args:
        state: storage_state JSON 字符串或已解析的字典
        domain_filter: 可选域名过滤（如 ".goofish.com"），只提取匹配的 cookie

    Returns:
        "k1=v1; k2=v2" 格式的 cookie 字符串
    """
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except (json.JSONDecodeError, TypeError):
            return ""

    cookies = state.get("cookies", [])
    if domain_filter:
        cookies = [c for c in cookies if domain_filter in c.get("domain", "")]

    return "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name") and c.get("value"))


def cookie_string_to_storage_state(cookie_str: str, domain: str, path: str = "/") -> dict:
    """原始 Cookie 字符串 → Playwright storage_state 格式

    Args:
        cookie_str: "k1=v1; k2=v2" 格式
        domain: cookie 所属域名（如 ".goofish.com"）
        path: cookie path，默认 "/"

    Returns:
        Playwright storage_state 字典 {"cookies": [...], "origins": []}
    """
    cookies = []
    for pair in cookie_str.split(";"):
        pair = pair.strip()
        if "=" not in pair:
            continue
        name, value = pair.split("=", 1)
        name, value = name.strip(), value.strip()
        if not name:
            continue
        cookies.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": path,
            "httpOnly": False,
            "secure": True,
            "sameSite": "Lax",
        })
    return {"cookies": cookies, "origins": []}


def detect_cookie_format(cookie_data: str) -> str:
    """检测 cookie 数据格式

    Returns:
        "storage_state" | "xianguanjia" | "cookie_string" | "unknown"
    """
    if not cookie_data or not cookie_data.strip():
        return "unknown"

    stripped = cookie_data.strip()

    if stripped.startswith("{"):
        try:
            parsed = json.loads(stripped)
        except (json.JSONDecodeError, TypeError):
            return "unknown"

        if "cookies" in parsed and isinstance(parsed["cookies"], list):
            return "storage_state"
        if "appKey" in parsed or "appSecret" in parsed or "_type" in parsed:
            return "xianguanjia"
        return "unknown"

    # k=v; k2=v2 格式
    if "=" in stripped:
        return "cookie_string"

    return "unknown"


def normalize_cookie_for_browser(cookie_data: str, domain: str) -> dict | None:
    """将任意格式的 cookie 统一转为 Playwright storage_state 字典

    Returns:
        storage_state dict，或 None（无法转换）
    """
    fmt = detect_cookie_format(cookie_data)

    if fmt == "storage_state":
        return json.loads(cookie_data)
    elif fmt == "cookie_string":
        return cookie_string_to_storage_state(cookie_data, domain)
    elif fmt == "xianguanjia":
        # 闲管家凭证不包含浏览器 cookie，无法转换
        return None
    return None


def extract_key_cookies(cookie_data: str, keys: list[str]) -> dict[str, str]:
    """从 cookie 数据中提取指定 key 的值

    Args:
        cookie_data: 任意格式的 cookie 数据
        keys: 要提取的 cookie name 列表

    Returns:
        {key: value} 字典
    """
    fmt = detect_cookie_format(cookie_data)
    result: dict[str, str] = {}

    if fmt == "storage_state":
        parsed = json.loads(cookie_data)
        for c in parsed.get("cookies", []):
            if c.get("name") in keys:
                result[c["name"]] = c.get("value", "")
    elif fmt == "cookie_string":
        for pair in cookie_data.split(";"):
            pair = pair.strip()
            if "=" not in pair:
                continue
            name, value = pair.split("=", 1)
            name = name.strip()
            if name in keys:
                result[name] = value.strip()

    return result
