"""SOCKS5 认证代理桥接（纯 Python asyncio 实现，进程内运行）

Chromium 原生不支持带认证的 SOCKS5 代理。解决方案：
  Chromium → 本地无认证 HTTP 代理（localhost:random_port） → 认证 SOCKS5 → 外网

实现：在主进程内开 asyncio.start_server，无外部子进程依赖（PyInstaller 友好）。
"""
from __future__ import annotations

import asyncio
import logging
import socket
from urllib.parse import urlparse

from python_socks.async_.asyncio import Proxy

logger = logging.getLogger(__name__)


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def needs_bridge(proxy_url: str) -> bool:
    """判断是否需要桥接（带认证的代理必须桥）"""
    if not proxy_url:
        return False
    parsed = urlparse(proxy_url)
    scheme = (parsed.scheme or "http").lower()
    has_auth = bool(parsed.username)
    if scheme in ("socks4", "socks5", "socks5h") and has_auth:
        return True
    if scheme in ("http", "https") and has_auth:
        return True
    return False


class ProxyBridge:
    def __init__(self, upstream_url: str):
        self.upstream_url = upstream_url or ""
        self.needed = needs_bridge(self.upstream_url)
        self.local_port = 0
        self.local_url = ""
        self._server: asyncio.AbstractServer | None = None
        self._task: asyncio.Task | None = None

    async def _tunnel(self, r, w, host: str, port: int):
        proxy = Proxy.from_url(self.upstream_url)
        try:
            stream = await proxy.connect(host, port, timeout=15)
        except Exception as e:
            try:
                w.write(f"HTTP/1.1 502 Bad Gateway\r\n\r\n{e}".encode())
                await w.drain()
            except Exception:
                pass
            w.close()
            return

        try:
            w.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await w.drain()
        except Exception:
            return

        up_r, up_w = await asyncio.open_connection(sock=stream)

        async def pipe(src, dst):
            try:
                while True:
                    data = await src.read(65536)
                    if not data:
                        break
                    dst.write(data)
                    await dst.drain()
            except Exception:
                pass
            finally:
                try:
                    dst.close()
                except Exception:
                    pass

        await asyncio.gather(pipe(r, up_w), pipe(up_r, w))

    async def _handle(self, reader, writer):
        try:
            line = await reader.readline()
            if not line:
                writer.close()
                return
            req = line.decode(errors="ignore").strip()
            while True:
                h = await reader.readline()
                if h == b"\r\n" or not h:
                    break

            parts = req.split(" ")
            if len(parts) < 3:
                writer.close()
                return
            method, target, _ = parts

            if method == "CONNECT":
                host, port_s = target.rsplit(":", 1)
                await self._tunnel(reader, writer, host, int(port_s))
            else:
                u = urlparse(target)
                host, port = u.hostname, (u.port or 80)
                path = u.path or "/"
                if u.query:
                    path += "?" + u.query
                proxy = Proxy.from_url(self.upstream_url)
                stream = await proxy.connect(host, port, timeout=15)
                up_r, up_w = await asyncio.open_connection(sock=stream)
                fresh = f"{method} {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
                up_w.write(fresh.encode())
                await up_w.drain()

                async def pipe(src, dst):
                    try:
                        while True:
                            data = await src.read(65536)
                            if not data:
                                break
                            dst.write(data)
                            await dst.drain()
                    except Exception:
                        pass
                    finally:
                        try:
                            dst.close()
                        except Exception:
                            pass

                await asyncio.gather(pipe(reader, up_w), pipe(up_r, writer))
        except Exception as e:
            try:
                writer.write(f"HTTP/1.1 500 Bridge Error\r\n\r\n{e}".encode())
                await writer.drain()
            except Exception:
                pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def start(self) -> str:
        """启动 in-process 桥接，返回本地无认证 HTTP 代理 URL"""
        if not self.needed:
            return self.upstream_url

        self.local_port = _pick_free_port()
        self.local_url = f"http://127.0.0.1:{self.local_port}"

        logger.info("[proxy-bridge] Starting in-process %s → %s",
                    self.local_url, urlparse(self.upstream_url).hostname)

        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", self.local_port
        )
        self._task = asyncio.create_task(self._server.serve_forever())
        logger.info("[proxy-bridge] Ready on %s", self.local_url)
        return self.local_url

    def is_alive(self) -> bool:
        return self._server is not None and self._server.is_serving()

    async def stop(self):
        if self._server is not None:
            try:
                self._server.close()
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self.local_url:
            logger.info("[proxy-bridge] Stopped %s", self.local_url)
