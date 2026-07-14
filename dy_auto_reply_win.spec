# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — 抖音自动回复 Windows 桌面应用

用法（在项目根目录）：
    pyinstaller dy_auto_reply_win.spec --clean --noconfirm

输出：
    dist/dy_auto_reply/dy_auto_reply.exe   ← 主程序（双击运行）
    dist/dy_auto_reply/                    ← 整个目录都需要分发（onedir 模式）
"""
import json
import os
import sys
from pathlib import Path

import playwright

ROOT = Path(SPECPATH).resolve()
sys.path.insert(0, str(ROOT))


def _bundled_chromium() -> tuple[Path, str]:
    browsers_json = (
        Path(playwright.__file__).parent
        / "driver"
        / "package"
        / "browsers.json"
    )
    browser_data = json.loads(browsers_json.read_text(encoding="utf-8"))
    revision = next(
        item["revision"]
        for item in browser_data.get("browsers", [])
        if item.get("name") == "chromium"
    )

    roots = []
    env_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if env_root and env_root != "0":
        roots.append(Path(env_root))
    roots.append(ROOT / ".playwright-browsers")
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        roots.append(Path(local_app_data) / "ms-playwright")

    for browser_root in roots:
        chromium_dir = browser_root / f"chromium-{revision}"
        chrome_exe = chromium_dir / "chrome-win64" / "chrome.exe"
        if chrome_exe.exists():
            print(f"Bundling Chromium revision {revision} from {chromium_dir}")
            return chromium_dir, revision

    searched = "\n  - ".join(str(root) for root in roots)
    raise RuntimeError(
        "未找到与当前 Playwright 完全匹配的 Chromium。请先执行：\n"
        "  set PLAYWRIGHT_BROWSERS_PATH=%CD%\\.playwright-browsers\n"
        "  python -m playwright install chromium\n"
        f"已检查：\n  - {searched}"
    )


CHROMIUM_DIR, CHROMIUM_REVISION = _bundled_chromium()

datas = [
    (str(ROOT / "config" / "dy_reply.example.yaml"), "config"),
    (str(ROOT / "shared"), "shared"),
    (str(ROOT / "scripts"), "scripts"),
    (str(ROOT / "platforms"), "platforms"),
    (
        str(CHROMIUM_DIR),
        f"playwright-browsers/chromium-{CHROMIUM_REVISION}",
    ),
]

hiddenimports = [
    "fastapi", "fastapi.applications", "fastapi.routing", "fastapi.middleware",
    "fastapi.staticfiles", "fastapi.responses",
    "starlette", "starlette.applications", "starlette.routing",
    "starlette.middleware", "starlette.responses", "starlette.staticfiles",
    "pydantic", "pydantic_core",
    "uvicorn", "uvicorn.main", "uvicorn.config", "uvicorn.server",
    "uvicorn.lifespan", "uvicorn.lifespan.on",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.loops", "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.logging",
    "h11", "anyio",

    "playwright", "playwright.sync_api", "playwright.async_api",
    "playwright._impl", "playwright._impl._driver",

    "yaml", "sqlite3", "httpx", "httpcore",
    "python_socks", "python_socks.async_", "python_socks.async_.asyncio",
    # httpx 走 socks5 代理预检必需，否则配 SOCKS5 的账号代理测试/预检 ImportError
    "socksio",

    "shared", "shared.app_paths", "shared.proxy_utils", "shared.rules", "shared.rules.engine",
    "shared.process_lock",
    "shared.ai", "shared.ai.agent", "shared.conversation",
    "shared.conversation.memory",
    "scripts", "scripts.dy_auto_reply", "scripts.dy_config_ui",
    "scripts.dy_reply_store",
    "platforms", "platforms.browser", "platforms.browser.engine",
    "platforms.browser.proxy_bridge", "platforms.browser.monitor",
    "platforms.browser.bitbrowser", "platforms.browser.stealth",
    "platforms.douyin", "platforms.douyin.messenger",
    "platforms.douyin.commenter", "platforms.douyin.selectors",
]

excludes = [
    "tkinter", "matplotlib", "numpy", "pandas", "scipy",
    "PIL", "cv2", "torch", "tensorflow", "jupyter",
    "notebook", "IPython",
]

block_cipher = None

a = Analysis(
    ["launcher_app.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="dy_auto_reply",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,         # GUI 模式（不弹黑色 cmd 窗口）
    disable_windowed_traceback=False,
    target_arch=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="dy_auto_reply",
)
