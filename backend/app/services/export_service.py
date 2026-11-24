"""导出服务：按查询日志重放 SQL，流式导出完整结果（CSV）。"""
import csv
import io

from ..agent import context
from ..agent.guard import prepare
from ..core import config, deps
from ..core.errors import AppError, NotFoundError
from ..db.executor import iter_rows
from ..schemas.catalog import table_names
from . import connection_service


def build(conn_id, log_id):
    """校验日志归属，去掉平台自动补的 LIMIT，返回可直接流式执行的 SQL。"""
    log = deps.store.get_log(log_id)
    if not log:
        raise NotFoundError("查询记录不存在")
    if log["conn_id"] != conn_id:
        raise AppError("该查询记录不属于当前数据源", 400)
    sql = (log.get("sql") or "").strip()
    if not sql:
        raise AppError("该记录没有可导出的 SQL", 400)

    engine, _ = connection_service.get_engine(conn_id)
    catalog, _ = deps.store.get_catalog(conn_id)
    if not catalog:
        raise NotFoundError("尚未读取该数据源结构")
    dialect = catalog.get("sqlglot_dialect") or "sqlite"

    # 只有平台自动追加的行数限制才去掉；用户自己写的 LIMIT 5 必须保留
    if log.get("limit_applied"):
        sql = context.drop_limit(sql, dialect)
    safe, _, _ = prepare(sql, dialect, table_names(catalog),
                         config.EXPORT_MAX_ROWS, enforce_limit=False)
    return engine, safe, log


def csv_stream(engine, sql, max_rows=None):
    it = iter_rows(engine, sql)
    columns = next(it)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    yield "\ufeff" + buf.getvalue()   # BOM，Excel 直接双击不乱码
    buf.seek(0)
    buf.truncate(0)

    n = 0
    for row in it:
        writer.writerow(row)
        n += 1
        if n % 500 == 0:
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)
        if max_rows and n >= max_rows:
            break
    rest = buf.getvalue()
    if rest:
        yield rest
