"""本地元数据仓储：数据源、schema 缓存、指标口径、查询日志、反馈。"""
import json
import sqlite3
import threading
import uuid
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS connections(
  id TEXT PRIMARY KEY, name TEXT, url TEXT, dialect TEXT, note TEXT,
  created_at TEXT, last_used TEXT
);
CREATE TABLE IF NOT EXISTS catalog(
  conn_id TEXT PRIMARY KEY, payload TEXT, refreshed_at TEXT
);
CREATE TABLE IF NOT EXISTS metrics(
  id TEXT PRIMARY KEY, conn_id TEXT, name TEXT, aliases TEXT, formula TEXT,
  table_name TEXT, note TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS query_log(
  id TEXT PRIMARY KEY, conn_id TEXT, question TEXT, sql TEXT, status TEXT,
  row_count INTEGER, latency_ms INTEGER, error TEXT, trace TEXT, created_at TEXT,
  limit_applied INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS feedback(
  log_id TEXT PRIMARY KEY, rating TEXT, comment TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS conversations(
  id TEXT PRIMARY KEY, conn_id TEXT, title TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS conversation_turns(
  id TEXT PRIMARY KEY, conv_id TEXT, conn_id TEXT, seq INTEGER, question TEXT,
  sql TEXT, summary TEXT, tables TEXT, row_count INTEGER, latency_ms INTEGER,
  error TEXT, created_at TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS conversation_memory(
  conv_id TEXT PRIMARY KEY, conn_id TEXT, payload TEXT, updated_seq INTEGER, updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_turns_conv ON conversation_turns(conv_id, seq);
CREATE INDEX IF NOT EXISTS idx_conv_conn ON conversations(conn_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_memory_conn ON conversation_memory(conn_id);
"""


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class MetadataStore:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self._migrate()
            self.conn.commit()

    def _migrate(self):
        """老库平滑升级：缺列就补，不重建表。"""
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(query_log)").fetchall()}
        if "limit_applied" not in cols:
            self.conn.execute("ALTER TABLE query_log ADD COLUMN limit_applied INTEGER DEFAULT 0")
        turn_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(conversation_turns)").fetchall()}
        if "payload" not in turn_cols:
            self.conn.execute("ALTER TABLE conversation_turns ADD COLUMN payload TEXT")

    # ---------- 数据源 ----------
    def add_connection(self, name, url, dialect, note=""):
        cid = uuid.uuid4().hex[:10]
        with self.lock:
            self.conn.execute(
                "INSERT INTO connections(id,name,url,dialect,note,created_at,last_used) VALUES(?,?,?,?,?,?,?)",
                (cid, name, url, dialect, note, _now(), _now()),
            )
            self.conn.commit()
        return cid

    def list_connections(self):
        with self.lock:
            rows = self.conn.execute("SELECT * FROM connections ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def get_connection(self, cid):
        with self.lock:
            row = self.conn.execute("SELECT * FROM connections WHERE id=?", (cid,)).fetchone()
        return dict(row) if row else None

    def touch_connection(self, cid):
        with self.lock:
            self.conn.execute("UPDATE connections SET last_used=? WHERE id=?", (_now(), cid))
            self.conn.commit()

    def delete_connection(self, cid):
        with self.lock:
            self.conn.execute("DELETE FROM connections WHERE id=?", (cid,))
            self.conn.execute("DELETE FROM catalog WHERE conn_id=?", (cid,))
            self.conn.execute("DELETE FROM metrics WHERE conn_id=?", (cid,))
            self.conn.execute("DELETE FROM conversation_memory WHERE conn_id=?", (cid,))
            self.conn.execute("DELETE FROM conversation_turns WHERE conn_id=?", (cid,))
            self.conn.execute("DELETE FROM conversations WHERE conn_id=?", (cid,))
            self.conn.commit()

    # ---------- schema 缓存 ----------
    def save_catalog(self, cid, catalog):
        with self.lock:
            self.conn.execute(
                "INSERT INTO catalog(conn_id,payload,refreshed_at) VALUES(?,?,?) "
                "ON CONFLICT(conn_id) DO UPDATE SET payload=excluded.payload, refreshed_at=excluded.refreshed_at",
                (cid, json.dumps(catalog, ensure_ascii=False), _now()),
            )
            self.conn.commit()

    def get_catalog(self, cid):
        with self.lock:
            row = self.conn.execute("SELECT payload,refreshed_at FROM catalog WHERE conn_id=?", (cid,)).fetchone()
        if not row:
            return None, None
        return json.loads(row["payload"]), row["refreshed_at"]

    # ---------- 指标口径 ----------
    def add_metric(self, cid, name, aliases, formula, table_name, note=""):
        mid = uuid.uuid4().hex[:10]
        with self.lock:
            self.conn.execute(
                "INSERT INTO metrics(id,conn_id,name,aliases,formula,table_name,note,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (mid, cid, name, aliases, formula, table_name, note, _now()),
            )
            self.conn.commit()
        return mid

    def list_metrics(self, cid):
        with self.lock:
            rows = self.conn.execute("SELECT * FROM metrics WHERE conn_id=? ORDER BY created_at", (cid,)).fetchall()
        return [dict(r) for r in rows]

    def delete_metric(self, mid):
        with self.lock:
            self.conn.execute("DELETE FROM metrics WHERE id=?", (mid,))
            self.conn.commit()

    # ---------- 日志与反馈 ----------
    def log_query(self, cid, question, sql, status, row_count, latency_ms, error, trace, limit_applied=False):
        lid = uuid.uuid4().hex[:10]
        with self.lock:
            self.conn.execute(
                "INSERT INTO query_log(id,conn_id,question,sql,status,row_count,latency_ms,error,trace,created_at,limit_applied) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (lid, cid, question, sql, status, row_count, latency_ms, error,
                 json.dumps(trace, ensure_ascii=False), _now(), 1 if limit_applied else 0),
            )
            self.conn.commit()
        return lid

    def add_feedback(self, log_id, rating, comment=""):
        with self.lock:
            self.conn.execute(
                "INSERT INTO feedback(log_id,rating,comment,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(log_id) DO UPDATE SET rating=excluded.rating, comment=excluded.comment",
                (log_id, rating, comment, _now()),
            )
            self.conn.commit()

    def get_log(self, log_id):
        with self.lock:
            row = self.conn.execute("SELECT * FROM query_log WHERE id=?", (log_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["trace"] = json.loads(d.get("trace") or "[]")
        except Exception:
            d["trace"] = []
        return d

    def list_logs(self, cid=None, limit=50):
        with self.lock:
            if cid:
                rows = self.conn.execute(
                    "SELECT * FROM query_log WHERE conn_id=? ORDER BY created_at DESC LIMIT ?", (cid, limit)
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT * FROM query_log ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["trace"] = json.loads(d.get("trace") or "[]")
            out.append(d)
        return out

    # ---------- 会话记忆（完整落库，不截断轮数） ----------
    def create_conversation(self, cid, title=""):
        conv_id = uuid.uuid4().hex[:12]
        with self.lock:
            self.conn.execute(
                "INSERT INTO conversations(id,conn_id,title,created_at,updated_at) VALUES(?,?,?,?,?)",
                (conv_id, cid, title or "", _now(), _now()),
            )
            self.conn.commit()
        return conv_id

    def get_conversation(self, conv_id):
        with self.lock:
            row = self.conn.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
        return dict(row) if row else None

    def list_conversations(self, cid, limit=50):
        with self.lock:
            rows = self.conn.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM conversation_turns t WHERE t.conv_id=c.id) AS turn_count "
                "FROM conversations c WHERE c.conn_id=? ORDER BY c.updated_at DESC LIMIT ?",
                (cid, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_conversation(self, conv_id):
        with self.lock:
            self.conn.execute("DELETE FROM conversation_memory WHERE conv_id=?", (conv_id,))
            self.conn.execute("DELETE FROM conversation_turns WHERE conv_id=?", (conv_id,))
            self.conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
            self.conn.commit()

    def next_turn_seq(self, conv_id):
        with self.lock:
            row = self.conn.execute(
                "SELECT COALESCE(MAX(seq),0) FROM conversation_turns WHERE conv_id=?", (conv_id,)
            ).fetchone()
        return int(row[0]) + 1

    def add_turn(self, conv_id, cid, question, sql, summary, tables, row_count, latency_ms,
                 error="", payload=None):
        turn_id = uuid.uuid4().hex[:10]
        seq = self.next_turn_seq(conv_id)
        payload_text = None
        if payload is not None:
            try:
                payload_text = json.dumps(payload, ensure_ascii=False, default=str)
            except Exception:
                payload_text = None
        with self.lock:
            self.conn.execute(
                "INSERT INTO conversation_turns(id,conv_id,conn_id,seq,question,sql,summary,tables,"
                "row_count,latency_ms,error,created_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (turn_id, conv_id, cid, seq, question, sql, summary,
                 json.dumps(tables or [], ensure_ascii=False), row_count, latency_ms, error, _now(),
                 payload_text),
            )
            self.conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (_now(), conv_id))
            if seq == 1:
                self.conn.execute(
                    "UPDATE conversations SET title=? WHERE id=? AND (title='' OR title IS NULL)",
                    ((question or "")[:40], conv_id),
                )
            self.conn.commit()
        return {"id": turn_id, "seq": seq}

    def list_turns(self, conv_id, limit=None, offset=0):
        sql = ("SELECT * FROM conversation_turns WHERE conv_id=? ORDER BY seq ASC")
        params = [conv_id]
        if limit:
            sql += " LIMIT ? OFFSET ?"
            params += [int(limit), int(offset)]
        with self.lock:
            rows = self.conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["tables"] = json.loads(d.get("tables") or "[]")
            except Exception:
                d["tables"] = []
            try:
                d["payload"] = json.loads(d.get("payload") or "null")
            except Exception:
                d["payload"] = None
            out.append(d)
        return out

    def count_turns(self, conv_id):
        with self.lock:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM conversation_turns WHERE conv_id=?", (conv_id,)
            ).fetchone()
        return int(row[0])

    # ---------- 结构化会话记忆（与完整轮次分开存，接口列表不会被大字段拖慢） ----------
    def get_memory(self, conv_id):
        with self.lock:
            row = self.conn.execute(
                "SELECT payload FROM conversation_memory WHERE conv_id=?", (conv_id,)
            ).fetchone()
        if not row or not row["payload"]:
            return None
        try:
            return json.loads(row["payload"])
        except Exception:
            return None

    def save_memory(self, conv_id, cid, state):
        payload = json.dumps(state or {}, ensure_ascii=False, default=str)
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO conversation_memory(conv_id,conn_id,payload,updated_seq,updated_at) "
                "VALUES(?,?,?,?,?)",
                (conv_id, cid, payload, int((state or {}).get("updated_seq") or 0), _now()),
            )
            self.conn.commit()
        return True
