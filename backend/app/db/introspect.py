"""Schema 自省：把任意数据库读成统一的 catalog（表/列/类型/注释/主外键/枚举取值）。"""
from sqlalchemy import inspect, text

from .engine import dialect_of, quote_ident

SYSTEM_TABLES = {"_schema_notes", "schema_notes", "sqlite_sequence"}

# 这些列名/注释通常承载枚举值，采样出来给模型看，能显著减少“把 on_sale 猜成在售”
VALUE_HINTS = (
    "status", "state", "type", "kind", "level", "channel", "category", "gender",
    "method", "source", "flag", "grade", "stage", "role", "tag", "状态", "类型",
    "渠道", "等级", "性别", "方式", "来源", "类别",
)
TEXT_TYPE_HINTS = ("CHAR", "TEXT", "STRING", "VARCHAR", "NVARCHAR", "ENUM")


def introspect(engine, max_tables=300):
    insp = inspect(engine)
    sg, raw_dialect = dialect_of(engine)
    tables = []
    for tname in insp.get_table_names()[:max_tables]:
        if tname in SYSTEM_TABLES:
            continue
        cols = []
        for c in insp.get_columns(tname):
            try:
                ctype = str(c.get("type") or "")
            except Exception:
                ctype = ""
            cols.append({
                "name": c["name"],
                "type": ctype,
                "nullable": bool(c.get("nullable", True)),
                "comment": (c.get("comment") or ""),
                "pk": False,
            })
        try:
            pk = insp.get_pk_constraint(tname).get("constrained_columns") or []
        except Exception:
            pk = []
        for c in cols:
            c["pk"] = c["name"] in pk
        try:
            tcomment = (insp.get_table_comment(tname) or {}).get("text") or ""
        except Exception:
            tcomment = ""
        fks = []
        try:
            for f in insp.get_foreign_keys(tname):
                fks.append({
                    "columns": f.get("constrained_columns") or [],
                    "ref_table": f.get("referred_table") or "",
                    "ref_columns": f.get("referred_columns") or [],
                })
        except Exception:
            pass
        tables.append({"name": tname, "comment": tcomment, "columns": cols, "fks": fks})

    notes = _read_schema_notes(engine, insp)
    if notes:
        by_name = {t["name"]: t for t in tables}
        for key, note in notes.items():
            tname, _, cname = key.partition(".")
            t = by_name.get(tname)
            if not t:
                continue
            if cname:
                for c in t["columns"]:
                    if c["name"] == cname and not c["comment"]:
                        c["comment"] = note
            elif not t["comment"]:
                t["comment"] = note

    _sample_values(engine, tables, sg)
    return {"dialect": raw_dialect, "sqlglot_dialect": sg, "tables": tables}


def _is_enum_like(col):
    name = (col.get("name") or "").lower()
    comment = col.get("comment") or ""
    ctype = (col.get("type") or "").upper()
    if not any(h in (name + " " + comment).lower() for h in VALUE_HINTS):
        return False
    return any(h in ctype for h in TEXT_TYPE_HINTS) or ctype == ""


def _sample_sql(tname, col, sg, rows):
    t = quote_ident(tname)
    c = quote_ident(col)
    if sg == "tsql":
        return f"SELECT TOP {rows} {c} FROM {t} WHERE {c} IS NOT NULL"
    if sg == "oracle":
        return f"SELECT {c} FROM {t} WHERE {c} IS NOT NULL AND ROWNUM <= {rows}"
    return f"SELECT {c} FROM {t} WHERE {c} IS NOT NULL LIMIT {rows}"


def _sample_values(engine, tables, sg, max_columns=60, rows=200, max_values=10):
    """对枚举类文本列取前若干行做取值采样；失败静默跳过，不影响自省。"""
    budget = max_columns
    for t in tables:
        for c in t["columns"]:
            if budget <= 0:
                return
            if not _is_enum_like(c):
                continue
            budget -= 1
            try:
                with engine.connect() as conn:
                    raw = [str(r[0]) for r in conn.execute(text(_sample_sql(t["name"], c["name"], sg, rows))) if r[0] is not None]
            except Exception:
                continue
            uniq = []
            seen = set()
            for v in raw:
                v = v.strip()
                if not v or v in seen:
                    continue
                seen.add(v)
                uniq.append(v)
            # 取值太少（全空/全同）或太多（像自由文本）都不值得塞进提示词
            if 2 <= len(uniq) <= max_values:
                c["values"] = uniq[:max_values]


def _read_schema_notes(engine, insp):
    """可选语义注记表：_schema_notes(table_name, column_name, note)，用于 SQLite 等无注释能力的库。"""
    names = set(insp.get_table_names())
    if "_schema_notes" not in names:
        return {}
    out = {}
    try:
        with engine.connect() as conn:
            for row in conn.execute(text("SELECT table_name, column_name, note FROM _schema_notes")):
                t, c, n = row[0], row[1], row[2]
                if not n:
                    continue
                out[f"{t}.{c}" if c else t] = str(n)
    except Exception:
        return {}
    return out
