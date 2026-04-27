"""桌面应用启动器（PyInstaller 入口）

通过 --mode 参数分发到不同子命令：
  --mode=ui    （默认）启动 FastAPI 配置 UI + 自动开浏览器
  --mode=main  启动主回复脚本（被 UI 通过 subprocess 调起）
  --mode=login --account NAME   交互式登录

打包后单一可执行文件：dy_auto_reply.app/Contents/MacOS/dy_auto_reply
开发模式：python launcher_app.py [--mode=...]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _hide_from_dock():
    """子进程不显示 Dock 图标（macOS 限定）

    通过 ctypes 直接调 Objective-C runtime：
        NSApplication.sharedApplication().setActivationPolicy:Prohibited
    """
    if sys.platform != "darwin":
        return
    try:
        import ctypes
        from ctypes import c_void_p, c_char_p, c_int

        objc = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib")
        objc.objc_getClass.restype = c_void_p
        objc.objc_getClass.argtypes = [c_char_p]
        objc.sel_registerName.restype = c_void_p
        objc.sel_registerName.argtypes = [c_char_p]

        # objc_msgSend 取 (id) 返回值
        msg_id = objc.objc_msgSend
        msg_id.restype = c_void_p
        msg_id.argtypes = [c_void_p, c_void_p]

        # objc_msgSend 接 int 参数版本
        msg_int = ctypes.cdll.LoadLibrary("/usr/lib/libobjc.A.dylib").objc_msgSend
        msg_int.restype = c_void_p
        msg_int.argtypes = [c_void_p, c_void_p, c_int]

        # NSApplication.sharedApplication() 才存在 ApplicationServices；
        # 其实 NSApplication 在 AppKit 里，链接一下
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")

        cls = objc.objc_getClass(b"NSApplication")
        if not cls:
            return
        sel_shared = objc.sel_registerName(b"sharedApplication")
        app = msg_id(cls, sel_shared)
        if not app:
            return
        sel_set = objc.sel_registerName(b"setActivationPolicy:")
        # NSApplicationActivationPolicyProhibited = 2
        msg_int(app, sel_set, 2)
    except Exception:
        pass


def _setup_paths():
    """打包模式下：把 _MEIPASS 加入 sys.path 让现有 import 都能 work；
    切换 cwd 到用户数据目录，让所有相对路径自然指向应用支持目录。"""
    if getattr(sys, "frozen", False):
        meipass = Path(getattr(sys, "_MEIPASS", ""))
        if meipass and str(meipass) not in sys.path:
            sys.path.insert(0, str(meipass))
        # Playwright 浏览器路径：强制指向用户缓存（默认行为），
        # 否则 frozen 模式下会去 bundle 内的 .local-browsers 找（不存在）
        if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
            if sys.platform == "darwin":
                default_pw = Path.home() / "Library" / "Caches" / "ms-playwright"
            elif sys.platform == "win32":
                default_pw = Path.home() / "AppData" / "Local" / "ms-playwright"
            else:
                default_pw = Path.home() / ".cache" / "ms-playwright"
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(default_pw)
    # 加项目根（开发模式 + 打包模式都需要）
    root = Path(__file__).resolve().parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


_setup_paths()

from shared.app_paths import APP_DATA, ensure_data_dirs, chdir_to_data, is_frozen  # noqa: E402


def _ensure_chromium():
    """检查 Playwright Chromium 是否已下载，没有就调 playwright driver 拉一次"""
    pw_path = Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
                   or (Path.home() / "Library" / "Caches" / "ms-playwright"))
    if pw_path.exists():
        # 检查是否有任意 chromium 版本目录
        if any(p.is_dir() and p.name.startswith("chromium-") for p in pw_path.iterdir()):
            return  # 已装

    print("[launcher] First run: downloading Chromium (~150 MB), please wait...")
    try:
        from playwright._impl._driver import compute_driver_executable, get_driver_env
        node_exe, cli_js = compute_driver_executable()
        env = get_driver_env()
        env["PLAYWRIGHT_BROWSERS_PATH"] = str(pw_path)
        cmd = [str(node_exe), str(cli_js), "install", "chromium"]
        result = subprocess.run(cmd, env=env, capture_output=True, text=True)
        if result.returncode == 0:
            print("[launcher] Chromium installed successfully")
        else:
            print(f"[launcher] Chromium install failed (exit={result.returncode})")
            print(result.stderr[-500:] if result.stderr else "")
    except Exception as e:
        print(f"[launcher] chromium install error: {e}")


def _open_browser_later(url: str, delay: float = 1.5):
    def _run():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=_run, daemon=True).start()


def run_ui():
    """启动 FastAPI 配置 UI（端口 8020）+ 自动打开浏览器"""
    ensure_data_dirs(seed_example_config=True)
    chdir_to_data()
    _ensure_chromium()

    # import 即注册 routes
    import uvicorn
    from scripts.dy_config_ui import app

    print(f"[launcher] APP_DATA = {APP_DATA}")
    print(f"[launcher] Config UI: http://127.0.0.1:8020")
    _open_browser_later("http://127.0.0.1:8020", delay=1.5)
    uvicorn.run(app, host="127.0.0.1", port=8020, log_level="info")


def run_main():
    """运行主回复脚本（被 UI 通过 subprocess 调起）"""
    _hide_from_dock()
    ensure_data_dirs(seed_example_config=True)
    chdir_to_data()
    _ensure_chromium()
    # dy_auto_reply 内部用 argparse，需要清空 launcher 的参数
    sys.argv = ["dy_auto_reply"]
    from scripts.dy_auto_reply import main as _main
    asyncio.run(_main())


def run_login(account: str):
    """交互式扫码登录"""
    _hide_from_dock()
    ensure_data_dirs(seed_example_config=True)
    chdir_to_data()
    _ensure_chromium()
    # 模拟 dy_auto_reply.py CLI: --login --account NAME
    sys.argv = ["dy_auto_reply", "--login", "--account", account]
    from scripts.dy_auto_reply import main as _main
    asyncio.run(_main())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["ui", "main", "login"], default="ui")
    parser.add_argument("--account", default="")
    args, _ = parser.parse_known_args()

    if args.mode == "main":
        run_main()
    elif args.mode == "login":
        run_login(args.account)
    else:
        run_ui()


if __name__ == "__main__":
    main()
