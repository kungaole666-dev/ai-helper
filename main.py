import sys
import os
import xml.etree.ElementTree as ET
import threading
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from pydantic import BaseModel

sys.stdout.reconfigure(encoding='utf-8')

from config import cfg
from wxcrypt import WXBizMsgCrypt
from wechat_client import wx_client
from agent import run_agent
import db

app = FastAPI(title="AI 智能解题与记事助理")

HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "index.html")

# 1. 网页前端入口 (微信/手机/电脑浏览器打开即用)
@app.get("/", response_class=HTMLResponse)
def index_page():
    if os.path.exists(HTML_PATH):
        with open(HTML_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>前端页面 index.html 正在准备中...</h1>"

# 2. 网页端对话 API
class ChatRequest(BaseModel):
    user_id: str
    message: str

@app.post("/api/chat")
async def api_chat(req: ChatRequest):
    user_id = req.user_id.strip() or "guest_user"
    message = req.message.strip()
    if not message:
        return {"reply": "请输入您的问题或想要记录的备忘。"}

    print(f"💬 [Web 访客 {user_id}]: {message}")
    reply = run_agent(user_id=user_id, user_message=message)
    return {"reply": reply}

# 3. 备忘录清单 API (多用户隔离)
@app.get("/api/notes")
def api_get_notes(user_id: str = Query(...)):
    notes = db.get_notes(user_id=user_id, limit=30)
    return {"notes": notes}

@app.post("/api/notes/delete")
def api_delete_note(user_id: str = Query(...), note_id: int = Query(...)):
    success = db.delete_note(user_id=user_id, note_id=note_id)
    return {"success": success}

# 4. 企业微信回调 (保留备用)
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
