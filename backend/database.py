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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT (datetime('now', '+8 hours'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS document_tags (
                document_id INTEGER NOT NULL,
                tag_id INTEGER NOT NULL,
                PRIMARY KEY (document_id, tag_id),
                FOREIGN KEY (document_id) REFERENCES documents(id),
                FOREIGN KEY (tag_id) REFERENCES tags(id)
            )
        """)
        # Migrations for existing DBs
        try:
            await db.execute("ALTER TABLE documents ADD COLUMN source_url TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE tags ADD COLUMN description TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE chat_history ADD COLUMN conversation_id TEXT")
        except Exception:
            pass
        try:
            await db.execute("ALTER TABLE chat_history ADD COLUMN selected_tags TEXT")
        except Exception:
            pass
        try:
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_chat_conv ON chat_history(conversation_id, id)"
            )
        except Exception:
            pass
        try:
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_doctag_tag ON document_tags(tag_id)"
            )
        except Exception:
            pass
        try:
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_doctag_doc ON document_tags(document_id)"
            )
        except Exception:
            pass
        # 保障清单（历史踩坑预警）：一份清单 + 多条结构化踩坑条目
        await db.execute("""
            CREATE TABLE IF NOT EXISTS checklists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                activity TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                source_doc_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT (datetime('now', '+8 hours')),
                created_by TEXT DEFAULT ''
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS checklist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                checklist_id INTEGER NOT NULL,
                dimension TEXT NOT NULL,
                phenomenon TEXT,
                cause TEXT,
                handling TEXT,
                suggestion TEXT,
                timing TEXT,
                source_files TEXT,
                handled INTEGER DEFAULT 0,
                sort_order INTEGER DEFAULT 0,
                FOREIGN KEY (checklist_id) REFERENCES checklists(id)
            )
        """)
        try:
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_checklist_item ON checklist_items(checklist_id)"
            )
        except Exception:
            pass
        # checklist_items 增量字段：阶段(排序)、团队/负责人(归属)、勾选追溯
        for col, ddl in [
            ("stage", "ALTER TABLE checklist_items ADD COLUMN stage TEXT"),
            ("team", "ALTER TABLE checklist_items ADD COLUMN team TEXT"),
            ("owner", "ALTER TABLE checklist_items ADD COLUMN owner TEXT"),
            ("handled_by", "ALTER TABLE checklist_items ADD COLUMN handled_by TEXT"),
            ("handled_at", "ALTER TABLE checklist_items ADD COLUMN handled_at TEXT"),
            ("cross_from", "ALTER TABLE checklist_items ADD COLUMN cross_from TEXT"),
            ("severity", "ALTER TABLE checklist_items ADD COLUMN severity TEXT"),
        ]:
            try:
                await db.execute(ddl)
            except Exception:
                pass
        await db.commit()
