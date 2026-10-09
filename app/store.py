import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager

DATA = Path(os.environ.get("RAG_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))

@contextmanager
def db():
    DATA.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATA / "knowledge.db", timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

def initialize():
    with db() as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript('''
            CREATE TABLE IF NOT EXISTS documents(
                id TEXT PRIMARY KEY, name TEXT NOT NULL, digest TEXT UNIQUE NOT NULL,
                pages INTEGER NOT NULL, bytes INTEGER NOT NULL, pdf BLOB NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            CREATE TABLE IF NOT EXISTS chunks(
                id INTEGER PRIMARY KEY, document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
                page INTEGER NOT NULL, text TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS chunks_document ON chunks(document_id);
            CREATE TABLE IF NOT EXISTS chats(
                id TEXT PRIMARY KEY, title TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY, chat_id TEXT REFERENCES chats(id) ON DELETE CASCADE,
                role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL,
                sources TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            );
            CREATE INDEX IF NOT EXISTS messages_chat ON messages(chat_id);
        ''')
