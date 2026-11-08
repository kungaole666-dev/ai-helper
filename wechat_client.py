import time
import httpx
from typing import Optional
from config import cfg

class WeChatClient:
    def __init__(self):
        self.corp_id = cfg.get("CORP_ID")
        self.secret = cfg.get("SECRET")
        self.agent_id = cfg.get("AGENT_ID")
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0

    def get_access_token(self) -> str:
        """获取企业微信 access_token，带自动缓存与过期刷新"""
        now = time.time()
        if self._access_token and now < self._token_expires_at - 300:
            return self._access_token

        url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={self.corp_id}&corpsecret={self.secret}"
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.get(url)
                data = resp.json()
                if data.get("errcode") == 0:
                    self._access_token = data.get("access_token")
                    expires_in = data.get("expires_in", 7200)
                    self._token_expires_at = now + expires_in
                    return self._access_token
                else:
                    raise Exception(f"获取 access_token 失败: {data}")
        except Exception as e:
            print(f"[WeChat API Error] {e}")
            raise

    def send_text(self, to_user: str, content: str):
        """主动给指定微信用户推送文本消息 (超过微信长度限制时自动分段发送)"""
        token = self.get_access_token()
        url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={token}"

        # 微信企业号单条文本上限大约 2048 字节，中文约 600-700 字，分片处理长解答
        chunks = self._split_text(content, max_chars=800)

        with httpx.Client(timeout=15.0) as client:
            for chunk in chunks:
                payload = {
                    "touser": to_user,
                    "msgtype": "text",
                    "agentid": self.agent_id,
                    "text": {
                        "content": chunk
                    },
                    "safe": 0
                }
                try:
                    resp = client.post(url, json=payload)
                    res_data = resp.json()
                    if res_data.get("errcode") != 0:
                        print(f"[WeChat Push Error] 消息推送失败: {res_data}")
                except Exception as e:
                    print(f"[WeChat Push Exception] {e}")

    @staticmethod
    def _split_text(text: str, max_chars: int = 800) -> list:
        if len(text) <= max_chars:
            return [text]
        chunks = []
        start = 0
        while start < len(text):
            chunks.append(text[start:start + max_chars])
            start += max_chars
        return chunks

wx_client = WeChatClient()
