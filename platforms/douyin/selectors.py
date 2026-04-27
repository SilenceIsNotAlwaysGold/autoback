"""抖音页面选择器集中管理

来源：douying 本地工具 + creator.douyin.com 实测。
改版时只需改这个文件。
"""

# ── URL ───────────────────────────────────────────────────
HOME_URL = "https://creator.douyin.com"
LOGIN_URL = "https://creator.douyin.com"  # 落地页，需要点击"登录"进入扫码
# 真实私信入口：创作者中心 → 数据中心 → 粉丝 → 互动消息 tab
# 用户实测确认：https://creator.douyin.com/creator-micro/data/following/chat
MESSAGING_URL = "https://creator.douyin.com/creator-micro/data/following/chat"
PUBLISH_URL = "https://creator.douyin.com/creator-micro/content/upload"
IMAGE_PUBLISH_URL = "https://creator.douyin.com/creator-micro/content/publish?content_type=note"
COMMENT_MANAGE_URL = "https://creator.douyin.com/creator-micro/interactive/comment"

# ── 登录检测 ──────────────────────────────────────────────
LOGIN_INDICATORS = ['/login', '/passport']

# ── 发布页 ────────────────────────────────────────────────
PUBLISH = {
    "editor_selectors": [
        '[data-e2e="publish-editor"]',
        '[class*="editor-kit"]',
        '.ql-editor',
        '[contenteditable="true"]',
    ],
    "upload_input": 'input[type="file"], [data-e2e="upload"] input[type="file"], [class*="upload"] input[type="file"]',
    "cover_input": '[class*="cover"] input[type="file"], [data-e2e="cover-upload"] input[type="file"]',
    "publish_btn_texts": ["发布", "确认发布"],
    "success_texts": ["发布成功", "作品已发布"],
    "success_urls": ["manage", "content"],
    "progress_selectors": '[class*="progress"], [class*="uploading"]',
    "complete_selectors": '[class*="progress-done"], [class*="upload-success"], [class*="upload-complete"]',

    # 话题/@/定位/定时/声明（参考 douyin_uplod）
    "topic_trigger": '[class*="topic-item"], [class*="hashtag-item"]',
    "mention_trigger": '[class*="mention-item"], [class*="at-user"]',
    "location_btn": '[class*="location"], :text("添加位置")',
    "location_search": '[placeholder*="搜索位置"], [class*="location-search"] input',
    "location_item": '[class*="location-item"], [class*="poi-item"]',
    "schedule_checkbox": '[class*="schedule"], :text("定时发布")',
    "schedule_datetime": '[class*="date-picker"], [class*="time-picker"]',
    "declaration_original": ':text("声明原创")',
}

# ── 私信页 ────────────────────────────────────────────────
# 页面实测（2026-04）：抖音用 Semi Design UI 组件 + 自定义 hash class
# 会话行：.semi-list-item （稳定）
# 会话内结构：item-header-name / item-header-time / item-content / text 都是带 hash 的
# 用户名：[class^="item-header-name-"]
# 最后消息：[class^="item-content-"] 内的 [class^="text-"]
# 虚拟滚动：.ReactVirtualized__List（不可见部分不在 DOM 中）
IM = {
    # 会话列表
    "conversation_list": ".semi-list-item",

    # 未读标记（Semi Badge 红点）
    "unread_selectors": ".semi-badge-dot, [class^='item-content-'] .semi-badge",

    # 会话行内子元素
    "name_selector": '[class^="item-header-name-"]',
    "last_msg_selector": '[class^="item-content-"] [class^="text-"]',
    "time_selector": '[class^="item-header-time-"]',

    # Tab: 全部 / 朋友私信 / 陌生人私信 / 群消息
    "tab_all": '.semi-tabs-tab:has-text("全部")',
    "tab_strangers": '.semi-tabs-tab:has-text("陌生人私信")',

    # ─── 会话详情页（点进某条会话后）─────
    # 输入框：div.chat-input-xxx（contenteditable，不是 textarea）
    "input_selector": '[class^="chat-input-"]',
    # 输入框内可能有 contenteditable 子元素；兜底直接点击 chat-input 容器后 keyboard type
    "input_editable": '[class^="chat-input-"] [contenteditable="true"], [class^="chat-input-"]',

    # 上传图片
    "upload_image_selectors": [
        'input[type="file"][accept*="image"]',
        'input[type="file"]',
    ],

    # 发送按钮：.chat-btn 是稳定 class（不带 hash）
    # 未输入时带 .semi-button-disabled，输入后才启用
    "send_selectors": [
        'button.chat-btn:not(.semi-button-disabled)',
        'button.chat-btn',
    ],

    # 消息气泡正文：稳定出现 15 次，精准匹配每条消息
    "message_item": '[class^="box-item-message-"]',
    # 消息项完整容器（含昵称/头像）
    "message_box": '[class^="box-item-W"]',
    # 群聊昵称（只在群消息里有）
    "message_nickname": '[class^="box-item-nickname-"]',
}

# ── 评论管理页 ────────────────────────────────────────────
# 创作中心→互动管理→评论管理
# 真实 DOM（基于 2026-04 实测）：
#   [class^="container-s"]      ← 每条评论的外层容器
#     [class^="content-FM"]     ← 内容块
#       [class^="username-"]    ← 用户名
#       [class^="comment-content-text-"]  ← 评论正文
# 注：抖音用 CSS-in-JS，class 带 6 位 hash；用 [class^=...] 前缀匹配抗改版
COMMENT = {
    # 评论行：优先用 content 块，比 container 更聚焦（container 在页面其他地方也用）
    "comment_row": '[class^="content-FM"]',

    # 行内子元素（在 row 作用域内查询）
    "author_name": '[class^="username-"]',
    "comment_text": '[class^="comment-content-text-"]',
    "comment_id_attr": ["data-comment-id", "data-id"],

    # "回复"按钮：每条评论下 operations 容器里的 div.item-*（其他还有"删除/举报"）
    # 用 text=回复 精准匹配
    "reply_trigger": '[class^="operations-"] [class^="item-"]',
    "reply_trigger_text": "回复",   # 按文字过滤，区分删除/举报

    # 弹出的回复输入框：contenteditable DIV，placeholder 以"回复 "开头
    # （和页面顶部默认输入框区分开）
    "reply_input": 'div[placeholder^="回复 "]',
    "reply_input_fallback": '[class^="input-"]',  # 兜底

    # 发送按钮：douyin-creator-interactive-button 稳定 class
    # 未输入时带 -disabled，输入后启用
    "reply_submit": 'button.douyin-creator-interactive-button:not(.douyin-creator-interactive-button-disabled)',

    # 已回复标记（用于过滤已处理评论）
    "replied_badge": ':text("已回复")',

    # 未读/筛选 Tab
    "filter_unreplied_tab": ':text("未回复")',
}
