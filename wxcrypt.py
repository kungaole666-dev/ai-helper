import base64
import hashlib
import struct
import secrets
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

class PKCS7Encoder:
    """提供基于 32 字节块大小的 PKCS7 算法加解密支持"""
    block_size = 32

    @classmethod
    def encode(cls, text_bytes: bytes) -> bytes:
        amount_to_pad = cls.block_size - (len(text_bytes) % cls.block_size)
        if amount_to_pad == 0:
            amount_to_pad = cls.block_size
        pad = bytes([amount_to_pad] * amount_to_pad)
        return text_bytes + pad

    @classmethod
    def decode(cls, decrypted_bytes: bytes) -> bytes:
        pad = decrypted_bytes[-1]
        if pad < 1 or pad > cls.block_size:
            pad = 0
        return decrypted_bytes[:-pad]

class WXBizMsgCrypt:
    def __init__(self, token: str, encoding_aes_key: str, corp_id: str):
        self.token = token
        self.corp_id = corp_id
        self.aes_key = base64.b64decode(encoding_aes_key + "=")
        if len(self.aes_key) != 32:
            raise ValueError("EncodingAESKey 无效，Base64 解码后必须为 32 字节")

    def _get_sha1(self, *args) -> str:
        items = sorted([str(arg) for arg in args])
        raw_str = "".join(items).encode("utf-8")
        return hashlib.sha1(raw_str).hexdigest()

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        """验证 URL 真实性并解密 echostr"""
        signature = self._get_sha1(self.token, timestamp, nonce, echostr)
        if signature != msg_signature:
            raise ValueError("签名验证失败")
        
        decrypted = self._decrypt(echostr)
        return decrypted.decode("utf-8")

    def decrypt_msg(self, msg_signature: str, timestamp: str, nonce: str, post_data: str) -> str:
        """从加密 XML 中解密出原始 XML 消息"""
        import xml.etree.ElementTree as ET
        root = ET.fromstring(post_data)
        encrypt = root.find("Encrypt").text

        signature = self._get_sha1(self.token, timestamp, nonce, encrypt)
        if signature != msg_signature:
            raise ValueError("消息签名验证失败")

        decrypted = self._decrypt(encrypt)
        return decrypted.decode("utf-8")

    def _decrypt(self, encrypt_b64: str) -> bytes:
        cipher_bytes = base64.b64decode(encrypt_b64)
        iv = self.aes_key[:16]
        cipher = Cipher(algorithms.AES(self.aes_key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        plain_padded = decryptor.update(cipher_bytes) + decryptor.finalize()
        plain = PKCS7Encoder.decode(plain_padded)

        # 结构: 16字节随机数 + 4字节内容长度 + 内容 + corp_id
        content_len = struct.unpack("!I", plain[16:20])[0]
        content = plain[20:20 + content_len]
        from_corp_id = plain[20 + content_len:].decode("utf-8")

        if from_corp_id != self.corp_id:
            raise ValueError(f"CorpID 不匹配: 收到 {from_corp_id}, 预期 {self.corp_id}")

        return content

    def encrypt_msg(self, reply_xml: str, nonce: str, timestamp: str = None) -> str:
        """将回复内容加密为 XML 格式"""
        import time
        if not timestamp:
            timestamp = str(int(time.time()))

        xml_bytes = reply_xml.encode("utf-8")
        random_bytes = secrets.token_bytes(16)
        msg_len = struct.pack("!I", len(xml_bytes))
        corp_id_bytes = self.corp_id.encode("utf-8")

        payload = random_bytes + msg_len + xml_bytes + corp_id_bytes
        padded_payload = PKCS7Encoder.encode(payload)

        iv = self.aes_key[:16]
        cipher = Cipher(algorithms.AES(self.aes_key), modes.CBC(iv), backend=default_backend())
        encryptor = cipher.encryptor()
        encrypted = encryptor.update(padded_payload) + encryptor.finalize()
        encrypt_b64 = base64.b64encode(encrypted).decode("utf-8")

        signature = self._get_sha1(self.token, timestamp, nonce, encrypt_b64)

        return f"""<xml>
<Encrypt><![CDATA[{encrypt_b64}]]></Encrypt>
<MsgSignature><![CDATA[{signature}]]></MsgSignature>
<TimeStamp>{timestamp}</TimeStamp>
<Nonce><![CDATA[{nonce}]]></Nonce>
</xml>"""
