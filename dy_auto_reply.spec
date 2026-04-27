# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — 抖音自动回复 macOS 桌面应用

用法（在项目根目录）：
    pyinstaller dy_auto_reply.spec --clean --noconfirm

输出：
    dist/dy_auto_reply.app/        ← 双击运行的应用包
"""
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve()
sys.path.insert(0, str(ROOT))

# ── 资源（只读，bundled）─────────────────────────────────
datas = [
    (str(ROOT / "config" / "dy_reply.example.yaml"), "config"),
    (str(ROOT / "shared"), "shared"),
    (str(ROOT / "scripts"), "scripts"),
    (str(ROOT / "platforms"), "platforms"),
]

# ── hidden imports ──────────────────────────────────────
# FastAPI / Pydantic / Uvicorn 用动态加载，必须显式
hiddenimports = [
    # FastAPI 全家桶
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

    # Playwright
    "playwright", "playwright.sync_api", "playwright.async_api",
    "playwright._impl", "playwright._impl._driver",

    # Yaml / sqlite / httpx / proxy
    "yaml", "sqlite3", "httpx", "httpcore",
    "python_socks", "python_socks.async_", "python_socks.async_.asyncio",

    # 项目内部模块（PyInstaller 静态分析可能漏掉）
    "shared", "shared.app_paths", "shared.rules", "shared.rules.engine",
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

# ── 排除大体积无用包 ─────────────────────────────────────
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
    console=False,         # GUI 应用（双击不弹终端）
    disable_windowed_traceback=False,
    argv_emulation=True,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
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

app = BUNDLE(
    coll,
    name="dy_auto_reply.app",
    icon=None,
    bundle_identifier="com.autofish.dyreply",
    info_plist={
        "CFBundleName": "抖音自动回复",
        "CFBundleDisplayName": "抖音自动回复",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "NSHighResolutionCapable": True,
        "LSBackgroundOnly": False,
        "NSRequiresAquaSystemAppearance": False,
    },
)
