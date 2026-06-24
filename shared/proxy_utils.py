"""代理字符串智能解析 / 归一化

用户购买的代理常见格式五花八门，这里统一解析成标准 URL：
    scheme://user:pass@host:port

支持的输入格式（自动识别）：
    1. 竖线格式（最常见的售卖格式）：
         182.40.197.72|14081|luckly579|luckly579|2026-04-17
         182.40.197.72|14081|user|pass
         182.40.197.72|14081                       （无认证）
       第 5 段（到期日等）一律忽略。
    2. 冒号格式：ip:port:user:pass  或  ip:port
    3. user:pass@ip:port            （无 scheme）
    4. 标准 URL：socks5://user:pass@ip:port、http://...、https://...、socks5h://...

关于"智能判断 socks5 / http"：
    仅凭 IP/端口/账号密码无法从字符串本身区分 socks5 还是 http
    （两者携带的信息完全相同）。因此：
      - 用户若显式写了 scheme（socks5:// / http://），尊重用户。
      - 未写 scheme 时，结构上先按 default_scheme 归一化（默认 socks5，
        市面按"IP|端口|账号|密码"售卖的代理绝大多数是 socks5）；
      - 真正的识别交给 detect_scheme()：实际去连一次，socks5 / http
        哪个能通就用哪个，再把确定的 scheme 写回配置。
"""
from __future__ import annotations

from urllib.parse import urlparse, quote, unquote

# 认可的 scheme（小写）
_KNOWN_SCHEMES = ("socks5h", "socks5", "socks4", "https", "http")


def parse_proxy(raw: str) -> dict | None:
    """把任意支持格式解析为 {scheme, host, port, username, password}。

    无法解析（缺 host/port）时返回 None。scheme 可能为 None（未显式指定）。
    """
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None

    scheme: str | None = None

    # ── 先剥离显式 scheme ─────────────────────────────
    # 形如 socks5://...  或  http://...
    low = s.lower()
    for sc in _KNOWN_SCHEMES:
        if low.startswith(sc + "://"):
            scheme = sc
            s = s[len(sc) + 3:]
            break

    host = port = username = password = None

    # ── 竖线格式：ip|port|user|pass|...（忽略第 5 段及以后）─────
    if "|" in s:
        parts = [p.strip() for p in s.split("|")]
        if len(parts) >= 2 and parts[0] and parts[1]:
            host, port = parts[0], parts[1]
            if len(parts) >= 4 and parts[2]:
                username, password = parts[2], parts[3]
            elif len(parts) == 3 and parts[2]:
                # ip|port|user（只有用户名，无密码）——少见但容错
                username = parts[2]

    # ── 标准 URL / user:pass@host:port（含 @ 用 urlparse）───────
    elif "@" in s:
        # urlparse 需要 scheme 才能正确拆 netloc，临时补一个
        parsed = urlparse(f"//{s}", scheme="")
        host = parsed.hostname
        port = str(parsed.port) if parsed.port else None
        username = unquote(parsed.username) if parsed.username else None
        password = unquote(parsed.password) if parsed.password else None

    # ── 冒号格式：ip:port:user:pass  或  ip:port ──────────────
    elif ":" in s:
        parts = s.split(":")
        if len(parts) == 2:
            host, port = parts[0].strip(), parts[1].strip()
        elif len(parts) == 4:
            host, port, username, password = (p.strip() for p in parts)
        elif len(parts) >= 4:
            # ip:port:user:pass（密码里可能含冒号，余下全归密码）
            host, port = parts[0].strip(), parts[1].strip()
            username = parts[2].strip()
            password = ":".join(parts[3:])

    if not host or not port:
        return None
    try:
        port = int(str(port).strip())
    except (TypeError, ValueError):
        return None
    if not (0 < port < 65536):
        return None

    return {
        "scheme": scheme,
        "host": host,
        "port": port,
        "username": username or None,
        "password": password or None,
    }


def build_url(parts: dict, scheme: str | None = None) -> str:
    """由 parse_proxy 的结果组装标准代理 URL。"""
    sc = (scheme or parts.get("scheme") or "socks5").lower()
    host = parts["host"]
    port = parts["port"]
    user = parts.get("username")
    pwd = parts.get("password")
    if user:
        # 账号密码里可能含 @ : / 等特殊字符，转义以免破坏 URL
        auth = quote(str(user), safe="")
        if pwd:
            auth += ":" + quote(str(pwd), safe="")
        return f"{sc}://{auth}@{host}:{port}"
    return f"{sc}://{host}:{port}"


def normalize_proxy(raw: str, default_scheme: str = "socks5") -> str:
    """把任意格式归一化为标准 URL。

    - 解析失败时原样返回（不破坏用户输入，交由后续校验报错）。
    - 已是完整标准 URL（带 scheme）的，重新组装一遍（顺带转义账号密码）。
    - 无 scheme 的，用 default_scheme。
    """
    if not raw or not raw.strip():
        return ""
    parts = parse_proxy(raw)
    if not parts:
        return raw.strip()
    return build_url(parts, parts.get("scheme") or default_scheme)


def detect_scheme(raw: str, timeout: float = 6.0,
                  candidates: tuple[str, ...] = ("socks5", "http")) -> dict:
    """实际连一次以判定代理类型（socks5 vs http）。

    仅凭字符串无法区分，这里用 httpx 依次按候选 scheme 发起请求，
    第一个能取到出口 IP 的即判定为该类型。

    用户已显式写了 scheme 时不再瞎试，直接验证该 scheme。

    返回 {ok, scheme, url, ip, latency_ms, message}
    """
    import time as _t
    import httpx

    parts = parse_proxy(raw)
    if not parts:
        return {"ok": False, "message": f"无法解析代理格式：{raw!r}"}

    # 用户显式指定了 scheme → 只验证它，不猜
    if parts.get("scheme"):
        order = [parts["scheme"]]
    else:
        order = list(candidates)

    # 探测出口 IP 的地址（任一成功即可；国内 ipify/httpbin 时常超时）
    probes = [
        ("http://ip-api.com/json/", lambda j: j.get("query")),
        ("http://httpbin.org/ip", lambda j: j.get("origin", "").split(",")[0].strip()),
        ("https://api.ipify.org?format=json", lambda j: j.get("ip")),
    ]

    last_err = None
    for sc in order:
        url = build_url(parts, sc)
        t0 = _t.monotonic()
        try:
            with httpx.Client(proxy=url, timeout=timeout, verify=False) as client:
                for probe_url, parser in probes:
                    try:
                        resp = client.get(probe_url)
                        resp.raise_for_status()
                        ip = parser(resp.json())
                        if ip:
                            return {
                                "ok": True,
                                "scheme": sc,
                                "url": url,
                                "ip": ip,
                                "latency_ms": int((_t.monotonic() - t0) * 1000),
                                "message": f"识别为 {sc.upper()} 代理",
                            }
                    except Exception as e:
                        last_err = f"{type(e).__name__}: {e}"
                        continue
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            continue

    return {"ok": False, "message": f"socks5/http 均无法连通。最后错误：{last_err}"}

