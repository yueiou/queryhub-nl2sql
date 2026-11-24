"""只读执行器：统一列名、JSON 化结果、限制返回行数。"""
import time
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import text


def _jsonable(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (datetime, date)):
        return v.isoformat(sep=" ") if isinstance(v, datetime) else v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return "<binary>"
    return str(v)


def iter_rows(engine, sql, batch=1000):
    """流式执行：先产出列名，再逐批产出数据行，用于导出全量结果（不一次性进内存）。"""
    with engine.connect() as conn:
        try:
            conn = conn.execution_options(stream_results=True)
        except Exception:
            pass
        result = conn.execute(text(sql))
        yield [str(k) for k in result.keys()]
        while True:
            chunk = result.fetchmany(batch)
            if not chunk:
                break
            for row in chunk:
                yield [_jsonable(v) for v in row]


def execute_sql(engine, sql, max_rows=200):
    """执行只读查询：限制返回行数，返回列名与数据。"""
    t0 = time.perf_counter()
    with engine.connect() as conn:
        result = conn.execute(text(sql))
        columns = [str(k) for k in result.keys()]
        rows = []
        truncated = False
        for row in result.fetchmany(max_rows + 1):
            if len(rows) >= max_rows:
                truncated = True
                break
            rows.append([_jsonable(v) for v in row])
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
    }
