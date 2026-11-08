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

def register_user(username: str, password: str) -> Tuple[bool, str, Optional[str]]:
    """注册新账户并赋予加密密码"""
    username = username.strip()
    password = password.strip()
    if not username or len(username) < 2:
        return False, "用户名长度至少需 2 个字符", None
    if not password or len(password) < 4:
        return False, "密码长度至少需 4 个字符", None

    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users WHERE username = ?", (username,))
    if cursor.fetchone():
        conn.close()
        return False, "该账户已存在，请直接输入密码登录", None

    pwd_hash, salt = hash_password(password)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
    return True, "注册成功", token

def login_user(username: str, password: str) -> Tuple[bool, str, Optional[str]]:
    """验证账户与密码"""
    username = username.strip()
    password = password.strip()
    conn = get_db_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash, salt FROM users WHERE username = ?", (username,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return False, "账户不存在，请切换至【注册】创建账户", None

    pwd_hash, salt = row
    test_hash, _ = hash_password(password, salt)
    if test_hash != pwd_hash:
        conn.close()
        return False, "密码错误，请重新输入", None

    token = secrets.token_hex(24)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "INSERT INTO user_tokens (token, username, created_at) VALUES (?, ?, ?)",
        (token, username, now)
    )
    conn.commit()
    conn.close()
    return True, "登录成功", token

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
