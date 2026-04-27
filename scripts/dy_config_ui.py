"""抖音自动回复 · 本地配置 UI

用法：
    python scripts/dy_config_ui.py
    # 浏览器打开 http://localhost:8020

功能：
- 读写 config/dy_reply.yaml
- 可视化管理关键词规则（增/删/改）
- 支持多条回复、匹配模式、图片、默认兜底

注意：
- 保存后主脚本 dy_auto_reply.py 需要重启才能生效
  （或者等下一轮 120s 内自动重读 YAML —— 当前脚本每轮都重新解析）
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import subprocess
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import uvicorn

# 数据目录：打包模式 = APP_DATA，开发模式 = 项目根（与 ROOT 相同）
try:
    from shared.app_paths import APP_DATA as _DATA_ROOT
except Exception:
    _DATA_ROOT = ROOT

CONFIG_PATH = _DATA_ROOT / "config" / "dy_reply.yaml"
EXAMPLE_PATH = _DATA_ROOT / "config" / "dy_reply.example.yaml"


app = FastAPI(title="DY Reply Config")


class Rule(BaseModel):
    keywords: str = ""
    match_mode: str = "contains"   # contains / exact / regex
    reply_texts: list[str] = []    # 多条随机
    reply_image: str = ""
    reply_text_after: str = ""
    is_default: bool = False


class RulesPayload(BaseModel):
    rules: list[Rule]


class Account(BaseModel):
    name: str
    login_timeout: int = 180
    proxy_url: str = ""
    bitbrowser_id: str = ""


class AccountsPayload(BaseModel):
    accounts: list[Account]


class ReplyModePayload(BaseModel):
    keyword_enabled: bool = True
    brainless_enabled: bool = False
    brainless_reply_texts: list[str] = []
    # 兼容旧字段
    mode: str | None = None


def load_config() -> dict:
    path = CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_PATH
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_config(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False, indent=2)


def normalize_rule(r: dict) -> dict:
    """把表单 rule 转成 YAML 格式：reply_texts 只有 1 条时转为 reply_text"""
    out: dict = {}
    if r.get("is_default"):
        out["is_default"] = True
    else:
        if r.get("keywords"):
            out["keywords"] = r["keywords"]
        if r.get("match_mode") and r["match_mode"] != "contains":
            out["match_mode"] = r["match_mode"]

    texts = [t for t in (r.get("reply_texts") or []) if t]
    if len(texts) == 1:
        out["reply_text"] = texts[0]
    elif len(texts) > 1:
        out["reply_texts"] = texts

    if r.get("reply_image"):
        out["reply_image"] = r["reply_image"]
    if r.get("reply_text_after"):
        out["reply_text_after"] = r["reply_text_after"]

    return out


def denormalize_rule(r: dict) -> dict:
    """YAML → 表单格式：reply_text / reply_texts 统一为数组"""
    texts: list[str] = []
    if r.get("reply_texts"):
        texts = list(r["reply_texts"])
    elif r.get("reply_text"):
        texts = [r["reply_text"]]
    return {
        "keywords": r.get("keywords", ""),
        "match_mode": r.get("match_mode", "contains"),
        "reply_texts": texts,
        "reply_image": r.get("reply_image", ""),
        "reply_text_after": r.get("reply_text_after", ""),
        "is_default": bool(r.get("is_default")),
    }


# ── API ──────────────────────────────────────────────────

@app.get("/api/rules")
def api_get_rules():
    cfg = load_config()
    rules = cfg.get("rules", [])
    return {"rules": [denormalize_rule(r) for r in rules]}


@app.post("/api/rules")
def api_save_rules(payload: RulesPayload):
    try:
        cfg = load_config()
        cfg["rules"] = [normalize_rule(r.model_dump()) for r in payload.rules]
        save_config(cfg)
        return {"ok": True, "count": len(cfg["rules"]), "path": str(CONFIG_PATH)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/reply-mode")
def api_get_reply_mode():
    cfg = load_config()
    # 兼容旧字段 reply_mode
    if "keyword_enabled" in cfg or "brainless_enabled" in cfg:
        kw = bool(cfg.get("keyword_enabled", True))
        br = bool(cfg.get("brainless_enabled", False))
    else:
        old = cfg.get("reply_mode", "keyword")
        kw = (old == "keyword")
        br = (old == "brainless")
    return {
        "keyword_enabled": kw,
        "brainless_enabled": br,
        "brainless_reply_texts": cfg.get("brainless_reply_texts", []),
    }


@app.post("/api/reply-mode")
def api_save_reply_mode(payload: ReplyModePayload):
    cfg = load_config()
    cfg["keyword_enabled"] = payload.keyword_enabled
    cfg["brainless_enabled"] = payload.brainless_enabled
    cfg["brainless_reply_texts"] = [t for t in payload.brainless_reply_texts if t]
    # 清理旧字段
    cfg.pop("reply_mode", None)
    save_config(cfg)
    return {
        "ok": True,
        "keyword_enabled": payload.keyword_enabled,
        "brainless_enabled": payload.brainless_enabled,
        "brainless_count": len(cfg["brainless_reply_texts"]),
    }


@app.get("/api/meta")
def api_meta():
    """其它配置摘要（只读展示）"""
    cfg = load_config()
    return {
        "accounts": [a.get("name") for a in cfg.get("accounts", [])],
        "messenger_enabled": cfg.get("messenger", {}).get("enabled"),
        "commenter_enabled": cfg.get("commenter", {}).get("enabled"),
        "ai_enabled": cfg.get("ai", {}).get("enabled"),
        "config_path": str(CONFIG_PATH),
    }


# ── 账号管理 API ────────────────────────────────────

@app.get("/api/accounts")
def api_get_accounts():
    cfg = load_config()
    accs = cfg.get("accounts", [])
    return {"accounts": [
        {
            "name": a.get("name", ""),
            "login_timeout": a.get("login_timeout", 180),
            "proxy_url": a.get("proxy_url", ""),
            "bitbrowser_id": a.get("bitbrowser_id", ""),
        }
        for a in accs
    ]}


@app.post("/api/accounts")
def api_save_accounts(payload: AccountsPayload):
    # 检查重名
    names = [a.name for a in payload.accounts]
    if len(names) != len(set(names)):
        raise HTTPException(status_code=400, detail="账号名重复")
    # 检查非法字符（作为 profile 目录名）
    for n in names:
        if not n or any(c in n for c in '/\\:*?"<>|'):
            raise HTTPException(status_code=400, detail=f"非法账号名：{n}")
    cfg = load_config()
    cfg["accounts"] = [
        # 去掉空字段，保持 YAML 干净
        {k: v for k, v in a.model_dump().items() if v not in ("", None)}
        for a in payload.accounts
    ]
    save_config(cfg)
    return {"ok": True, "count": len(cfg["accounts"])}


# 进程跟踪：每个登录任务存 { account -> Popen }
_login_procs: dict[str, subprocess.Popen] = {}
_login_logfs: dict[str, object] = {}   # 对应 logf 句柄，进程结束后关闭


def _spawn_login(name: str, acc: dict) -> tuple[bool, str, int]:
    """启动单个账号的登录子进程，返回 (ok, message, pid)"""
    existing = _login_procs.get(name)
    if existing and existing.poll() is None:
        return False, "已在登录中", existing.pid

    proxy = (acc.get("proxy_url") or "").strip()
    if proxy and any(p in proxy for p in ("1.2.3.4", "user:pass@", "example.com")):
        return False, f"代理占位符：{proxy}", 0

    log_dir = _DATA_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"login_{name}.log"

    main_script = ROOT / "scripts" / "dy_auto_reply.py"
    try:
        # 关闭上一次的 logf（若存在）
        old_logf = _login_logfs.pop(name, None)
        if old_logf:
            try: old_logf.close()
            except Exception: pass
        logf = open(log_file, "w", buffering=1)
        # 打包模式：复用主 exe + --mode=login；开发模式：调 python 脚本
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--mode=login", "--account", name]
            cwd = None
        else:
            cmd = [sys.executable, "-u", str(main_script), "--login", "--account", name]
            cwd = str(ROOT)
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=logf, stderr=subprocess.STDOUT,
        )
        _login_procs[name] = proc
        _login_logfs[name] = logf
        return True, "已启动", proc.pid
    except Exception as e:
        return False, str(e), 0


@app.post("/api/proxy/test")
def api_test_proxy(payload: dict):
    """测试代理是否可用：通过代理访问 httpbin.org/ip 返回出口 IP

    Request: {"proxy_url": "http://user:pass@ip:port"}
    Response: {"ok": true, "ip": "...", "latency_ms": 234} 或错误
    """
    import time as _t
    import httpx
    proxy_url = (payload.get("proxy_url") or "").strip()
    if not proxy_url:
        return {"ok": False, "message": "未配置代理（将走本机 IP）"}
    # 多个探测地址，任一成功即可（国内环境 ipify/httpbin 时常超时）
    probes = [
        ("http://ip-api.com/json/", lambda j: j.get("query")),
        ("https://ip.useragentinfo.com/json", lambda j: j.get("ip")),
        ("http://httpbin.org/ip", lambda j: j.get("origin", "").split(",")[0].strip()),
        ("https://api.ipify.org?format=json", lambda j: j.get("ip")),
    ]
    last_err = None
    t0 = _t.monotonic()
    for url, parser in probes:
        try:
            with httpx.Client(proxy=proxy_url, timeout=6, verify=False) as client:
                resp = client.get(url)
                resp.raise_for_status()
                ip = parser(resp.json())
                if ip:
                    ms = int((_t.monotonic() - t0) * 1000)
                    return {
                        "ok": True, "ip": ip, "latency_ms": ms,
                        "proxy": proxy_url.split("@")[-1] if "@" in proxy_url else proxy_url,
                        "probe": url,
                    }
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            continue
    return {"ok": False, "message": f"所有探测点均失败。最后错误：{last_err}"}


class BulkProxyPayload(BaseModel):
    proxies: list[str]       # 每行一个，顺序对应 accounts


@app.post("/api/accounts/bulk-proxy")
def api_bulk_proxy(payload: BulkProxyPayload):
    """批量把代理列表分配给账号（按顺序）"""
    cfg = load_config()
    accs = cfg.get("accounts", [])
    proxies = [p.strip() for p in payload.proxies if p.strip()]
    updated = 0
    for i, acc in enumerate(accs):
        if i >= len(proxies):
            break
        acc["proxy_url"] = proxies[i]
        updated += 1
    cfg["accounts"] = accs
    save_config(cfg)
    return {"ok": True, "updated": updated, "total_accounts": len(accs)}


# ── 主脚本生命周期 ─────────────────────────────────

_main_proc: subprocess.Popen | None = None
_main_logf: object = None          # 主脚本 logf 句柄
_main_log_path = _DATA_ROOT / "logs" / "dy_main.log"


@app.get("/api/main/status")
def api_main_status():
    """返回主脚本运行状态 + 最近日志"""
    global _main_proc
    running = _main_proc is not None and _main_proc.poll() is None
    pid = _main_proc.pid if running else None
    # 最近 30 行日志
    tail: list[str] = []
    if _main_log_path.exists():
        try:
            with open(_main_log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            tail = [l.rstrip("\n") for l in lines[-30:]]
        except Exception:
            pass
    return {"running": running, "pid": pid, "log_tail": tail}


@app.post("/api/main/start")
def api_main_start():
    global _main_proc, _main_logf
    if _main_proc and _main_proc.poll() is None:
        return {"ok": False, "message": f"主脚本已在运行 PID={_main_proc.pid}"}
    main_script = ROOT / "scripts" / "dy_auto_reply.py"
    try:
        _main_log_path.parent.mkdir(exist_ok=True)
        # 关闭上一次的 logf
        if _main_logf:
            try: _main_logf.close()
            except Exception: pass
        logf = open(_main_log_path, "w", buffering=1)
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--mode=main"]
            cwd = None
        else:
            cmd = [sys.executable, "-u", str(main_script)]
            cwd = str(ROOT)
        _main_proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=logf, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _main_logf = logf
        return {"ok": True, "pid": _main_proc.pid, "message": "主脚本已启动"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/main/stop")
def api_main_stop():
    global _main_proc, _main_logf
    import os, signal as _sig
    if not _main_proc or _main_proc.poll() is not None:
        _main_proc = None
        return {"ok": True, "message": "主脚本未在运行"}
    try:
        # Unix: 终止整个进程组（含子进程）；Windows 无 killpg，直接 terminate
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(_main_proc.pid), _sig.SIGTERM)
        else:
            _main_proc.terminate()
        _main_proc.wait(timeout=5)
    except Exception:
        try: _main_proc.kill()
        except Exception: pass
    finally:
        if _main_logf:
            try: _main_logf.close()
            except Exception: pass
            _main_logf = None
    _main_proc = None
    return {"ok": True, "message": "已停止"}


@app.post("/api/accounts/login-all")
def api_login_all():
    """批量登录：所有账号并发启动各自的登录子进程（每个独立 profile，不冲突）"""
    cfg = load_config()
    results = []
    for acc in cfg.get("accounts", []):
        name = acc.get("name", "")
        if not name:
            continue
        ok, msg, pid = _spawn_login(name, acc)
        results.append({"name": name, "ok": ok, "message": msg, "pid": pid})
    return {"started": sum(1 for r in results if r["ok"]), "results": results}


@app.post("/api/accounts/{name}/login")
def api_login_account(name: str):
    """启动 --login 流程，弹浏览器扫码"""
    cfg = load_config()
    acc = next((a for a in cfg.get("accounts", []) if a.get("name") == name), None)
    if not acc:
        raise HTTPException(status_code=404, detail=f"账号 {name} 不存在，先添加保存")
    ok, msg, pid = _spawn_login(name, acc)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    return {"ok": True, "message": f"浏览器窗口即将弹出，请扫码 {name}", "pid": pid}


@app.get("/api/accounts/{name}/login/status")
def api_login_status(name: str):
    proc = _login_procs.get(name)
    log_file = _DATA_ROOT / "logs" / f"login_{name}.log"
    # 返回最后 50 行日志（若有）
    tail_lines: list[str] = []
    if log_file.exists():
        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            tail_lines = [l.rstrip("\n") for l in lines[-50:]]
        except Exception:
            pass

    if not proc:
        return {"state": "idle", "log_tail": tail_lines}
    if proc.poll() is None:
        return {"state": "running", "pid": proc.pid, "log_tail": tail_lines}
    return {"state": "done", "exit_code": proc.returncode, "log_tail": tail_lines}


# ── HTML 页面 ────────────────────────────────────────────

HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>抖音自动回复 · 规则配置</title>
<style>
  :root {
    --bg: #0f172a;
    --panel: #1e293b;
    --border: #334155;
    --text: #e2e8f0;
    --muted: #94a3b8;
    --accent: #3b82f6;
    --danger: #ef4444;
    --success: #10b981;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 24px 16px 60px;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif;
    font-size: 14px;
    line-height: 1.5;
  }
  .container { max-width: 880px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 20px; }
  .meta {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 12px 16px;
    margin-bottom: 20px;
    font-size: 13px;
    color: var(--muted);
  }
  .meta code { color: var(--text); }
  .toolbar { display: flex; gap: 12px; align-items: center; margin-bottom: 16px; }
  button {
    background: var(--accent);
    color: white;
    border: 0;
    padding: 8px 16px;
    border-radius: 6px;
    font-size: 13px;
    cursor: pointer;
  }
  button:hover { opacity: 0.9; }
  button.secondary { background: transparent; border: 1px solid var(--border); color: var(--text); }
  button.danger { background: var(--danger); }
  button.small { padding: 4px 10px; font-size: 12px; }
  /* Tab 导航 */
  .tabs { display: flex; gap: 4px; border-bottom: 2px solid var(--border); margin-bottom: 18px; }
  .tab { padding: 10px 22px; cursor: pointer; color: var(--muted); font-size: 14px;
         border-bottom: 2px solid transparent; margin-bottom: -2px; }
  .tab.active { color: var(--accent); border-color: var(--accent); font-weight: 600; }
  .tab:hover { color: var(--text); }
  .panel { display: none; }
  .panel.active { display: block; }
  /* 无脑模式切换 */
  .mode-switch {
    background: var(--panel); border: 1px solid var(--border); border-radius: 8px;
    padding: 16px 18px; margin-bottom: 14px;
  }
  .mode-switch-header {
    display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;
  }
  .mode-switch-title { font-weight: 600; font-size: 15px; }
  .mode-switch-hint { color: var(--muted); font-size: 12px; margin-top: 4px; }
  .toggle {
    position: relative; display: inline-block; width: 50px; height: 26px;
  }
  .toggle input { opacity: 0; width: 0; height: 0; }
  .slider {
    position: absolute; cursor: pointer; top:0; left:0; right:0; bottom:0;
    background: #475569; border-radius: 26px; transition: 0.2s;
  }
  .slider:before {
    content: ""; position: absolute; height: 20px; width: 20px; left: 3px; bottom: 3px;
    background: white; border-radius: 50%; transition: 0.2s;
  }
  input:checked + .slider { background: var(--accent); }
  input:checked + .slider:before { transform: translateX(24px); }
  .rule {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 16px;
    margin-bottom: 12px;
  }
  .rule.default { border-color: #f59e0b; }
  .rule-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 10px;
  }
  .rule-title { font-weight: 600; }
  .rule-title.default { color: #f59e0b; }
  .field { margin-bottom: 10px; }
  .field-label {
    color: var(--muted);
    font-size: 12px;
    margin-bottom: 4px;
  }
  input[type="text"], select, textarea {
    width: 100%;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 6px 10px;
    border-radius: 4px;
    font-family: inherit;
    font-size: 13px;
  }
  textarea { resize: vertical; min-height: 40px; }
  .reply-row {
    display: flex;
    gap: 8px;
    margin-bottom: 6px;
  }
  .reply-row input { flex: 1; }
  .row-inline { display: flex; gap: 12px; }
  .row-inline .field { flex: 1; }
  .saved-toast {
    position: fixed;
    bottom: 24px;
    right: 24px;
    background: var(--success);
    color: white;
    padding: 10px 18px;
    border-radius: 6px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
    display: none;
  }
  .saved-toast.show { display: block; }
  .saved-toast.err { background: var(--danger); }
  .hint { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .default-badge {
    background: #f59e0b;
    color: #1e293b;
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 10px;
    font-weight: 600;
    margin-left: 8px;
  }
</style>
</head>
<body>
<div class="container">
  <h1>抖音自动回复 · 规则配置</h1>
  <div class="sub">编辑关键词 → 命中规则 → 自动回复。保存后主脚本下一轮（60s内）生效。</div>

  <div class="meta" id="meta">加载中...</div>

  <!-- 运行控制台 -->
  <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:14px 16px;margin-bottom:16px">
    <div style="display:flex;align-items:center;gap:12px">
      <div style="flex:1">
        <div style="font-weight:600;font-size:15px">
          🎯 主脚本状态: <span id="main-status" style="color:#94a3b8">加载中…</span>
        </div>
        <div style="color:var(--muted);font-size:12px;margin-top:2px">
          启动后所有账号开始实时监听私信，收到关键词消息自动回复。
        </div>
      </div>
      <button id="main-start-btn" style="background:#10b981;color:white;padding:10px 22px;font-size:14px;font-weight:700" onclick="startMain()">▶ 启动监听</button>
      <button id="main-stop-btn" style="background:#ef4444;color:white;padding:10px 22px;font-size:14px;font-weight:700;display:none" onclick="stopMain()">⏹ 停止</button>
      <button class="secondary" onclick="toggleMainLog()">📝 日志</button>
    </div>
    <div id="main-log-panel" style="display:none;margin-top:10px;background:#0f172a;color:#e2e8f0;border-radius:6px;padding:10px;font-family:Menlo,monospace;font-size:11px;height:200px;overflow-y:auto;white-space:pre-wrap"></div>
  </div>

  <!-- Tab 导航 -->
  <div class="tabs">
    <div class="tab active" data-tab="accounts" onclick="switchTab('accounts')">📱 账号管理</div>
    <div class="tab" data-tab="rules" onclick="switchTab('rules')">💬 回复规则管理</div>
  </div>

  <!-- 账号管理面板 -->
  <div class="panel active" id="panel-accounts">
    <div class="toolbar">
      <button onclick="addAccount()">+ 添加账号</button>
      <button style="background:#8b5cf6;color:white" onclick="bulkAddAccounts()">➕ 批量添加</button>
      <button onclick="saveAccounts()">💾 保存账号</button>
      <button style="background:#10b981;color:white" onclick="showBulkProxy()">🧩 批量填代理</button>
      <button style="background:#06b6d4;color:white" onclick="loginAll()">🚀 批量登录全部</button>
      <button class="secondary" onclick="loadAccounts()">↻ 重新加载</button>
    </div>
    <div id="accounts-container"></div>
  </div>

  <!-- 规则管理面板 -->
  <div class="panel" id="panel-rules">
    <!-- 两个独立开关 -->
    <div class="mode-switch">
      <!-- 关键词匹配开关 -->
      <div class="mode-switch-header" style="padding-bottom:10px;border-bottom:1px solid var(--border);margin-bottom:10px">
        <div style="flex:1">
          <div class="mode-switch-title">
            🔑 关键词匹配
            <span style="font-size:11px;color:var(--muted);margin-left:8px">(优先级：高)</span>
          </div>
          <div class="mode-switch-hint">消息命中关键词规则时自动回复。未命中时 → 看无脑是否开启。</div>
        </div>
        <label class="toggle">
          <input type="checkbox" id="keywordToggle" onchange="onKwToggle()">
          <span class="slider"></span>
        </label>
      </div>

      <!-- 无脑模式开关 -->
      <div class="mode-switch-header">
        <div style="flex:1">
          <div class="mode-switch-title">
            🤖 无脑兜底回复
            <span style="font-size:11px;color:var(--muted);margin-left:8px">(优先级：低 · 关键词未命中时兜底)</span>
          </div>
          <div class="mode-switch-hint">任何消息只要关键词未命中 → 从下面文案随机选一条回复。</div>
        </div>
        <label class="toggle">
          <input type="checkbox" id="brainlessToggle" onchange="onBrToggle()">
          <span class="slider"></span>
        </label>
      </div>
      <div id="brainless-body" style="display:none;margin-top:10px">
        <div class="field-label">无脑兜底文案（多条随机选一）</div>
        <div id="brainless-texts"></div>
        <div style="margin-top:6px">
          <button class="secondary small" onclick="addBrainlessText()">+ 添加一条</button>
          <span id="brainless-saved-hint" style="color:var(--muted);font-size:12px;margin-left:8px"></span>
        </div>
      </div>

      <!-- 统一保存按钮 -->
      <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--border)">
        <button style="background:var(--accent)" onclick="saveMode()">💾 保存开关设置</button>
        <span id="mode-summary" style="color:var(--muted);font-size:12px;margin-left:10px"></span>
      </div>
    </div>

    <!-- 状态 banner -->
    <div id="mode-banner" style="display:none;padding:10px 14px;border-radius:6px;margin-bottom:14px;font-size:13px"></div>

    <!-- 关键词规则 -->
    <div id="keyword-section">
      <div class="toolbar">
        <button onclick="addRule(false)">+ 添加关键词规则</button>
        <button class="secondary" onclick="addRule(true)">+ 添加兜底规则</button>
        <button onclick="save()">💾 保存规则</button>
        <button class="secondary" onclick="load()">↻ 重新加载</button>
      </div>
      <div id="rules-container"></div>
    </div>
  </div>
</div>

<div id="toast" class="saved-toast"></div>

<script>
let rules = [];
let brainlessTexts = [];
let keywordEnabled = true;
let brainlessEnabled = false;

// ── Tab 切换 ────────────────────────────
function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.panel').forEach(p => p.classList.toggle('active', p.id === 'panel-' + name));
}

// ── 主脚本生命周期 ──────────────────────
async function refreshMainStatus() {
  try {
    const r = await fetch('/api/main/status').then(r => r.json());
    const el = document.getElementById('main-status');
    const start = document.getElementById('main-start-btn');
    const stop = document.getElementById('main-stop-btn');
    if (r.running) {
      el.innerHTML = '<span style="color:#10b981">● 运行中 (PID ' + r.pid + ')</span>';
      start.style.display = 'none';
      stop.style.display = 'inline-block';
    } else {
      el.innerHTML = '<span style="color:#9ca3af">● 已停止</span>';
      start.style.display = 'inline-block';
      stop.style.display = 'none';
    }
    // 更新日志面板（如果打开着）
    const panel = document.getElementById('main-log-panel');
    if (panel.style.display !== 'none') {
      panel.textContent = (r.log_tail || []).join('\n') || '(暂无日志)';
      panel.scrollTop = panel.scrollHeight;
    }
  } catch (e) {}
}

async function startMain() {
  const r = await fetch('/api/main/start', { method: 'POST' }).then(r => r.json());
  if (r.ok) {
    toast('🚀 主脚本已启动，开始监听');
    refreshMainStatus();
  } else {
    alert('启动失败: ' + (r.detail || r.message));
  }
}

async function stopMain() {
  if (!confirm('确定停止所有账号监听？')) return;
  const r = await fetch('/api/main/stop', { method: 'POST' }).then(r => r.json());
  toast(r.ok ? '⏹ 已停止' : ('❌ ' + (r.detail || r.message)), !r.ok);
  refreshMainStatus();
}

function toggleMainLog() {
  const p = document.getElementById('main-log-panel');
  p.style.display = p.style.display === 'none' ? 'block' : 'none';
  if (p.style.display !== 'none') refreshMainStatus();
}

async function load() {
  const [rulesRes, metaRes, modeRes] = await Promise.all([
    fetch('/api/rules').then(r => r.json()),
    fetch('/api/meta').then(r => r.json()),
    fetch('/api/reply-mode').then(r => r.json()),
  ]);
  rules = rulesRes.rules || [];
  keywordEnabled = !!modeRes.keyword_enabled;
  brainlessEnabled = !!modeRes.brainless_enabled;
  brainlessTexts = modeRes.brainless_reply_texts || [];

  document.getElementById('keywordToggle').checked = keywordEnabled;
  document.getElementById('brainlessToggle').checked = brainlessEnabled;
  document.getElementById('brainless-body').style.display = brainlessEnabled ? 'block' : 'none';
  document.getElementById('keyword-section').style.opacity = keywordEnabled ? '1' : '0.4';

  updateBanner();
  renderBrainless();
  const savedHint = document.getElementById('brainless-saved-hint');
  if (savedHint) {
    const n = (brainlessTexts || []).filter(t => t && t.trim()).length;
    savedHint.textContent = n > 0 ? `· 已保存 ${n} 条` : '';
  }
  document.getElementById('meta').innerHTML = `
    账号: <code>${(metaRes.accounts || []).join(', ') || '（未配置）'}</code> ·
    私信: <code>${metaRes.messenger_enabled ? '✅' : '❌'}</code> ·
    评论: <code>${metaRes.commenter_enabled ? '✅' : '❌'}</code> ·
    AI: <code>${metaRes.ai_enabled ? '✅' : '❌'}</code><br>
    <span style="font-size:11px">配置文件: ${metaRes.config_path}</span>
  `;
  render();
  loadAccounts();
}

// ── 账号管理 ────────────────────────────
let accounts = [];

async function loadAccounts() {
  const r = await fetch('/api/accounts').then(r => r.json());
  accounts = r.accounts || [];
  renderAccounts();
}

function renderAccounts() {
  const c = document.getElementById('accounts-container');
  c.innerHTML = '';
  if (accounts.length === 0) {
    c.innerHTML = '<div style="color:var(--muted);text-align:center;padding:30px;background:var(--panel);border-radius:8px">（尚未配置任何账号，点「+ 添加账号」开始）</div>';
    return;
  }
  accounts.forEach((a, i) => {
    const div = document.createElement('div');
    div.className = 'rule';
    div.innerHTML = accountHTML(a, i);
    c.appendChild(div);
  });
}

function accountHTML(a, i) {
  return `
    <div class="rule-header">
      <div class="rule-title">账号 #${i + 1} — ${esc(a.name || '(未命名)')}</div>
      <div style="display:flex;gap:6px">
        <button class="small" style="background:#06b6d4" onclick="loginAccount(${i})">🔑 扫码登录</button>
        <button class="secondary small" onclick="viewLoginLog('${esc(a.name)}')">📝 登录日志</button>
        <button class="secondary small" onclick="removeAccount(${i})">🗑 删除</button>
      </div>
    </div>
    <div class="row-inline">
      <div class="field" style="flex:2">
        <div class="field-label">账号名（profile 目录名，字母数字_-）</div>
        <input type="text" value="${esc(a.name)}" oninput="updateAcc(${i},'name',this.value)" placeholder="例：dy_acc01">
      </div>
      <div class="field" style="flex:1">
        <div class="field-label">登录超时（秒）</div>
        <input type="text" value="${a.login_timeout || 180}" oninput="updateAcc(${i},'login_timeout',parseInt(this.value)||180)">
      </div>
    </div>
    <div class="field">
      <div class="field-label">
        代理 URL（可选，留空=本机 IP）
        <button class="small" style="margin-left:8px;background:#10b981;color:white;padding:2px 8px;font-size:11px" onclick="testProxy(${i})">🧪 测试</button>
      </div>
      <input type="text" value="${esc(a.proxy_url)}" oninput="updateAcc(${i},'proxy_url',this.value)" placeholder="socks5://用户名:密码@IP:端口 或 http://...">
    </div>
    <div class="field" style="${a.bitbrowser_id ? '' : 'display:none'}" id="bb-${i}">
      <div class="field-label">
        比特浏览器窗口 ID
        <span style="font-size:11px;color:var(--muted);margin-left:6px">(高级：用专业指纹浏览器替代本地)</span>
      </div>
      <input type="text" value="${esc(a.bitbrowser_id)}" oninput="updateAcc(${i},'bitbrowser_id',this.value)" placeholder="比特浏览器窗口 ID">
    </div>
    ${a.bitbrowser_id ? '' : `
    <div style="margin-top:4px">
      <a href="javascript:void(0)" onclick="document.getElementById('bb-${i}').style.display='block';this.style.display='none'"
         style="font-size:11px;color:var(--muted);text-decoration:underline">⚙ 高级：接入比特浏览器</a>
    </div>
    `}
  `;
}

function addAccount() {
  const n = prompt('新账号名（字母/数字/下划线/短横，如 dy_acc01）');
  if (!n) return;
  if (accounts.some(a => a.name === n)) { alert('重名了'); return; }
  accounts.push({ name: n, login_timeout: 180, proxy_url: '', bitbrowser_id: '' });
  renderAccounts();
}

function bulkAddAccounts() {
  const input = prompt(
    `批量添加账号（2 种方式）：

① 输入数字 N → 按 dy_acc01 ~ dy_acc${'${'}N${'}'} 自动生成
   例如：20 → 生成 dy_acc01...dy_acc20

② 每行一个账号名 → 按填的名字生成
   例如：
     shop_main
     shop_clone_1
     shop_vip

当前已有 ${accounts.length} 个账号`,
    ''
  );
  if (!input) return;
  const trimmed = input.trim();
  let newNames = [];
  if (/^\\d+$/.test(trimmed)) {
    // 数字 → 按序号生成
    const n = parseInt(trimmed);
    if (n < 1 || n > 100) { alert('范围 1~100'); return; }
    // 从当前账号数+1 开始编号，避开现有名字
    const existing = new Set(accounts.map(a => a.name));
    let idx = 1;
    while (newNames.length < n) {
      const cand = 'dy_acc' + String(idx).padStart(2, '0');
      if (!existing.has(cand)) newNames.push(cand);
      idx++;
    }
  } else {
    // 列表 → 逐个用
    newNames = trimmed.split(/\\n/).map(s => s.trim()).filter(Boolean);
    const existing = new Set(accounts.map(a => a.name));
    newNames = newNames.filter(n => !existing.has(n));
  }
  if (!newNames.length) { alert('没新增（或都已存在）'); return; }
  newNames.forEach(n => {
    accounts.push({ name: n, login_timeout: 180, proxy_url: '', bitbrowser_id: '' });
  });
  renderAccounts();
  toast(`✅ 新增 ${newNames.length} 个账号（记得 💾 保存）`);
}

function updateAcc(i, k, v) { accounts[i][k] = v; }

function removeAccount(i) {
  if (!confirm(`确定删除账号「${accounts[i].name}」？`)) return;
  accounts.splice(i, 1);
  renderAccounts();
}

async function saveAccounts() {
  try {
    const r = await fetch('/api/accounts', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ accounts }),
    }).then(r => r.json());
    toast(r.ok ? `✅ 已保存 ${r.count} 个账号` : ('❌ ' + (r.detail || r.message)), !r.ok);
  } catch (e) { toast('❌ ' + e.message, true); }
}

async function loginAccount(i) {
  const acc = accounts[i];
  if (!acc.name) { alert('请先填账号名并保存'); return; }
  // 先保存一次避免用户忘记
  await saveAccounts();
  const res = await fetch(`/api/accounts/${encodeURIComponent(acc.name)}/login`, { method: 'POST' });
  let r;
  try { r = await res.json(); } catch (e) { r = { ok: false, detail: '解析响应失败' }; }
  if (r.ok) {
    toast(`🔑 ${r.message}，请扫码（看屏幕上弹出的浏览器窗口）`);
    // 15 秒后拉一次日志
    setTimeout(() => viewLoginLog(acc.name, true), 15000);
  } else {
    alert('❌ 启动登录失败：\n\n' + (r.detail || r.message));
  }
}

async function testProxy(i) {
  const acc = accounts[i];
  const proxy = acc.proxy_url || '';
  if (!proxy) {
    alert('该账号没配代理，走本机 IP');
    return;
  }
  // 提示测试中
  toast('🧪 正在测试 ' + acc.name + ' 的代理...');
  const r = await fetch('/api/proxy/test', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ proxy_url: proxy }),
  }).then(r => r.json());
  if (r.ok) {
    toast(`✅ ${acc.name} 代理正常\n出口 IP: ${r.ip}\n延迟 ${r.latency_ms}ms`);
  } else {
    alert(`❌ ${acc.name} 代理失败\n\n${r.message}\n\n检查格式是否为 http://用户名:密码@IP:端口`);
  }
}

function showBulkProxy() {
  const lines = accounts.map((a, i) => `# ${i+1}. ${a.name}: ${a.proxy_url || '（空）'}`).join('\n');
  const example = `请每行粘一个代理，按顺序对应 ${accounts.length} 个账号：\n\nhttp://user1:pass1@ip1:port\nhttp://user2:pass2@ip2:port\n...\n\n当前状态:\n${lines}`;
  const input = prompt(example, '');
  if (!input) return;
  const proxies = input.split(/\\n|,/).map(s => s.trim()).filter(Boolean);
  if (!proxies.length) { alert('没解析到代理'); return; }
  fetch('/api/accounts/bulk-proxy', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ proxies }),
  }).then(r => r.json()).then(r => {
    toast(`✅ 已给 ${r.updated}/${r.total_accounts} 个账号分配代理`);
    loadAccounts();  // 刷新显示
  });
}

async function loginAll() {
  await saveAccounts();
  if (!confirm('将为所有账号同时打开浏览器扫码。\n\n注意：\n1. 先停止主脚本（避免 profile 冲突）\n2. 扫码前在抖音 APP 切换到对应账号\n\n继续？')) return;
  const r = await fetch('/api/accounts/login-all', { method: 'POST' }).then(r => r.json());
  const ok = (r.results || []).filter(x => x.ok).length;
  const fail = (r.results || []).filter(x => !x.ok);
  let msg = `🚀 已启动 ${ok} 个登录浏览器\n\n`;
  if (fail.length) msg += '失败的账号:\n' + fail.map(x => `  ${x.name}: ${x.message}`).join('\n');
  msg += '\n\n请依次扫码（切换抖音 APP 账号后扫）';
  alert(msg);
}

async function viewLoginLog(name, silent) {
  try {
    const r = await fetch(`/api/accounts/${encodeURIComponent(name)}/login/status`).then(r => r.json());
    const tail = (r.log_tail || []).join('\n') || '（暂无日志）';
    const stateTxt = { idle: '空闲', running: '运行中', done: '已结束' }[r.state] || r.state;
    const msg = `账号 ${name} 登录状态：${stateTxt}${r.pid ? ' PID=' + r.pid : ''}${r.exit_code != null ? ' 退出码=' + r.exit_code : ''}\n\n--- 最近日志 ---\n${tail}`;
    // 弹窗展示 & 打印到控制台方便复制
    console.log(msg);
    if (!silent) alert(msg);
    else if (r.state === 'done' && r.exit_code) {
      alert(`登录进程异常退出（退出码 ${r.exit_code}）\n\n点 📝 登录日志 查看详情`);
    }
  } catch (e) {
    alert('读取日志失败：' + e.message);
  }
}

// ── 无脑模式 ─────────────────────────────
function renderBrainless() {
  const wrap = document.getElementById('brainless-texts');
  wrap.innerHTML = '';
  (brainlessTexts.length ? brainlessTexts : ['']).forEach((t, i) => {
    const row = document.createElement('div');
    row.className = 'reply-row';
    row.innerHTML = `
      <input type="text" value="${esc(t)}" oninput="brainlessTexts[${i}]=this.value" placeholder="回复内容">
      <button class="secondary small" onclick="removeBrainlessText(${i})">✕</button>
    `;
    wrap.appendChild(row);
  });
  // 确保 brainlessTexts 至少有一项
  if (brainlessTexts.length === 0) brainlessTexts = [''];
}
function addBrainlessText() { brainlessTexts.push(''); renderBrainless(); }
function removeBrainlessText(i) { brainlessTexts.splice(i, 1); renderBrainless(); }

function onKwToggle() {
  keywordEnabled = document.getElementById('keywordToggle').checked;
  document.getElementById('keyword-section').style.opacity = keywordEnabled ? '1' : '0.4';
  updateBanner();
}
function onBrToggle() {
  brainlessEnabled = document.getElementById('brainlessToggle').checked;
  document.getElementById('brainless-body').style.display = brainlessEnabled ? 'block' : 'none';
  updateBanner();
}
function updateBanner() {
  const b = document.getElementById('mode-banner');
  const s = document.getElementById('mode-summary');
  if (!keywordEnabled && !brainlessEnabled) {
    b.style.display = 'block';
    b.style.background = '#7f1d1d';
    b.style.border = '1px solid #ef4444';
    b.style.color = '#fecaca';
    b.innerHTML = '❌ <strong>两个开关都已关闭</strong> → 不会自动回复任何消息';
    if (s) s.textContent = '当前状态：不回复';
  } else if (keywordEnabled && brainlessEnabled) {
    b.style.display = 'block';
    b.style.background = '#064e3b';
    b.style.border = '1px solid #10b981';
    b.style.color = '#d1fae5';
    b.innerHTML = '✓ 关键词命中优先回复，未命中兜底用无脑文案';
    if (s) s.textContent = '当前状态：关键词 + 无脑兜底';
  } else if (keywordEnabled) {
    b.style.display = 'block';
    b.style.background = '#1e3a8a';
    b.style.border = '1px solid #3b82f6';
    b.style.color = '#dbeafe';
    b.innerHTML = '🔑 只在关键词命中时回复，其他消息不回';
    if (s) s.textContent = '当前状态：仅关键词';
  } else {
    b.style.display = 'block';
    b.style.background = '#78350f';
    b.style.border = '1px solid #f59e0b';
    b.style.color = '#fef3c7';
    b.innerHTML = '🤖 无脑兜底：任意消息都回无脑文案';
    if (s) s.textContent = '当前状态：仅无脑';
  }
}

async function saveMode() {
  try {
    const texts = brainlessTexts.filter(t => t && t.trim());
    if (brainlessEnabled && texts.length === 0) {
      toast('❌ 开了无脑但没填文案，请添加至少一条', true);
      return;
    }
    const r = await fetch('/api/reply-mode', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        keyword_enabled: keywordEnabled,
        brainless_enabled: brainlessEnabled,
        brainless_reply_texts: texts,
      }),
    }).then(r => r.json());
    if (r.ok) {
      toast(`✅ 已保存：关键词=${keywordEnabled ? '开' : '关'}，无脑=${brainlessEnabled ? '开' : '关'}（${r.brainless_count} 条文案）`);
      await load();
    } else {
      toast('❌ 保存失败: ' + (r.detail || r.message || 'unknown'), true);
    }
  } catch (e) { toast('❌ ' + e.message, true); }
}

function render() {
  const container = document.getElementById('rules-container');
  container.innerHTML = '';
  if (rules.length === 0) {
    container.innerHTML = '<div style="color:var(--muted);text-align:center;padding:40px">（尚未配置任何规则，点上方按钮添加）</div>';
    return;
  }
  rules.forEach((r, i) => {
    const div = document.createElement('div');
    div.className = 'rule' + (r.is_default ? ' default' : '');
    div.innerHTML = ruleHTML(r, i);
    container.appendChild(div);
  });
}

function ruleHTML(r, i) {
  const isDef = r.is_default;
  return `
    <div class="rule-header">
      <div class="rule-title ${isDef ? 'default' : ''}">
        规则 #${i + 1}
        ${isDef ? '<span class="default-badge">兜底</span>' : ''}
      </div>
      <button class="secondary small" onclick="removeRule(${i})">🗑 删除</button>
    </div>

    ${!isDef ? `
    <div class="row-inline">
      <div class="field" style="flex: 3">
        <div class="field-label">关键词（逗号分隔）</div>
        <input type="text" value="${esc(r.keywords)}" oninput="updateField(${i}, 'keywords', this.value)" placeholder="例：多少钱,价格,怎么卖">
      </div>
      <div class="field" style="flex: 1">
        <div class="field-label">匹配模式</div>
        <select onchange="updateField(${i}, 'match_mode', this.value)">
          <option value="contains" ${r.match_mode === 'contains' ? 'selected' : ''}>包含</option>
          <option value="exact" ${r.match_mode === 'exact' ? 'selected' : ''}>全等</option>
          <option value="regex" ${r.match_mode === 'regex' ? 'selected' : ''}>正则</option>
        </select>
      </div>
    </div>
    ` : `
    <div class="hint">兜底规则：当所有关键词规则都未命中时触发</div>
    `}

    <div class="field">
      <div class="field-label">回复文本（多条随机选一条）</div>
      ${(r.reply_texts || []).map((t, j) => `
        <div class="reply-row">
          <input type="text" value="${esc(t)}" oninput="updateReplyText(${i}, ${j}, this.value)" placeholder="回复内容">
          <button class="secondary small" onclick="removeReplyText(${i}, ${j})">✕</button>
        </div>
      `).join('')}
      <button class="secondary small" onclick="addReplyText(${i})">+ 添加一条回复</button>
    </div>

    <div class="row-inline">
      <div class="field" style="flex: 2">
        <div class="field-label">图片路径（可选，PC 私信不支持发图）</div>
        <input type="text" value="${esc(r.reply_image)}" oninput="updateField(${i}, 'reply_image', this.value)" placeholder="data/cards/xxx.png">
      </div>
      <div class="field" style="flex: 3">
        <div class="field-label">图片后文字（可选）</div>
        <input type="text" value="${esc(r.reply_text_after)}" oninput="updateField(${i}, 'reply_text_after', this.value)" placeholder="例：扫码加我微信~">
      </div>
    </div>
  `;
}

function esc(s) {
  return (s || '').toString().replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

function addRule(isDefault) {
  rules.push({
    keywords: '',
    match_mode: 'contains',
    reply_texts: [''],
    reply_image: '',
    reply_text_after: '',
    is_default: !!isDefault,
  });
  render();
}

function removeRule(i) {
  if (!confirm('确定删除这条规则？')) return;
  rules.splice(i, 1);
  render();
}

function updateField(i, field, value) {
  rules[i][field] = value;
}

function updateReplyText(i, j, value) {
  rules[i].reply_texts[j] = value;
}

function addReplyText(i) {
  rules[i].reply_texts = rules[i].reply_texts || [];
  rules[i].reply_texts.push('');
  render();
}

function removeReplyText(i, j) {
  rules[i].reply_texts.splice(j, 1);
  render();
}

async function save() {
  try {
    const res = await fetch('/api/rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ rules }),
    });
    const data = await res.json();
    if (data.ok) toast(`✅ 已保存 ${data.count} 条规则`);
    else toast('❌ 保存失败: ' + (data.detail || 'unknown'), true);
  } catch (e) {
    toast('❌ 请求失败: ' + e.message, true);
  }
}

function toast(msg, err) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'saved-toast show' + (err ? ' err' : '');
  setTimeout(() => { t.className = 'saved-toast'; }, 2500);
}

load();
refreshMainStatus();
setInterval(refreshMainStatus, 3000);    // 每 3 秒刷新主脚本状态
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
def root():
    return HTML


# ── 启动 ─────────────────────────────────────────────────

def main():
    import argparse
    p = argparse.ArgumentParser(description="抖音自动回复配置 UI")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8020)
    args = p.parse_args()
    print(f"\n➜ 打开浏览器访问: http://{args.host}:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
