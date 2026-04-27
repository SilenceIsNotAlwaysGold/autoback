# 抖音自动回复 — Windows 打包仓库

这是从 macOS 主仓库精简出的 Windows 打包子集，仅用于 GitHub Actions 在 Windows runner 上自动构建 .exe。

## 触发构建

- **自动**：push 到 `main` / `master` 分支会自动跑
- **手动**：去 [Actions](../../actions) 页面 → "Build Windows EXE" → 「Run workflow」

## 下载产物

构建成功后：
1. 进入对应的 Actions Run 页面
2. 滚到底部 `Artifacts` 区域
3. 下载 `dy_auto_reply-windows.zip`（解压一次得到分发用的 zip）

## 本地构建（如果你有 Windows 机器）

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-build.txt
pyinstaller dy_auto_reply_win.spec --clean --noconfirm
```

输出在 `dist\dy_auto_reply\` 目录。

## 项目结构

```
launcher_app.py              ← PyInstaller 入口
dy_auto_reply_win.spec       ← Windows spec
dy_auto_reply.spec           ← macOS spec（这里保留只是备份）
requirements-build.txt       ← 打包依赖
config/dy_reply.example.yaml ← 默认配置模板
scripts/                     ← 主脚本 + UI
platforms/                   ← 浏览器引擎 + 抖音平台代码
shared/                      ← 路径管理 + 规则引擎 + AI
.github/workflows/           ← CI 配置
```

## 文档

- `使用说明.txt` — 给最终用户的快速上手
- `教程-Windows.md` — 详细使用教程
