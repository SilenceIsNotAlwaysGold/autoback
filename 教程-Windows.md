# 抖音自动回复 — Windows 使用教程

## 一、安装

1. **解压** `dy_auto_reply_windows.zip`，会得到 `dy_auto_reply` 文件夹
2. **整个文件夹拷到任何位置**（建议 `C:\dy_auto_reply\`），不要只拷 .exe

## 二、首次启动

### 步骤 1：绕过 Windows Defender / SmartScreen

应用未做代码签名，第一次启动会被拦下。

**方法 A**：右键 `dy_auto_reply.exe` → 「以管理员身份运行」

**方法 B**：双击后弹出「Windows 已保护你的电脑」蓝色窗口
- 点「**更多信息**」 → 出现「仍要运行」按钮 → 点击运行

**方法 C**：右键 .exe → 「属性」 → 勾选「解除锁定」 → 应用 → 双击

### 步骤 2：等待初始化（首次最久 2 分钟）

第一次启动会做：
1. 在 `%APPDATA%\dy_auto_reply\` 创建数据目录
2. **下载 Chromium 浏览器内核**（约 150 MB，1–2 分钟）

⚠️ 此时窗口可能"假死"，没有进度条，**耐心等**。

下载完成后会自动用默认浏览器打开 **http://127.0.0.1:8020** 配置界面。

## 三、配置账号

UI 切到「账号管理」Tab：

- **账号名**：自定义标识，例如 `acc1`
- **代理 URL**（必填）：
  - 例：`socks5://用户名:密码@1.2.3.4:10086`
- **登录超时**：默认 180 秒
- **比特浏览器 ID**（可选）

填完点「保存」→ 「测试代理」→ 显示 IP 表示通了。

「扫码登录」按钮 → 弹 Chrome → 抖音 APP 扫码 → 登录态保存。

## 四、配置规则 + 启动

「关键词规则」Tab：
- 顶部开关：**关键词模式** + **用户回复冷却（默认 30s）**
- 加规则：关键词逗号分隔（中英文都行），回复内容多行随机选一条

顶栏「启动主脚本」开始运行。

## 五、数据位置

```
%APPDATA%\dy_auto_reply\
  ├─ config\dy_reply.yaml       ← 配置
  ├─ data\dy_reply.db           ← 回复记录
  ├─ data\browser_profiles\     ← 各账号登录态
  └─ logs\dy_main.log           ← 运行日志
```

完整路径例：`C:\Users\你的用户名\AppData\Roaming\dy_auto_reply\`

资源管理器地址栏输入 `%APPDATA%\dy_auto_reply` 可直接打开。

## 六、常见问题

**Q: 启动后没反应？**
A: 看日志 `%APPDATA%\dy_auto_reply\logs\dy_main.log`

**Q: Chromium 下载失败？**
A: 检查网络，挂代理重试。或者手动跑：
```
%APPDATA%\dy_auto_reply\dy_auto_reply.exe
```
能看到下载进度。

**Q: 想停止 bot？**
A: UI 点「停止主脚本」。或者任务管理器结束 `dy_auto_reply.exe`。

**Q: 想换电脑用？**
A: 把 `%APPDATA%\dy_auto_reply\` 整个目录拷到新电脑同位置。

**Q: 杀毒软件误报？**
A: PyInstaller 打包的 .exe 经常被误报。把 .exe 加白名单。

## 七、卸载

1. 删除整个 `dy_auto_reply` 文件夹
2. 删除 `%APPDATA%\dy_auto_reply\` 目录
3. 删除 `%LOCALAPPDATA%\ms-playwright\` 目录（Chromium 缓存）
