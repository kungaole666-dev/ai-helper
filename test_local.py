import sys
import os

sys.stdout.reconfigure(encoding='utf-8')
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import db
from wxcrypt import WXBizMsgCrypt

def test():
    print("1. 测试数据库多用户隔离...")
    # 用户 A 记录
    res_a = db.add_note(user_id="user_A", content="买牛奶", category="待办")
    # 用户 B 记录
    res_b = db.add_note(user_id="user_B", content="准备高数考试", category="学习")

    notes_a = db.get_notes(user_id="user_A")
    notes_b = db.get_notes(user_id="user_B")

    assert any(n["content"] == "买牛奶" for n in notes_a), "用户A笔记未找到"
    assert not any(n["content"] == "准备高数考试" for n in notes_a), "用户A看到了用户B的笔记，隔离失败！"
    assert any(n["content"] == "准备高数考试" for n in notes_b), "用户B笔记未找到"
    print("✅ 数据库多用户隔离测试通过！")

    print("2. 测试企业微信加解密机制...")
    token = "test_token_123"
    # 43位的合法 EncodingAESKey (base64解码后32字节)
    aes_key = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
    corp_id = "wx_test_corp_123"

    crypt = WXBizMsgCrypt(token, aes_key, corp_id)

    # 测试签名和加解密
    raw_xml = "<xml><ToUserName><![CDATA[wx_test_corp_123]]></ToUserName><FromUserName><![CDATA[user_A]]></FromUserName><MsgType><![CDATA[text]]></MsgType><Content><![CDATA[你好，帮我解一道题]]></Content></xml>"
    encrypted_xml = crypt.encrypt_msg(raw_xml, nonce="nonce_123")
    
    # 提取生成的 xml
    import xml.etree.ElementTree as ET
    root = ET.fromstring(encrypted_xml)
    sig = root.find("MsgSignature").text
    ts = root.find("TimeStamp").text
    nonce = root.find("Nonce").text

    decrypted = crypt.decrypt_msg(sig, ts, nonce, encrypted_xml)
    assert decrypted == raw_xml, "解密内容与原文不符！"
    print("✅ 企微加解密与验签逻辑测试通过！")

if __name__ == "__main__":
    test()
