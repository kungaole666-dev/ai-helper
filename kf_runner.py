import sys
import time
import threading
from typing import Set

sys.stdout.reconfigure(encoding='utf-8')

from config import cfg
from kf_client import WeChatKFClient
from agent import run_agent

def start_kf_poller():
    corp_id = cfg.get("CORP_ID")
    kf_secret = cfg.get("KF_SECRET", "").strip()
    open_kfid = cfg.get("OPEN_KFID", "").strip()

    if not kf_secret:
        print("❌ 未配置 KF_SECRET，请在 config.json 中填入微信客服专用的 Secret！")
        return

    client = WeChatKFClient(corp_id=corp_id, kf_secret=kf_secret)

    # 1. 自动校验或获取客服账号
    try:
        accounts = client.get_account_list()
        print(f"✅ 成功连接微信客服！当前可用客服账号数量: {len(accounts)}")
        for acc in accounts:
            print(f"   • 客服名: {acc.get('name')}, open_kfid: {acc.get('open_kfid')}")
        if not open_kfid and accounts:
            open_kfid = accounts[0].get("open_kfid")
            print(f"👉 自动选中第一个客服账号: {open_kfid}")
    except Exception as e:
        print(f"❌ 微信客服鉴权异常: {e}")
        return

    # 2. 初始化 cursor（跳过历史消息，从当前最新点开始）
    cursor = ""
    res = client.sync_msg(cursor="", open_kfid=open_kfid)
    if res.get("errcode") == 0:
        cursor = res.get("next_cursor", "")
        print("🚀 微信客服实时监听已就绪，正在等待微信端用户发消息...")
    else:
        print(f"初始化 sync_msg 异常: {res}")
        return

    processed_msg_ids: Set[str] = set()

    # 3. 循环拉取消息
    while True:
        try:
            res = client.sync_msg(cursor=cursor, open_kfid=open_kfid)
            if res.get("errcode") == 0:
                cursor = res.get("next_cursor", cursor)
                msg_list = res.get("msg_list", [])

                for m in msg_list:
                    msg_id = m.get("msgid")
                    if msg_id in processed_msg_ids:
                        continue
                    processed_msg_ids.add(msg_id)
                    # 保留最近 1000 个 msg_id
                    if len(processed_msg_ids) > 1000:
                        processed_msg_ids.pop()

                    msg_type = m.get("msgtype")
                    from_user = m.get("external_userid", "")
                    origin = m.get("origin", 0)  # 3 代表客户发送的消息
                    current_kfid = m.get("open_kfid", open_kfid)

                    # 只要是客户发送的文本消息
                    if msg_type == "text" and from_user:
                        content = m.get("text", {}).get("content", "").strip()
                        print(f"\n📩 收到微信客户 [{from_user}] 消息: {content}")

                        # 启动异步线程处理
                        threading.Thread(
                            target=handle_user_message,
                            args=(client, from_user, current_kfid, content),
                            daemon=True
                        ).start()

            else:
                print(f"[KF Polling Warn] {res}")

        except Exception as e:
            print(f"[KF Polling Error] {e}")

        # 每隔 1.5 秒轮询一次
        time.sleep(1.5)

def handle_user_message(client: WeChatKFClient, user_id: str, kfid: str, content: str):
    """异步处理消息并推回微信"""
    try:
        print(f"🤖 正在为用户 [{user_id}] 思考（Gemini 推理/记事）...")
        reply = run_agent(user_id=user_id, user_message=content)
        print(f"📤 正在推回给微信客服窗口 [{user_id}]...")
        client.send_msg(to_user=user_id, open_kfid=kfid, content=reply)
        print("✅ 推送成功！用户微信已收到回复。")
    except Exception as e:
        print(f"❌ 处理回复失败: {e}")

if __name__ == "__main__":
    start_kf_poller()
