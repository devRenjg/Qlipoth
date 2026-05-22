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
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS import_trees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                root_url TEXT NOT NULL,
                root_title TEXT NOT NULL,
                tree_data TEXT NOT NULL,
                doc_count INTEGER NOT NULL,
                imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Add source_url column if missing (migration for existing DBs)
        try:
            await db.execute("ALTER TABLE documents ADD COLUMN source_url TEXT")
        except Exception:
            pass
        await db.commit()
