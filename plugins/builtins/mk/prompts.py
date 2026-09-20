"""MK 配置 — 快捷指令和按钮"""
ACTION_PROMPTS = {
    "auto": "根据以下内容，帮用户完成任务。直接输出结果，像继续写一样自然。",
    "translate_cn": "将以下内容翻译为中文，只返回译文。",
    "translate_en": "Translate the following content to English. Return only the translation.",
    "explain": "用简单的大白话解释以下内容，像朋友聊天一样。",
    "polish": "润色以下文字，让表达更流畅自然，保持原意。",
    "code_review": "审查以下代码，指出问题并给出改进建议。语气轻松但专业。",
    "expand": "扩写/补全以下代码或文字，保持风格一致。",
    "summarize": "用一两句话总结以下内容。",
    "reply": "帮用户回复这条消息。回复要自然、简短，像真人聊天一样。",
}

PROCESS_BUTTONS = [
    {"label": "📝 自动", "action": "auto"},
    {"label": "🌐 →中文", "action": "translate_cn"},
    {"label": "🌐 →English", "action": "translate_en"},
    {"label": "📋 总结", "action": "summarize"},
    {"label": "🐛 审查", "action": "code_review"},
    {"label": "📝 润色", "action": "polish"},
    {"label": "🔍 解释", "action": "explain"},
    {"label": "📝 扩写", "action": "expand"},
]

MENU_ITEMS = ["✨ AI-Eat"]
