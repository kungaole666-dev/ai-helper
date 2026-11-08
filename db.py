import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Any, Optional

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
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
    # 尝试给已有历史表增加 session_id 字段
    try:
        cursor.execute("ALTER TABLE chat_history ADD COLUMN session_id TEXT DEFAULT 'default'")
    except Exception:
        pass

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_user_sess ON chat_history(user_id, session_id)")

    conn.commit()
    conn.close()

# 初始化数据库
init_db()

def ensure_session(user_id: str, session_id: str, title: str = None) -> str:
    """确保会话存在，若不存在则创建"""
    if not session_id:
        session_id = "default"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_FILE)
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
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, title, created_at, updated_at FROM sessions WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,)
    )
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        # 如果该用户暂无会话记录，自动创建一个默认会话
        ensure_session(user_id, "default", "新对话")
        return [{"id": "default", "title": "新对话", "created_at": "", "updated_at": ""}]
    return [
        {"id": r[0], "title": r[1], "created_at": r[2], "updated_at": r[3]}
        for r in rows
    ]

def delete_session(user_id: str, session_id: str) -> bool:
    """删除某条历史对话及所有消息"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE user_id = ? AND id = ?", (user_id, session_id))
    cursor.execute("DELETE FROM chat_history WHERE user_id = ? AND session_id = ?", (user_id, session_id))
    conn.commit()
    conn.close()
    return True

def add_note(user_id: str, content: str, category: str = "备忘") -> Dict[str, Any]:
    """为指定用户添加一条笔记/待办"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_FILE)
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
    conn = sqlite3.connect(DB_FILE)
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
    conn = sqlite3.connect(DB_FILE)
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
    conn = sqlite3.connect(DB_FILE)
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
    conn = sqlite3.connect(DB_FILE)
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
    conn = sqlite3.connect(DB_FILE)
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

def get_recent_users(limit: int = 10) -> List[str]:
    """获取最近活跃的账户列表"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT user_id FROM chat_history ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [r[0] for r in rows if r[0]]

def clear_chat_history(user_id: str, session_id: str = None) -> bool:
    """清空指定用户的聊天记忆"""
    conn = sqlite3.connect(DB_FILE)
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
