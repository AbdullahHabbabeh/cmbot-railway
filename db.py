import os
import psycopg2
from psycopg2 import pool
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL")        # provided by Railway
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable not set.")

# ------------------------------------------------------------------
# Connection pool – one per worker (Railway uses gunicorn with 1 worker)
# ------------------------------------------------------------------
pg_pool = psycopg2.pool.SimpleConnectionPool(1, 10, DATABASE_URL)

@contextmanager
def get_cursor():
    conn = pg_pool.getconn()
    try:
        with conn:
            with conn.cursor() as cur:
                yield cur
    finally:
        pg_pool.putconn(conn)

# ------------------------------------------------------------------
# One-time schema bootstrap
# ------------------------------------------------------------------
def init_db():
    with get_cursor() as cur:
        # clients table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                user_id     BIGINT PRIMARY KEY,
                user_name   TEXT NOT NULL,
                last_order  TIMESTAMPTZ
            );
        """)
        # orders table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT REFERENCES clients(user_id) ON DELETE CASCADE,
                item_code   TEXT NOT NULL,
                item_name   TEXT NOT NULL,
                quantity    INTEGER NOT NULL,
                price_per_item NUMERIC(10,2) NOT NULL,
                total_price NUMERIC(10,2) NOT NULL,
                status      TEXT DEFAULT 'pending',
                created_at  TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        # payments table
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT REFERENCES clients(user_id) ON DELETE CASCADE,
                amount      NUMERIC(10,2) NOT NULL,
                confirmed_by_cm BOOLEAN DEFAULT FALSE,
                created_at  TIMESTAMPTZ DEFAULT NOW(),
                original_ts TIMESTAMPTZ
            );
        """)
        # pending_payments table (acts as a queue)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pending_payments (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT REFERENCES clients(user_id) ON DELETE CASCADE,
                user_name   TEXT NOT NULL,
                amount      NUMERIC(10,2) NOT NULL,
                created_at  TIMESTAMPTZ DEFAULT NOW()
            );
        """)
    logger.info("PostgreSQL tables initialised.")