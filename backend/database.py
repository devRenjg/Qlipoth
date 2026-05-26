import aiosqlite
from pathlib import Path
from config import load_settings

settings = load_settings()
DB_PATH = settings.db_path


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_name TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                file_type TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                source_url TEXT,
                uploaded_at TIMESTAMP DEFAULT (datetime('now', '+8 hours'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS import_trees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                root_url TEXT NOT NULL,
                root_title TEXT NOT NULL,
                tree_data TEXT NOT NULL,
                doc_count INTEGER NOT NULL,
                imported_at TIMESTAMP DEFAULT (datetime('now', '+8 hours'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bili_uid TEXT UNIQUE NOT NULL,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                password_salt TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                token TEXT UNIQUE,
                avatar_url TEXT,
                last_seen TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                source_urls TEXT,
                created_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS failed_imports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT,
                error TEXT NOT NULL,
                parent_url TEXT,
                depth INTEGER DEFAULT 0,
                retry_count INTEGER DEFAULT 0,
                created_at TIMESTAMP,
                last_retry TIMESTAMP
            )
        """)
        # Migrations for existing DBs
        try:
            await db.execute("ALTER TABLE documents ADD COLUMN source_url TEXT")
        except Exception:
            pass
        await db.commit()
