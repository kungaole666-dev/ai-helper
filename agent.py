import os
import json
import time
import httpx
from typing import Dict, Any, List
from config import cfg
import db
import search

SYSTEM_INSTRUCTION = """你是一个严谨、高效的专业 AI 助手。
1. 解题与分析：面对学科问题、学术推导、代码编程或逻辑推理时，给出严谨详细的分步解答与推导。数学公式使用标准 LaTeX 格式（行内公式用 $...$，独立块级公式用 $$...$$）。
2. 文档与图像：精准分析用户上传的文档和图像内容，提取关键信息并做客观解答。
3. 记事与管理：协助记录和整理备忘事项。
回复必须专业、准确、客观，排版清晰规整，不使用夸张或冗余的修饰语。"""

# 全局 HTTP 长连接池，消除每一次请求的 TLS/TCP 握手延时 (提速 500ms ~ 1s)
_CLIENT_POOL = None

def get_http_client(proxy: str = None) -> httpx.Client:
    global _CLIENT_POOL
    if _CLIENT_POOL is None or _CLIENT_POOL.is_closed:
        _CLIENT_POOL = httpx.Client(
            proxy=proxy,
            timeout=60.0,
            limits=httpx.Limits(max_keepalive_connections=25, max_connections=50)
        )
    return _CLIENT_POOL

def handle_quick_notes(user_id: str, text: str) -> str:
    """快速前置拦截：记事本与备忘查询"""
    clean_text = text.strip()
    
    # 查看笔记
    if clean_text in ["查看笔记", "我的备忘", "我记了什么", "备忘录", "待办清单", "查看待办"]:
        notes = db.get_notes(user_id=user_id, limit=20)
        if not notes:
            return "暂无备忘事项。"
        lines = ["备忘列表:"]
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
                return f"已记录：{content}"
            else:
                return "请输入需要记录的备忘内容。"

    return None

def get_api_key_pool() -> List[str]:
    """支持多 API Key 自动轮询与负载均衡"""
    raw = os.environ.get("GEMINI_API_KEY", "").strip() or cfg.get("GEMINI_API_KEY", "").strip()
    if not raw:
        return []
    keys = [k.strip() for k in raw.replace("；", ",").replace(";", ",").split(",") if k.strip()]
    return keys

def prepare_gemini_contents(user_id: str, prompt: str, image_data: Dict[str, str] = None, file_data: Dict[str, Any] = None, session_id: str = "default") -> List[Dict[str, Any]]:
    """构建精炼的高性能上下文 Contents"""
    # 读取上下文历史 (保留最近 6 轮对话，兼顾记忆连贯性与极速传输)
    history = db.get_chat_history(user_id, session_id=session_id, limit=6)
    contents = []
    for h in history:
        role = "user" if h["role"] == "user" else "model"
        if not contents and role != "user":
            continue
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"][0]["text"] += "\n" + h["content"]
        else:
            contents.append({
                "role": role,
                "parts": [{"text": h["content"]}]
            })

    user_parts = []
    default_hint = "请详细分析并解答用户提供的题目或文件内容，给出清晰严谨的推导与解答。"
    text_prompt = prompt.strip() if prompt else ((default_hint if (image_data or file_data) else ""))

    if file_data and file_data.get("text_content"):
        fname = file_data.get("name", "文档")
        text_prompt = f"【用户上传的附件文档: {fname}】\n\n```\n{file_data['text_content']}\n```\n\n用户问题/需求: {text_prompt or '请全面总结、分析并解答该文件中的关键内容'}"

    if text_prompt:
        user_parts.append({"text": text_prompt})

    if image_data and image_data.get("data"):
        user_parts.append({
            "inlineData": {
                "mimeType": image_data.get("mime_type", "image/jpeg"),
                "data": image_data.get("data")
            }
        })

    if file_data and file_data.get("mime_type") == "application/pdf" and file_data.get("data"):
        user_parts.append({
            "inlineData": {
                "mimeType": "application/pdf",
                "data": file_data.get("data")
            }
        })

    if not user_parts:
        return []

    contents.append({
        "role": "user",
        "parts": user_parts
    })
    return contents

def stream_gemini(user_id: str, prompt: str, model_name: str = None, image_data: Dict[str, str] = None, file_data: Dict[str, Any] = None, session_id: str = "default"):
    """流式返回 Gemini 结果 (打字机效果，毫秒级首字呈现)"""
    keys = get_api_key_pool()
    if not keys:
        yield json.dumps({"text": "【系统提示】尚未配置 GEMINI_API_KEY，请在 Render 的 Environment 中配置。"}, ensure_ascii=False) + "\n"
        return

    # 优先使用极速轻量模型以获得极速响应
    primary_model = model_name or os.environ.get("GEMINI_MODEL", "").strip() or cfg.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
    candidate_models = [primary_model]
    for fallback in ["gemini-3.5-flash-lite", "gemini-3.8-flash", "gemini-3.1-flash-lite", "gemini-3.1-pro-preview"]:
        if fallback not in candidate_models:
            candidate_models.append(fallback)

    raw_proxy = cfg.get("PROXY", "").strip()
    proxy = raw_proxy if raw_proxy else None
    if os.environ.get("RENDER") or os.environ.get("PORT"):
        proxy = None

    client = get_http_client(proxy=proxy)
    contents = prepare_gemini_contents(user_id, prompt, image_data, file_data, session_id)
    if not contents:
        yield json.dumps({"text": "请发送题目文字、上传图片或拖入文档。"}, ensure_ascii=False) + "\n"
        return

    payload = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 2048
        }
    }

    last_err = None
    for current_model in candidate_models:
        for api_key in keys:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{current_model}:streamGenerateContent?alt=sse&key={api_key}"
            try:
                with client.stream("POST", url, json=payload, timeout=60.0) as resp:
                    if resp.status_code == 200:
                        full_text = ""
                        in_thought = False
                        try:
                            for line in resp.iter_lines():
                                if not line or not line.startswith("data: "):
                                    continue
                                data_str = line[6:].strip()
                                if not data_str or data_str == "[DONE]":
                                    continue
                                try:
                                    chunk_json = json.loads(data_str)
                                    candidates = chunk_json.get("candidates", [])
                                    if candidates and "content" in candidates[0]:
                                        parts = candidates[0]["content"].get("parts", [])
                                        for p in parts:
                                            t = p.get("text", "")
                                            is_thought = p.get("thought", False)
                                            if t:
                                                if is_thought:
                                                    if not in_thought:
                                                        in_thought = True
                                                        full_text += "<think>\n"
                                                        yield json.dumps({"text": "<think>\n"}, ensure_ascii=False) + "\n"
                                                    full_text += t
                                                    yield json.dumps({"text": t}, ensure_ascii=False) + "\n"
                                                else:
                                                    if in_thought:
                                                        in_thought = False
                                                        full_text += "\n</think>\n\n"
                                                        yield json.dumps({"text": "\n</think>\n\n"}, ensure_ascii=False) + "\n"
                                                    full_text += t
                                                    yield json.dumps({"text": t}, ensure_ascii=False) + "\n"
                                except Exception:
                                    continue
                        finally:
                            if in_thought:
                                full_text += "\n</think>\n\n"
                            if full_text:
                                saved_user_text = prompt.strip() or "分析与解答"
                                if image_data:
                                    saved_user_text = "[图片] " + (prompt.strip() if prompt else "图像分析")
                                elif file_data:
                                    saved_user_text = f"[文件: {file_data.get('name', '附件')}] " + (prompt.strip() if prompt else "文档分析")

                                db.save_chat_message(user_id, "user", saved_user_text, session_id=session_id)
                                db.save_chat_message(user_id, "assistant", full_text, session_id=session_id)
                        return
                    elif resp.status_code in [429, 503]:
                        print(f"[Fallback] 模型 {current_model} 繁忙 ({resp.status_code})，自动切换备用...")
                        last_err = f"模型 {current_model} 暂时繁忙"
                        time.sleep(0.2)
                        continue
                    else:
                        yield json.dumps({"text": f"接口异常 ({resp.status_code}): {resp.text[:200]}"}, ensure_ascii=False) + "\n"
                        return
            except Exception as e:
                last_err = e
                time.sleep(0.2)
                continue

    yield json.dumps({"text": f"当前全球请求高峰，请稍候再试 ({last_err})"}, ensure_ascii=False) + "\n"

def call_gemini(user_id: str, prompt: str, model_name: str = None, image_data: Dict[str, str] = None, file_data: Dict[str, Any] = None, session_id: str = "default") -> str:
    """非流式调用 (供降级或单次直调使用)"""
    full_resp = []
    for chunk_line in stream_gemini(user_id, prompt, model_name, image_data, file_data, session_id):
        try:
            chunk = json.loads(chunk_line)
            if "text" in chunk:
                full_resp.append(chunk["text"])
        except Exception:
            pass
    return "".join(full_resp)

def stream_agent(user_id: str, user_message: str, model_name: str = None, image_data: Dict[str, str] = None, file_data: Dict[str, Any] = None, session_id: str = "default"):
    """流式 Agent 主入口"""
    if not image_data and not file_data:
        note_resp = handle_quick_notes(user_id, user_message)
        if note_resp:
            yield json.dumps({"text": note_resp}, ensure_ascii=False) + "\n"
            return

        if user_message.startswith("搜索") or user_message.startswith("搜一下") or user_message.startswith("查一下"):
            query = user_message.lstrip("搜索：: 查一下搜一下")
            search_res = search.search_web(query)
            prompt_with_search = f"用户想要了解「{query}」，以下是网页检索到的参考资料：\n{search_res}\n\n请结合以上资料给出全面清晰的总结。"
            for chunk in stream_gemini(user_id, prompt_with_search, model_name=model_name, session_id=session_id):
                yield chunk
            return

    for chunk in stream_gemini(user_id, user_message, model_name=model_name, image_data=image_data, file_data=file_data, session_id=session_id):
        yield chunk

def run_agent(user_id: str, user_message: str, model_name: str = None, image_data: Dict[str, str] = None, file_data: Dict[str, Any] = None, session_id: str = "default") -> str:
    """标准同步主入口"""
    return call_gemini(user_id, user_message, model_name=model_name, image_data=image_data, file_data=file_data, session_id=session_id)
