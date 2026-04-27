"""统一应用数据路径

打包模式（PyInstaller / .app）：使用用户可写的应用支持目录
- macOS: ~/Library/Application Support/dy_auto_reply/
- Windows: %APPDATA%/dy_auto_reply/
- Linux:  ~/.config/dy_auto_reply/

开发模式：使用项目根目录（保持兼容现有 git checkout 工作流）

启动时调用 ensure_data_dirs() + chdir 到 APP_DATA，则所有相对路径
（"data/..."、"config/..."、"logs/..."）会自然指向应用数据目录。
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否为 PyInstaller 打包后的环境"""
    return getattr(sys, "frozen", False)


def _user_data_dir(app_name: str) -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / app_name
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / app_name
    return Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / app_name


APP_NAME = "dy_auto_reply"

if is_frozen():
    APP_DATA: Path = _user_data_dir(APP_NAME)
else:
    # 开发模式：项目根（这个文件位于 shared/app_paths.py）
    APP_DATA = Path(__file__).resolve().parent.parent


def ensure_data_dirs(seed_example_config: bool = True) -> None:
    """打包模式下首次启动时初始化必需目录。开发模式无操作。"""
    if not is_frozen():
        return

    for sub in ("config", "data", "logs", "data/cards", "data/materials",
                "data/browser_profiles", "data/browser_state"):
        (APP_DATA / sub).mkdir(parents=True, exist_ok=True)

    if seed_example_config:
        cfg = APP_DATA / "config" / "dy_reply.yaml"
        example = APP_DATA / "config" / "dy_reply.example.yaml"
        bundle_example = _bundle_resource("config/dy_reply.example.yaml")
        if bundle_example and bundle_example.exists():
            if not example.exists():
                shutil.copyfile(bundle_example, example)
            if not cfg.exists():
                shutil.copyfile(bundle_example, cfg)


def _bundle_resource(rel: str) -> Path | None:
    """打包后只读资源（PyInstaller 解压目录），开发模式返回项目根下路径"""
    if is_frozen():
        base = Path(getattr(sys, "_MEIPASS", ""))
        if not base:
            return None
        return base / rel
    return Path(__file__).resolve().parent.parent / rel


def chdir_to_data() -> None:
    """切到 APP_DATA，让现有代码里所有相对路径自然指向数据目录"""
    APP_DATA.mkdir(parents=True, exist_ok=True)
    os.chdir(APP_DATA)
