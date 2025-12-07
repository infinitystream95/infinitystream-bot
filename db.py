import sqlite3

DB_PATH = "requests.db"


def get_connection():
    return sqlite3.connect(DB_PATH)


def init_db():
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                title TEXT NOT NULL,
                year INTEGER,
                category TEXT NOT NULL, -- 'film' ou 'serie'
                status TEXT NOT NULL,   -- 'file_attente', 'en_cours', 'traitee'
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        conn.commit()


def add_request(user_id: str, platform: str, title: str, year: int, category: str) -> int:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO requests (user_id, platform, title, year, category, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, platform, title, year, category, "file_attente"),
        )
        conn.commit()
        return cur.lastrowid


def list_open_requests():
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, user_id, platform, title, year, category, status, created_at
            FROM requests
            WHERE status != 'traitee'
            ORDER BY created_at DESC
            """
        )
        return cur.fetchall()


def list_all_requests():
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, user_id, platform, title, year, category, status, created_at
            FROM requests
            ORDER BY created_at DESC
            """
        )
        return cur.fetchall()


def update_status(request_id: int, new_status: str) -> bool:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE requests SET status = ? WHERE id = ?",
            (new_status, request_id),
        )
        conn.commit()
        return cur.rowcount > 0


def delete_request(request_id: int) -> bool:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM requests WHERE id = ?", (request_id,))
        conn.commit()
        return cur.rowcount > 0
