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

def call_gemini(user_id: str, prompt: str, model_name: str = None, image_data: Dict[str, str] = None) -> str:
    """调用 Google Gemini 官方 API (支持多模态识图解题、动态切换模型与 503 自动容灾降级)"""
    api_key = os.environ.get("GEMINI_API_KEY", "").strip() or cfg.get("GEMINI_API_KEY", "").strip()
    
    # 优先使用前端传入的模型，其次环境变量，最后默认使用官方最强免费模型 gemini-3.8-flash
    primary_model = model_name or os.environ.get("GEMINI_MODEL", "").strip() or cfg.get("GEMINI_MODEL", "gemini-3.8-flash")
    
    # 候选备用模型 (全部采用 Google 官方 100% 免费模型，遇到波动自动平滑切换)
    candidate_models = [primary_model]
    for fallback in ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.1-pro-preview"]:
        if fallback not in candidate_models:
            candidate_models.append(fallback)

    raw_proxy = cfg.get("PROXY", "").strip()
    proxy = raw_proxy if raw_proxy else None

    # 云服务器（Render 等平台）原生支持海外直连 Google，强制不使用本地代理
    if os.environ.get("RENDER") or os.environ.get("PORT"):
        proxy = None

    if not api_key:
        return "【系统提示】尚未配置 GEMINI_API_KEY，请在 Render 的 Environment 中配置。"

    # 读取上下文历史 (连贯记忆)
    history = db.get_chat_history(user_id, limit=6)
    contents = []
    for h in history:
        contents.append({
            "role": "user" if h["role"] == "user" else "model",
            "parts": [{"text": h["content"]}]
        })

    # 组装当前用户问题与多模态图片
    user_parts = []
    text_prompt = prompt.strip() if prompt else ("请详细分析并解答这道题目，给出清晰推导步骤。" if image_data else "")
    if text_prompt:
        user_parts.append({"text": text_prompt})

    # 如果有上传图片，添加 inlineData 结构
    if image_data and image_data.get("data"):
        user_parts.append({
            "inlineData": {
                "mimeType": image_data.get("mime_type", "image/jpeg"),
                "data": image_data.get("data")
            }
        })

    if not user_parts:
        return "请发送题目文字或上传图片。"

    contents.append({
        "role": "user",
        "parts": user_parts
    })

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
    for current_model in candidate_models:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{current_model}:generateContent?key={api_key}"
        try:
            with httpx.Client(proxy=proxy, timeout=40.0) as client:
                resp = client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    answer = data["candidates"][0]["content"]["parts"][0]["text"]
                    
                    # 记录持久化聊天历史 (带图标识)
                    saved_user_text = text_prompt
                    if image_data:
                        saved_user_text = "[📷 图片] " + (text_prompt or "解题图片")
                    db.save_chat_message(user_id, "user", saved_user_text)
                    db.save_chat_message(user_id, "assistant", answer)
                    return answer
                elif resp.status_code == 503 or resp.status_code == 429:
                    print(f"[Model Fallback] 模型 {current_model} 高峰拥挤 ({resp.status_code})，自动尝试备用模型...")
                    last_err = f"模型 {current_model} 暂时繁忙"
                    time.sleep(0.5)
                    continue
                else:
                    return f"Gemini 接口响应异常 ({resp.status_code}): {resp.text[:200]}"
        except Exception as e:
            last_err = e
            time.sleep(0.5)

    return f"模型当前处于全球请求高峰期，请稍等 10 秒后再试 (错误: {last_err})"

def run_agent(user_id: str, user_message: str, model_name: str = None, image_data: Dict[str, str] = None) -> str:
    """主入口函数"""
    # 如果仅包含文本且没有图片，优先检查是否是记事本快速指令
    if not image_data:
        note_resp = handle_quick_notes(user_id, user_message)
        if note_resp:
            return note_resp

        # 检查是否显式要求联网搜索
        if user_message.startswith("搜索") or user_message.startswith("搜一下") or user_message.startswith("查一下"):
            query = user_message.lstrip("搜索：: 查一下搜一下")
            search_res = search.search_web(query)
            prompt_with_search = f"用户想要了解「{query}」，以下是网页检索到的参考资料：\n{search_res}\n\n请结合以上资料给出全面清晰的总结。"
            return call_gemini(user_id, prompt_with_search, model_name=model_name)

    # 正常交付 Gemini 进行多模态识图解题与连贯问答
    return call_gemini(user_id, user_message, model_name=model_name, image_data=image_data)
