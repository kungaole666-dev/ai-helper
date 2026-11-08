import os
import json
import time
import httpx
from typing import Dict, Any, List
from config import cfg
import db
import search

SYSTEM_INSTRUCTION = """你是一个智能、亲切且高效的微信个人助手。你具备以下核心能力：
1. 【解题辅导】：面对数学、物理、化学、编程等学科或算法问题时，给出严谨详细的分步解答、公式推导与思路解析。尽量使用纯文本易读排版。
2. 【日常问答与分析】：回答用户的各类疑问，给出条理清晰、有深度、客观准确的建议。
3. 【记事提醒辅助】：当用户想要记录待办时，提醒他们可以使用简洁的指令（如：'记一下：明天下午开会'），系统会自动归档。

请用自然、流畅的中文回答。"""

def handle_quick_notes(user_id: str, text: str) -> str:
    """快速前置拦截：记事本与备忘查询，保证100%命中且0延迟"""
    clean_text = text.strip()
    
    # 查看笔记
    if clean_text in ["查看笔记", "我的备忘", "我记了什么", "备忘录", "待办清单", "查看待办"]:
        notes = db.get_notes(user_id=user_id, limit=10)
        if not notes:
            return "📝 你目前还没有记录任何备忘事项哦。\n发送「记一下：买牛奶」即可随时记录！"
        lines = ["📝 【你的最新备忘清单】:"]
        for n in notes:
            lines.append(f"• #{n['id']} [{n['category']}] {n['content']} ({n['created_at'][5:16]})")
        return "\n".join(lines)

    # 记录笔记
    triggers = ["记一下", "帮我记", "备忘：", "备忘:", "记录：", "记录:", "待办：", "待办:"]
    for t in triggers:
        if clean_text.startswith(t):
            content = clean_text[len(t):].strip().lstrip("：:，, ")
            if content:
                res = db.add_note(user_id=user_id, content=content, category="待办" if "待办" in t else "备忘")
                return f"✅ 已成功记下！\n📌 内容: {content}\n🕒 时间: {res['created_at']}"
            else:
                return "你想记什么内容呢？例如发送：记一下：明天下午交作业"

    return None

def call_gemini(user_id: str, prompt: str) -> str:
    """调用 Google Gemini 官方 API"""
   api_key = os.environ.get("GEMINI_API_KEY", "").strip() or cfg.get("GEMINI_API_KEY", "").strip()
    model = cfg.get("GEMINI_MODEL", "gemini-3-flash-preview")
    # 如果是云端运行 (有 PORT 环境变量且 PROXY 为本地 127.0.0.1)，不使用本地代理直接直连
    if os.environ.get("RENDER") or os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("ZEABUR"):
        proxy = None
    elif proxy and "127.0.0.1" in proxy and os.environ.get("PORT"):
        # 云服务器上不需要 127.0.0.1 代理
        proxy = None

    if not api_key:
        return "【系统提示】尚未配置 GEMINI_API_KEY，请在 config.json 中配置。"

    # 读取上下文历史
    history = db.get_chat_history(user_id, limit=4)
    contents = []
    for h in history:
        contents.append({
            "role": "user" if h["role"] == "user" else "model",
            "parts": [{"text": h["content"]}]
        })
    # 追加当前问题
    contents.append({
        "role": "user",
        "parts": [{"text": prompt}]
    })

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

    payload = {
        "contents": contents,
        "systemInstruction": {
            "parts": [{"text": SYSTEM_INSTRUCTION}]
        },
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 2048
        }
    }

    last_err = None
    for attempt in range(2):
        try:
            with httpx.Client(proxy=proxy, timeout=35.0) as client:
                resp = client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    answer = data["candidates"][0]["content"]["parts"][0]["text"]
                    # 记录回答
                    db.save_chat_message(user_id, "user", prompt)
                    db.save_chat_message(user_id, "assistant", answer)
                    return answer
                elif resp.status_code == 429:
                    return "Gemini 服务当前请求较多，请稍等 10 秒后再试。"
                else:
                    return f"Gemini 接口响应异常 ({resp.status_code}): {resp.text[:200]}"
        except Exception as e:
            last_err = e
            time.sleep(1)

    print(f"[Gemini Request Exception] {last_err}")
    return f"调用 Gemini 时网络或服务异常: {last_err}"

def run_agent(user_id: str, user_message: str) -> str:
    """主入口函数"""
    # 1. 优先检查是否是记事本快速指令
    note_resp = handle_quick_notes(user_id, user_message)
    if note_resp:
        return note_resp

    # 2. 检查是否显式要求联网搜索
    if user_message.startswith("搜索") or user_message.startswith("搜一下") or user_message.startswith("查一下"):
        query = user_message.lstrip("搜索：: 查一下搜一下")
        search_res = search.search_web(query)
        # 将搜索结果喂给 Gemini 进行归纳
        prompt_with_search = f"用户想要了解「{query}」，以下是网页检索到的参考资料：\n{search_res}\n\n请结合以上资料给出全面清晰的总结。"
        return call_gemini(user_id, prompt_with_search)

    # 3. 正常交给 Gemini 进行解题与深度问答
    return call_gemini(user_id, user_message)
