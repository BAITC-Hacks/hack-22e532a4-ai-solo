"""Persistent request cap, not a dollar estimate. Reservations survive failed requests."""
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from .config import settings


def reserve_request():
    path = settings.db_path.with_name('api-budget.sqlite3')
    day = datetime.now(timezone.utc).date().isoformat()
    limit = int(os.getenv('BAQBAQ_DAILY_API_REQUESTS', '200'))
    with closing(sqlite3.connect(path, timeout=15)) as connection, connection:
        connection.execute('CREATE TABLE IF NOT EXISTS api_budget(day TEXT PRIMARY KEY, requests INTEGER NOT NULL)')
        connection.execute('BEGIN IMMEDIATE')
        row = connection.execute('SELECT requests FROM api_budget WHERE day=?', (day,)).fetchone()
        if (row[0] if row else 0) >= limit:
            raise RuntimeError('Дневной лимит Live-запросов исчерпан. Используйте Offline или обратитесь к владельцу стенда.')
        connection.execute('INSERT INTO api_budget VALUES(?,1) ON CONFLICT(day) DO UPDATE SET requests=requests+1', (day,))
