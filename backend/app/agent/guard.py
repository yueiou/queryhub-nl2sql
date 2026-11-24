"""SQL 只读护栏：单语句、只读、表白名单、系统库拦截、自动 LIMIT。"""
import re

import sqlglot
from sqlglot import exp

FORBIDDEN_NODES = {
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "Merge", "TruncateTable",
    "Grant", "Revoke", "Copy", "Attach", "Detach", "Export", "Load", "Set", "Use",
    "Transaction", "Commit", "Rollback", "Pragma", "Vacuum", "Analyze", "Call",
    "Command", "Comment",
}
SYSTEM_SCHEMAS = {
    "information_schema", "pg_catalog", "performance_schema", "mysql", "sys",
    "master", "tempdb", "msdb", "dba", "oracle",
}


class GuardError(Exception):
    pass


def strip_sql(text):
    if not text:
        return ""
    text = text.strip()
    m = re.search(r"```(?:sql)?\s*([\s\S]*?)```", text, re.I)
    if m:
        text = m.group(1).strip()
    text = re.sub(r"^\s*(SQL|sql)\s*[:：]\s*", "", text)
    return text.strip().rstrip(";").strip()


def prepare(sql, dialect, allowed_tables, max_rows=200, enforce_limit=True):
    """校验并规范化 SQL，返回 (safe_sql, tables_used, limit_applied)。

    enforce_limit=False 时不补 LIMIT（用户明确要求去掉时使用），
    展示层的 max_rows 截断仍由 executor 负责。
    """
    sql = strip_sql(sql)
    if not sql:
        raise GuardError("模型没有返回可执行的 SQL")
    if len(sql) > 8000:
        raise GuardError("SQL 过长，已拒绝执行")

    try:
        exprs = sqlglot.parse(sql, read=dialect or None)
    except Exception as e:
        raise GuardError(f"SQL 语法解析失败：{e}")
    exprs = [e for e in exprs if e is not None]
    if len(exprs) != 1:
        raise GuardError("只允许执行单条 SQL 语句")
    ast = exprs[0]

    for node in ast.walk():
        if type(node).__name__ in FORBIDDEN_NODES:
            raise GuardError(f"检测到非只读操作：{type(node).__name__}，平台仅允许 SELECT 查询")

    if not isinstance(ast, (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)):
        raise GuardError(f"不支持的语句类型：{type(ast).__name__}")

    cte_names = {cte.alias_or_name.lower() for cte in ast.find_all(exp.CTE) if cte.alias_or_name}
    allowed = {t.lower() for t in allowed_tables}
    used = []
    for tbl in ast.find_all(exp.Table):
        name = (tbl.name or "").lower()
        db = (tbl.db or "").lower()
        if db in SYSTEM_SCHEMAS:
            raise GuardError(f"禁止访问系统库：{db}")
        if not name or name in cte_names:
            continue
        if allowed and name not in allowed:
            raise GuardError(f"表 {tbl.name} 不在当前数据源的白名单内")
        if name not in used:
            used.append(name)

    limit_applied = False
    if enforce_limit and not ast.args.get("limit") and not ast.args.get("top"):
        try:
            ast = ast.limit(max_rows)
            limit_applied = True
        except Exception:
            raise GuardError("无法为查询追加行数限制，已拒绝执行")

    try:
        safe = ast.sql(dialect=dialect or None)
    except Exception as e:
        raise GuardError(f"SQL 规范化失败：{e}")
    return safe, used, limit_applied
