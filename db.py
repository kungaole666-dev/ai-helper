import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Any

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.db")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # 记事表，基于 user_id 做严格数据隔离
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

    # 聊天上下文表 (保留最近几轮对话，实现连续对话记忆)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_history_user_id ON chat_history(user_id)")

    conn.commit()
    conn.close()

# 初始化数据库
init_db()

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

def get_notes(user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
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

def search_notes(user_id: str, keyword: str) -> List[Dict[str, Any]]:
    """在指定用户的笔记中按关键词搜索"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, content, category, created_at FROM notes WHERE user_id = ? AND content LIKE ? ORDER BY id DESC LIMIT 10",
        (user_id, f"%{keyword}%")
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

def save_chat_message(user_id: str, role: str, content: str):
    """保存一条聊天消息"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chat_history (user_id, role, content, created_at) VALUES (?, ?, ?, ?)",
        (user_id, role, content, now)
    )
    conn.commit()
    conn.close()

def get_chat_history(user_id: str, limit: int = 8) -> List[Dict[str, str]]:
    """获取指定用户的最近聊天记录 (供大模型上下文推理)"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, content FROM chat_history WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    rows.reverse()
    return [{"role": r[0], "content": r[1]} for r in rows]

def get_display_history(user_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    """获取前端渲染展示的历史记录清单"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, role, content, created_at FROM chat_history WHERE user_id = ? ORDER BY id ASC LIMIT ?",
        (user_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    return [
        {"id": r[0], "role": r[1], "content": r[2], "created_at": r[3]}
        for r in rows
    ]

def clear_chat_history(user_id: str) -> bool:
    """清空指定用户的全部聊天记忆 (新建对话)"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chat_history WHERE user_id = ?", (user_id,))
    affected = cursor.rowcount
    conn.commit()
    conn.close()
    return affected > 0
