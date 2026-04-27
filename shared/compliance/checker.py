"""内容合规检查 — 发布前检测违规词"""
import logging
from typing import Any

logger = logging.getLogger(__name__)

# 内置违禁词列表（平台通用）
BUILTIN_BANNED_WORDS = [
    # 绝对化用语
    "最好", "最佳", "第一", "顶级", "极品", "绝无仅有", "独一无二",
    "国家级", "全球首", "世界第一", "行业领先",
    # 虚假宣传
    "假一赔十", "无效退款", "包治", "根治", "祖传秘方",
    # 引流违规
    "加微信", "加V", "加vx", "加wx", "私聊", "私信我",
    "转账", "打款", "汇款",
    # 平台违规
    "刷单", "好评返现", "删差评",
    # 敏感内容
    "赌博", "博彩", "色情", "枪支", "毒品",
]

# 平台专属违禁词
PLATFORM_BANNED: dict[str, list[str]] = {
    "xianyu": ["转转", "拼多多", "淘宝链接", "店铺链接"],
    "douyin": ["快手", "B站", "油管"],
    "xiaohongshu": ["淘宝", "拼多多", "咸鱼"],
}


def check_content(
    title: str = "",
    description: str = "",
    platform: str = "",
    custom_words: list[str] | None = None,
) -> dict[str, Any]:
    """检查内容合规性

    Returns:
        {
            "passed": bool,
            "violations": [{"word": str, "field": str, "level": "danger"|"warning"}],
            "suggestions": [str],
        }
    """
    violations: list[dict] = []
    text_fields = {"title": title, "description": description}

    # Check against banned words
    all_banned = BUILTIN_BANNED_WORDS.copy()
    if platform and platform in PLATFORM_BANNED:
        all_banned.extend(PLATFORM_BANNED[platform])
    if custom_words:
        all_banned.extend(custom_words)

    for field_name, text in text_fields.items():
        if not text:
            continue
        text_lower = text.lower()
        for word in all_banned:
            if word.lower() in text_lower:
                violations.append({
                    "word": word,
                    "field": field_name,
                    "level": "danger" if word in BUILTIN_BANNED_WORDS[:12] else "warning",
                })

    # Length checks
    suggestions: list[str] = []
    if title and len(title) > 30:
        suggestions.append(f"标题过长（{len(title)}字），建议30字以内")
    if title and len(title) < 4:
        suggestions.append("标题过短，建议至少4个字")
    if description and len(description) > 2000:
        suggestions.append(f"描述过长（{len(description)}字），可能被截断")

    return {
        "passed": len([v for v in violations if v["level"] == "danger"]) == 0,
        "violations": violations,
        "suggestions": suggestions,
    }
