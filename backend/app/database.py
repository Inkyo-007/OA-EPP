import os
import pymysql
from contextlib import contextmanager
from urllib.parse import urlparse, unquote

# ──────────────── 数据库配置 ────────────────
_DB_URL = os.environ.get("DATABASE_URL", "")
if _DB_URL:
    _parsed = urlparse(_DB_URL)
    DB_CONFIG = {
        "host": _parsed.hostname or "127.0.0.1",
        "port": _parsed.port or 3306,
        "user": _parsed.username or "root",
        "password": unquote(_parsed.password) if _parsed.password else "",
        "database": _parsed.path.lstrip("/") or "oaepp_dev",
        "charset": "utf8mb4",
    }
else:
    DB_CONFIG = {
        "host": os.environ.get("DB_HOST", "156.239.252.40"),
        "port": int(os.environ.get("DB_PORT", "13306")),
        "user": os.environ.get("DB_USER", "student_dev"),
        "password": os.environ.get("DB_PASSWORD", "OaEpp@Dev2026"),
        "database": os.environ.get("DB_NAME", "oaepp_dev"),
        "charset": "utf8mb4",
    }


def get_connection():
    """创建 MySQL 连接"""
    conn = pymysql.connect(**DB_CONFIG)
    conn.cursorclass = pymysql.cursors.DictCursor
    return conn


@contextmanager
def db():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """远程数据库表结构已由管理员预先创建，此处仅验证连接"""
    with db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1")
    print("[init_db] 数据库连接验证通过")
