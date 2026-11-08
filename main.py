import sys
import os
import xml.etree.ElementTree as ET
import threading
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel
import json
from typing import Optional, Dict, Any

sys.stdout.reconfigure(encoding='utf-8')

from config import cfg
from wxcrypt import WXBizMsgCrypt
from wechat_client import wx_client
from agent import run_agent, stream_agent
import db

app = FastAPI(title="AI")

HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
if not os.path.exists(HTML_PATH):
    HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "index.html")

# 1. 网页前端入口与 PWA 桌面配置
@app.get("/", response_class=HTMLResponse)
def index_page():
    if os.path.exists(HTML_PATH):
        with open(HTML_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>前端页面 index.html 正在准备中...</h1>"

@app.get("/manifest.json")
def pwa_manifest():
    return JSONResponse({
        "name": "AI 智能助手",
        "short_name": "AI",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": "#18181b",
        "icons": [
            {
                "src": "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'><rect width='512' height='512' rx='115' fill='%2318181b'/><text x='50%25' y='54%25' dominant-baseline='middle' text-anchor='middle' font-family='-apple-system,sans-serif' font-weight='800' font-size='220' fill='%23ffffff'>AI</text></svg>",
                "sizes": "512x512",
                "type": "image/svg+xml",
                "purpose": "any maskable"
            }
        ]
    })

# 2. 账户认证接口 (密码加盐哈希保护，杜绝他人查看数据)
class AuthRequest(BaseModel):
    username: str
    password: str

class LogoutRequest(BaseModel):
    token: str

class RestoreRequest(BaseModel):
    username: str
    user_key: str

class SyncHistoryRequest(BaseModel):
    token: str
    sessions: list

@app.post("/api/auth/register")
def api_register(req: AuthRequest):
    success, msg, token, user_key = db.register_user(req.username, req.password)
    if not success:
        return {"success": False, "message": msg}
    return {"success": True, "message": msg, "token": token, "username": req.username.strip(), "user_key": user_key}

@app.post("/api/auth/login")
def api_login(req: AuthRequest):
    success, msg, token, user_key = db.login_user(req.username, req.password)
    if not success:
        return {"success": False, "message": msg}
    return {"success": True, "message": msg, "token": token, "username": req.username.strip(), "user_key": user_key}

@app.post("/api/auth/restore")
def api_restore(req: RestoreRequest):
    success, msg, token = db.restore_user(req.username, req.user_key)
    if not success:
        return {"success": False, "message": msg}
    return {"success": True, "token": token, "username": req.username.strip()}

@app.post("/api/auth/sync_history")
def api_sync_history(req: SyncHistoryRequest):
    user_id = db.verify_token(req.token)
    if not user_id:
        return {"success": False}
    for sess in req.sessions:
        db.sync_push_history(user_id, sess.get("id"), sess.get("title"), sess.get("messages", []))
    return {"success": True}

@app.get("/api/auth/me")
def api_auth_me(token: str = Query(...)):
    username = db.verify_token(token)
    if not username:
        return {"authenticated": False}
    return {"authenticated": True, "username": username}

@app.post("/api/auth/logout")
def api_logout(req: LogoutRequest):
    db.logout_user(req.token)
    return {"success": True}

# 3. 网页端对话 API (强制 Token 认证，数据仅自己可见)
class ChatRequest(BaseModel):
    token: str
    session_id: Optional[str] = "default"
    message: str
    model: Optional[str] = None
    image: Optional[Dict[str, str]] = None
    file: Optional[Dict[str, Any]] = None

@app.post("/api/chat")
def api_chat(req: ChatRequest):
    try:
        user_id = db.verify_token(req.token)
        if not user_id:
            return {"reply": "未登录或登录已失效，请重新登录账户。"}

        session_id = req.session_id.strip() if req.session_id else "default"
        message = req.message.strip()
        model_name = req.model.strip() if req.model else None
        image_data = req.image if (req.image and req.image.get("data")) else None
        file_data = req.file if (req.file and (req.file.get("data") or req.file.get("text_content"))) else None

        if not message and not image_data and not file_data:
            return {"reply": "请输入您的问题、上传图片或拖入文件。"}

        print(f"💬 [Web 用户 {user_id} 会话 {session_id} 模型 {model_name} 图:{bool(image_data)} 文档:{bool(file_data)}]: {message[:30]}")
        reply = run_agent(user_id=user_id, user_message=message, model_name=model_name, image_data=image_data, file_data=file_data, session_id=session_id)
        return {"reply": reply}
    except Exception as e:
        print(f"❌ api_chat 处理异常: {e}")
        return {"reply": f"处理异常: {str(e)}"}

@app.post("/api/chat/stream")
def api_chat_stream(req: ChatRequest):
    """流式返回接口 (SSE 打字机输出，首字秒级返回)"""
    try:
        user_id = db.verify_token(req.token)
        if not user_id:
            def unauth_gen():
                yield json.dumps({"text": "未登录或登录已失效，请重新登录。"}, ensure_ascii=False) + "\n"
            return StreamingResponse(unauth_gen(), media_type="text/event-stream")

        session_id = req.session_id.strip() if req.session_id else "default"
        message = req.message.strip()
        model_name = req.model.strip() if req.model else None
        image_data = req.image if (req.image and req.image.get("data")) else None
        file_data = req.file if (req.file and (req.file.get("data") or req.file.get("text_content"))) else None

        if not message and not image_data and not file_data:
            def empty_gen():
                yield json.dumps({"text": "请输入您的问题、上传图片或拖入文件。"}, ensure_ascii=False) + "\n"
            return StreamingResponse(empty_gen(), media_type="text/event-stream")

        print(f"⚡ [Stream 用户 {user_id} 会话 {session_id} 模型 {model_name}]: {message[:30]}")
        
        def stream_gen():
            for chunk in stream_agent(user_id=user_id, user_message=message, model_name=model_name, image_data=image_data, file_data=file_data, session_id=session_id):
                yield chunk

        return StreamingResponse(stream_gen(), media_type="text/event-stream")
    except Exception as e:
        def err_gen():
            yield json.dumps({"text": f"请求异常: {str(e)}"}, ensure_ascii=False) + "\n"
        return StreamingResponse(err_gen(), media_type="text/event-stream")

# 4. 历史记录与多会话 API (严格鉴权)
@app.get("/api/sessions")
def api_get_sessions(token: str = Query(...)):
    user_id = db.verify_token(token)
    if not user_id:
        return {"sessions": []}
    sessions = db.get_user_sessions(user_id=user_id)
    return {"sessions": sessions}

class RenameSessionRequest(BaseModel):
    token: str
    session_id: str
    title: str

@app.post("/api/sessions/rename")
def api_rename_session(req: RenameSessionRequest):
    user_id = db.verify_token(req.token)
    if not user_id:
        return {"success": False, "message": "未登录"}
    success = db.rename_session(user_id=user_id, session_id=req.session_id, new_title=req.title)
    return {"success": success}

class DeleteLastRequest(BaseModel):
    token: str
    session_id: str

@app.post("/api/chat/delete_last")
def api_delete_last(req: DeleteLastRequest):
    user_id = db.verify_token(req.token)
    if not user_id:
        return {"success": False, "message": "未登录"}
    last_prompt = db.delete_last_assistant_message(user_id=user_id, session_id=req.session_id)
    return {"success": True, "prompt": last_prompt}

@app.post("/api/sessions/delete")
def api_delete_session(token: str = Query(...), session_id: str = Query(...)):
    user_id = db.verify_token(token)
    if not user_id:
        return {"success": False}
    success = db.delete_session(user_id=user_id, session_id=session_id)
    return {"success": success}

@app.get("/api/history")
def api_get_history(token: str = Query(...), session_id: Optional[str] = Query(None)):
    user_id = db.verify_token(token)
    if not user_id:
        return {"history": []}
    history = db.get_display_history(user_id=user_id, session_id=session_id or "default", limit=100)
    return {"history": history}

@app.post("/api/clear_history")
def api_clear_history(token: str = Query(...), session_id: Optional[str] = Query(None)):
    user_id = db.verify_token(token)
    if not user_id:
        return {"success": False}
    success = db.clear_chat_history(user_id=user_id, session_id=session_id)
    return {"success": success}

# 5. 备忘录清单 API (严格鉴权)
class NoteAddRequest(BaseModel):
    token: str
    content: str
    category: Optional[str] = "备忘"

@app.get("/api/notes")
def api_get_notes(token: str = Query(...)):
    user_id = db.verify_token(token)
    if not user_id:
        return {"notes": []}
    notes = db.get_notes(user_id=user_id, limit=50)
    return {"notes": notes}

@app.post("/api/notes/add")
def api_add_note(req: NoteAddRequest):
    user_id = db.verify_token(req.token)
    if not user_id:
        return {"success": False, "message": "未登录"}
    note = db.add_note(user_id=user_id, content=req.content, category=req.category or "备忘")
    return {"success": True, "note": note}

@app.post("/api/notes/delete")
def api_delete_note(token: str = Query(...), note_id: int = Query(...)):
    user_id = db.verify_token(token)
    if not user_id:
        return {"success": False}
    success = db.delete_note(user_id=user_id, note_id=note_id)
    return {"success": success}

# 6. 企业微信回调 (保留备用)
def get_crypt():
    return WXBizMsgCrypt(
        token=cfg.get("TOKEN"),
        encoding_aes_key=cfg.get("ENCODING_AES_KEY"),
        corp_id=cfg.get("CORP_ID")
    )

@app.get("/wechat/callback", response_class=PlainTextResponse)
def verify_wechat(
    msg_signature: str = Query(..., alias="msg_signature"),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...)
):
    try:
        crypt = get_crypt()
        return crypt.verify_url(msg_signature, timestamp, nonce, echostr)
    except Exception as e:
        return PlainTextResponse(str(e), status_code=403)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", cfg.get("PORT", 8000)))
    print(f"🚀 服务启动中，请在浏览器打开: http://localhost:{port}")
    uvicorn.run("main:app", host="0.0.0.0", port=port)
