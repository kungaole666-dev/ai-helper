import sqlite3
import os
import hashlib
import secrets
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db")

def get_db_conn():
    """获取支持高并发读写的 SQLite 连接 (开启 WAL 模式，超时 20 秒)"""
    conn = sqlite3.connect(DB_FILE, timeout=20.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn

def init_db():
    conn = get_db_conn()
    cursor = conn.cursor()

    # 0. 用户安全认证表 (密码加盐哈希存储)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        username TEXT PRIMARY KEY,
        password_hash TEXT NOT NULL,
        salt TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """)

    # 用户登录凭证表 (安全 Token 校验)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_tokens (
        token TEXT PRIMARY KEY,
        username TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tokens_username ON user_tokens(username)")

    # 1. 记事表，基于 user_id 做严格数据隔离
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        content TEXT NOT NULL,
        category TEXT DEFAULT '备忘',
        created_at TEXT NOT NULL
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_notes_user_id ON notes(user_id)")

    # 2. 会话表 (每个用户可创建多个独立的对话主题)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, id)
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id)")

    # 3. 聊天上下文表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        session_id TEXT DEFAULT 'default',
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """)
    try:
        cursor.execute("ALTER TABLE chat_history ADD COLUMN session_id TEXT DEFAULT 'default'")
    except Exception:
        pass

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_user_sess ON chat_history(user_id, session_id)")

    conn.commit()
    conn.close()

# 初始化数据库
init_db()

# --- 用户认证与密码安全逻辑 ---

def hash_password(password: str, salt: str = None) -> Tuple[str, str]:
    if not salt:
        salt = secrets.token_hex(8)
    h = hashlib.sha256((password + salt).encode('utf-8')).hexdigest()
    return h, salt

def auth_or_register_user(username: str, password: str) -> Tuple[bool, str, Optional[str], Optional[str]]:
    """智能认证：若账户不存在则自动安全注册；若已存在则严格验证密码登录 (彻底解决云端重启导致的注册/登录冲突)"""
    username = username.strip()
    password = password.strip()
    if not username:
        return False, "请输入账户名称", None, None
    if not password:
        return False, "请输入账户密码", None, None

    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash, salt FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if not row:
        # 账户尚不存在 (全新用户，或 Render 容器休眠重启清空了数据库) -> 自动安全创建并登录
        pwd_hash, salt = hash_password(password)
        cursor.execute(
            "INSERT INTO users (username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
            (username, pwd_hash, salt, now)
        )
        token = secrets.token_hex(24)
        cursor.execute(
            "INSERT INTO user_tokens (token, username, created_at) VALUES (?, ?, ?)",
            (token, username, now)
        )
        conn.commit()
        conn.close()
        return True, "账户创建并登录成功", token, pwd_hash
    else:
        # 账户已存在 -> 严格比对密码哈希，确保只有本人可以进入
        pwd_hash, salt = row
        test_hash, _ = hash_password(password, salt)
        if test_hash != pwd_hash:
            conn.close()
            return False, "该账户密码不正确，请重新输入", None, None

        token = secrets.token_hex(24)
        cursor.execute(
            "INSERT INTO user_tokens (token, username, created_at) VALUES (?, ?, ?)",
            (token, username, now)
        )
        conn.commit()
        conn.close()
        return True, "登录成功", token, pwd_hash

def reset_user_password(username: str, new_password: str) -> Tuple[bool, str, Optional[str], Optional[str]]:
    """重置或强制更新账号密码并直接登录"""
    username = username.strip()
    new_password = new_password.strip()
    if not username:
        return False, "请输入账户名称", None, None
    if not new_password:
        return False, "请输入新密码", None, None

    pwd_hash, salt = hash_password(new_password)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    if not row:
        cursor.execute(
            "INSERT INTO users (username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
            (username, pwd_hash, salt, now)
        )
    else:
        cursor.execute(
            "UPDATE users SET password_hash = ?, salt = ? WHERE username = ?",
            (pwd_hash, salt, username)
        )
    token = secrets.token_hex(24)
    cursor.execute(
        "INSERT INTO user_tokens (token, username, created_at) VALUES (?, ?, ?)",
        (token, username, now)
    )
    conn.commit()
    conn.close()
    return True, "密码已更新并成功进入", token, pwd_hash

def register_user(username: str, password: str) -> Tuple[bool, str, Optional[str], Optional[str]]:
    return auth_or_register_user(username, password)

def login_user(username: str, password: str) -> Tuple[bool, str, Optional[str], Optional[str]]:
    return auth_or_register_user(username, password)

def restore_user(username: str, user_key: str) -> Tuple[bool, str, Optional[str]]:
    """当 Render 免费容器重启导致 SQLite 被清空时，前端凭借安全凭证自动无感恢复账户"""
    username = username.strip()
    if not username or not user_key:
        return False, "无效凭证", None
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not row:
        # 容器重启重置了数据库，无缝重建该用户
        salt = secrets.token_hex(8)
        cursor.execute(
            "INSERT INTO users (username, password_hash, salt, created_at) VALUES (?, ?, ?, ?)",
            (username, user_key, salt, now)
        )
    else:
        if row[0] != user_key:
            conn.close()
            return False, "凭证校验失败", None

    token = secrets.token_hex(24)
    cursor.execute(
        "INSERT INTO user_tokens (token, username, created_at) VALUES (?, ?, ?)",
        (token, username, now)
    )
    conn.commit()
    conn.close()
    return True, "账户恢复成功", token

def sync_push_history(user_id: str, session_id: str, title: str, messages: List[Dict[str, str]]):
    """当云端数据库被重置时，支持从前端缓存中无缝恢复历史会话和聊天记录"""
    if not user_id or not messages:
        return
    ensure_session(user_id, session_id, title=title or "已恢复对话")
    conn = get_db_conn()
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if content:
            cursor.execute(
                "INSERT INTO chat_history (user_id, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, session_id, role, content, msg.get("created_at") or now)
            )
    conn.commit()
    conn.close()

def verify_token(token: str) -> Optional[str]:
    """通过安全 Token 获取真实绑定的用户名，防止伪造身份访问他人数据"""
    if not token or not token.strip():
        return None
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM user_tokens WHERE token = ?", (token.strip(),))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def logout_user(token: str) -> bool:
    if not token:
        return True
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM user_tokens WHERE token = ?", (token.strip(),))
    conn.commit()
    conn.close()
    return True

# --- 会话与对话记录逻辑 (基于真实 username 隔离) ---

def ensure_session(user_id: str, session_id: str, title: str = None) -> str:
    """确保会话存在，若不存在则创建"""
    if not session_id:
        session_id = "default"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM sessions WHERE user_id = ? AND id = ?", (user_id, session_id))
    row = cursor.fetchone()
    if not row:
        sess_title = (title[:30] if title else "新对话")
        cursor.execute(
            "INSERT INTO sessions (id, user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, user_id, sess_title, now, now)
        )
    elif title and row[1] in ["新对话", "未命名对话", "default", "当前对话"]:
        cursor.execute(
            "UPDATE sessions SET title = ?, updated_at = ? WHERE user_id = ? AND id = ?",
            (title[:30], now, user_id, session_id)
        )
    else:
        cursor.execute(
            "UPDATE sessions SET updated_at = ? WHERE user_id = ? AND id = ?",
            (now, user_id, session_id)
        )
    conn.commit()
    conn.close()
    return session_id

def get_user_sessions(user_id: str) -> List[Dict[str, Any]]:
    """获取指定用户的全部会话列表"""
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, title, created_at, updated_at FROM sessions WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        ensure_session(user_id, "default", "新对话")
        return [{"id": "default", "title": "新对话", "created_at": "", "updated_at": ""}]
    return [
        {"id": r[0], "title": r[1], "created_at": r[2], "updated_at": r[3]}
        for r in rows
    ]

def delete_session(user_id: str, session_id: str) -> bool:
    """删除某条历史对话及所有消息"""
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE user_id = ? AND id = ?", (user_id, session_id))
    cursor.execute("DELETE FROM chat_history WHERE user_id = ? AND session_id = ?", (user_id, session_id))
    conn.commit()
    conn.close()
    return True

def rename_session(user_id: str, session_id: str, new_title: str) -> bool:
    """重命名会话标题"""
    if not new_title or not new_title.strip():
        return False
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE sessions SET title = ?, updated_at = ? WHERE user_id = ? AND id = ?",
        (new_title.strip()[:50], now, user_id, session_id)
    )
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def delete_last_assistant_message(user_id: str, session_id: str) -> Optional[str]:
    """删除最近一条 assistant 消息供重新生成，并返回对应上一条 user 消息"""
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, role, content FROM chat_history WHERE user_id = ? AND session_id = ? ORDER BY id DESC LIMIT 2",
        (user_id, session_id)
    )
    rows = cursor.fetchall()
    if not rows:
        conn.close()
        return None

    last_user_prompt = None
    if rows[0][1] == "assistant":
        cursor.execute("DELETE FROM chat_history WHERE id = ?", (rows[0][0],))
        conn.commit()
        if len(rows) > 1 and rows[1][1] == "user":
            last_user_prompt = rows[1][2]
    elif rows[0][1] == "user":
        last_user_prompt = rows[0][2]

    conn.close()
    return last_user_prompt

def add_note(user_id: str, content: str, category: str = "备忘") -> Dict[str, Any]:
    """为指定用户添加一条笔记/待办"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO notes (user_id, content, category, created_at) VALUES (?, ?, ?, ?)",
        (user_id, content, category, now)
    )
    note_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return {"id": note_id, "content": content, "category": category, "created_at": now}

def get_notes(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """获取指定用户的最近笔记列表"""
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, content, category, created_at FROM notes WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    return [
        {"id": r[0], "content": r[1], "category": r[2], "created_at": r[3]}
        for r in rows
    ]

def delete_note(user_id: str, note_id: int) -> bool:
    """删除指定用户的某条笔记"""
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM notes WHERE user_id = ? AND id = ?", (user_id, note_id))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0

def save_chat_message(user_id: str, role: str, content: str, session_id: str = "default"):
    """保存一条聊天消息"""
    if not session_id:
        session_id = "default"
    ensure_session(user_id, session_id, title=content if role == "user" else None)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chat_history (user_id, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, session_id, role, content, now)
    )
    conn.commit()
    conn.close()

def get_chat_history(user_id: str, session_id: str = "default", limit: int = 8) -> List[Dict[str, str]]:
    """获取指定会话的最近聊天记录 (供大模型上下文推理)"""
    if not session_id:
        session_id = "default"
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, content FROM chat_history WHERE user_id = ? AND session_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, session_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    rows.reverse()
    return [{"role": r[0], "content": r[1]} for r in rows]

def get_display_history(user_id: str, session_id: str = "default", limit: int = 100) -> List[Dict[str, Any]]:
    """获取前端渲染展示的历史记录清单 (按时间升序排好)"""
    if not session_id:
        session_id = "default"
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, role, content, created_at FROM (
            SELECT id, role, content, created_at FROM chat_history 
            WHERE user_id = ? AND session_id = ?
            ORDER BY id DESC LIMIT ?
        ) ORDER BY id ASC
        """,
        (user_id, session_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    return [
        {"id": r[0], "role": r[1], "content": r[2], "created_at": r[3]}
        for r in rows
    ]

def clear_chat_history(user_id: str, session_id: str = None) -> bool:
    """清空指定用户的聊天记忆"""
    conn = get_db_conn()
    cursor = conn.cursor()
    if session_id:
        cursor.execute("DELETE FROM chat_history WHERE user_id = ? AND session_id = ?", (user_id, session_id))
        cursor.execute("DELETE FROM sessions WHERE user_id = ? AND id = ?", (user_id, session_id))
    else:
        cursor.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
        cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0
