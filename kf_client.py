import time
import httpx
from typing import Optional, Dict, Any, List
from config import cfg

class WeChatKFClient:
    def __init__(self, corp_id: str, kf_secret: str):
        self.corp_id = corp_id
        self.kf_secret = kf_secret
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0

    def get_access_token(self) -> str:
        """获取微信客服专用的 access_token"""
        now = time.time()
        if self._access_token and now < self._token_expires_at - 300:
            return self._access_token

        url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={self.corp_id}&corpsecret={self.kf_secret}"
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
                    raise Exception(f"获取微信客服 access_token 失败: {data}")
        except Exception as e:
            print(f"[KF Token Error] {e}")
            raise

    def get_account_list(self) -> List[Dict[str, Any]]:
        """获取当前配置的所有客服账号"""
        token = self.get_access_token()
        url = f"https://qyapi.weixin.qq.com/cgi-bin/kf/account/list?access_token={token}"
        with httpx.Client(timeout=10.0) as client:
            resp = client.get(url)
            data = resp.json()
            if data.get("errcode") == 0:
                return data.get("account_list", [])
            else:
                print(f"[KF Account Error] {data}")
                return []

    def sync_msg(self, cursor: str = "", open_kfid: str = "") -> Dict[str, Any]:
        """主动同步/拉取客服收到的最新消息 (无需公网域名与回调 URL!)"""
        token = self.get_access_token()
        url = f"https://qyapi.weixin.qq.com/cgi-bin/kf/sync_msg?access_token={token}"
        payload = {
            "token": cursor,
            "limit": 500
        }
        if open_kfid:
            payload["open_kfid"] = open_kfid

        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, json=payload)
            return resp.json()

    def send_msg(self, to_user: str, open_kfid: str, content: str):
        """发送消息给微信客服会话中的外部用户"""
        token = self.get_access_token()
        url = f"https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg?access_token={token}"

        # 超过长度限制时分段发送
        chunks = self._split_text(content, max_chars=800)

        with httpx.Client(timeout=15.0) as client:
            for chunk in chunks:
                payload = {
                    "touser": to_user,
                    "open_kfid": open_kfid,
                    "msgtype": "text",
                    "text": {
                        "content": chunk
                    }
                }
                try:
                    resp = client.post(url, json=payload)
                    res_data = resp.json()
                    if res_data.get("errcode") != 0:
                        print(f"[KF Send Error] 回复推送失败: {res_data}")
                except Exception as e:
                    print(f"[KF Send Exception] {e}")

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
