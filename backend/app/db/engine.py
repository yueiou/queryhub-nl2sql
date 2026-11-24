"""数据源引擎：URL 归一化、方言映射、连接创建与缓存。"""
import os
import re
import threading

from sqlalchemy import create_engine, text

from ..core import config

# SQLAlchemy dialect 名称 -> sqlglot 方言
DIALECT_MAP = {
    "sqlite": "sqlite",
    "mysql": "mysql",
    "mariadb": "mysql",
    "tidb": "mysql",
    "postgresql": "postgres",
    "postgres": "postgres",
    "redshift": "redshift",
    "mssql": "tsql",
    "oracle": "oracle",
    "dm": "oracle",
    "dameng": "oracle",
    "duckdb": "duckdb",
    "clickhouse": "clickhouse",
    "trino": "trino",
    "presto": "trino",
    "hive": "hive",
    "spark": "spark",
    "bigquery": "bigquery",
    "snowflake": "snowflake",
    "db2": "db2",
}


def resolve_url(raw, base_dir):
    """把用户填的地址统一成 SQLAlchemy URL；裸路径按 SQLite 处理。"""
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("数据源地址不能为空")
    if "://" not in raw:
        path = raw if os.path.isabs(raw) else os.path.join(base_dir, raw)
        raw = "sqlite:///" + os.path.abspath(path).replace("\\", "/")
    if raw.startswith("sqlite:///"):
        path = raw[len("sqlite:///"):]
        if path and path != ":memory:" and not os.path.isabs(path):
            path = os.path.abspath(os.path.join(base_dir, path))
        if path and path != ":memory:":
            raw = "sqlite:///" + path.replace("\\", "/")
    return raw


def make_engine(url):
    kwargs = {"pool_pre_ping": True, "pool_recycle": 1800}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(url, **kwargs)


def dialect_of(engine):
    name = (engine.dialect.name or "").lower()
    return DIALECT_MAP.get(name, name or "sqlite"), name


def test_connection(engine):
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    sg, name = dialect_of(engine)
    try:
        version = str(engine.dialect.server_version_info)
    except Exception:
        version = ""
    return {"dialect": name, "sqlglot_dialect": sg, "version": version}


def quote_ident(name):
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name or ""):
        return name
    return '"' + (name or "").replace('"', '""') + '"'


class EngineRegistry:
    """按数据源 id 缓存 engine，避免每次提问都重新建连。"""

    def __init__(self):
        self._engines = {}
        self._lock = threading.Lock()

    def get(self, conn_id, url):
        with self._lock:
            engine = self._engines.get(conn_id)
            if engine is None:
                engine = make_engine(resolve_url(url, config.DATA_DIR))
                self._engines[conn_id] = engine
            return engine

    def put(self, conn_id, engine):
        with self._lock:
            self._engines[conn_id] = engine

    def drop(self, conn_id):
        with self._lock:
            engine = self._engines.pop(conn_id, None)
        if engine is not None:
            engine.dispose()

    def dispose_all(self):
        with self._lock:
            items = list(self._engines.items())
            self._engines.clear()
        for _, engine in items:
            engine.dispose()
