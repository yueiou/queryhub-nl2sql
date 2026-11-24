"""规则兜底引擎：没有模型时，用关键词 + 指标 + 维度匹配生成单表聚合 SQL。

它保证平台链路可演示，但泛化能力远不如 LLM：不处理多表 JOIN、嵌套条件和口径推理。
"""
import re

from ..db.engine import quote_ident
from .retrieve import matched_metrics, tokens

NUMERIC_HINTS = ("INT", "DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "REAL", "NUMBER", "MONEY")
AMOUNT_WORDS = ("金额", "销售额", "成交额", "gmv", "营收", "收入", "总额", "客单价")
COUNT_WORDS = ("数量", "多少", "单量", "订单数", "几笔", "条数", "笔数", "几单", "个数")
AVG_WORDS = ("平均", "均值", "客单价")
GROUP_CUES = ("各", "每", "按", "分别", "哪个", "哪些", "排名", "最高", "最多", "最好",
              "畅销", "热销", "占比", "分布")
RANK_WORDS = ("排名", "最多", "最高", "最好", "畅销", "热销", "前", "top")
TREND_WORDS = ("趋势", "走势", "每月", "按月", "各月", "每天", "按天", "每日")


def _is_numeric(ctype):
    up = (ctype or "").upper()
    return any(h in up for h in NUMERIC_HINTS)


def _is_date(ctype, name, comment):
    up = (ctype or "").upper()
    txt = f"{name} {comment}"
    return any(h in up for h in ("DATE", "TIME")) or any(k in txt for k in ("日期", "时间", "date", "time"))


def _date_column(cols):
    for c in cols:
        if _is_date(c.get("type"), c["name"], c.get("comment") or ""):
            return c["name"]
    return None


def _month_expr(dialect, col):
    q = quote_ident(col)
    if dialect == "sqlite":
        return f"strftime('%Y-%m', {q})"
    if dialect == "mysql":
        return f"DATE_FORMAT({q}, '%Y-%m')"
    if dialect == "postgres":
        return f"TO_CHAR({q}, 'YYYY-MM')"
    if dialect == "tsql":
        return f"FORMAT({q}, 'yyyy-MM')"
    if dialect == "oracle":
        return f"TO_CHAR({q}, 'YYYY-MM')"
    return q


def _time_filter(dialect, date_col, q):
    if not date_col:
        return ""
    n = None
    m = re.search(r"(?:近|最近|过去)\s*(\d+)\s*(?:天|日)", q)
    if m:
        n = int(m.group(1))
    elif re.search(r"(?:近|最近)\s*(?:一周|7天)", q):
        n = 7
    elif re.search(r"(?:近|最近)\s*(?:一个月|30天)", q):
        n = 30

    col = quote_ident(date_col)
    if n:
        if dialect == "sqlite":
            return f"{col} >= date('now', '-{n} day')"
        if dialect == "mysql":
            return f"{col} >= DATE_SUB(CURDATE(), INTERVAL {n} DAY)"
        if dialect == "postgres":
            return f"{col} >= CURRENT_DATE - INTERVAL '{n} day'"
        if dialect == "tsql":
            return f"{col} >= DATEADD(day, -{n}, CAST(GETDATE() AS DATE))"
        if dialect == "oracle":
            return f"{col} >= TRUNC(SYSDATE) - {n}"
        return f"{col} >= CURRENT_DATE"

    if "本月" in q or "这个月" in q:
        if dialect == "sqlite":
            return f"{col} >= date('now', 'start of month')"
        if dialect == "mysql":
            return f"{col} >= DATE_FORMAT(CURDATE(), '%Y-%m-01')"
        if dialect == "postgres":
            return f"{col} >= DATE_TRUNC('month', CURRENT_DATE)"
        if dialect == "tsql":
            return f"{col} >= DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1)"
        return f"{col} >= TRUNC(SYSDATE, 'MM')"

    if "今年" in q:
        if dialect == "sqlite":
            return f"{col} >= date('now', 'start of year')"
        if dialect == "mysql":
            return f"{col} >= DATE_FORMAT(CURDATE(), '%Y-01-01')"
        if dialect == "postgres":
            return f"{col} >= DATE_TRUNC('year', CURRENT_DATE)"
        if dialect == "tsql":
            return f"{col} >= DATEFROMPARTS(YEAR(GETDATE()), 1, 1)"
        return f"{col} >= TRUNC(SYSDATE, 'YYYY')"

    return ""


def _choose_primary(question, tables, metrics, hits):
    """全量注入时表变多，tables[0] 可能只是维表；按“指标主表 / 事实表特征 / 关键词”挑主表。"""
    if hits:
        for m in hits:
            name = (m.get("table_name") or "").lower()
            for t in tables:
                if t["name"].lower() == name:
                    return t
    qt = tokens(question)
    q = question.lower()
    best, best_score = tables[0], -1
    for t in tables:
        score = 0
        name_tokens = tokens(t.get("name", "") + " " + (t.get("comment") or ""))
        score += sum(3 for tok in qt if tok in name_tokens)
        numeric = sum(1 for c in t["columns"] if _is_numeric(c.get("type")))
        if numeric >= 2:
            score += 2
        if _date_column(t["columns"]):
            score += 1
        if any(k in (t["name"] or "").lower() for k in ("fact", "f_", "dwd", "dws", "order", "trade")):
            score += 2
        if any(w in q for w in AMOUNT_WORDS) and _pick_column(t["columns"], qt, AMOUNT_WORDS):
            score += 2
        if score > best_score:
            best, best_score = t, score
    return best


def heuristic_sql(question, tables, metrics, dialect="sqlite"):
    if not tables:
        return "SELECT 1 AS note"
    q = question.lower()
    qt = tokens(question)
    hits = matched_metrics(question, metrics or [])
    primary = _choose_primary(question, tables, metrics or [], hits)
    cols = primary["columns"]

    agg, alias, agg_col = None, None, None
    ranking = any(w in q for w in RANK_WORDS)

    if hits:
        agg = hits[0]["formula"]
        alias = hits[0]["name"]
        agg_col = _guess_agg_col(agg, cols)
    else:
        amount_col = _pick_column(cols, qt, AMOUNT_WORDS)
        if any(w in q for w in AVG_WORDS) and amount_col:
            agg, alias, agg_col = f"AVG({quote_ident(amount_col)})", "平均值", amount_col
        elif any(w in q for w in AMOUNT_WORDS) and amount_col:
            agg, alias, agg_col = f"SUM({quote_ident(amount_col)})", "销售额", amount_col
        elif ranking and amount_col:
            agg, alias, agg_col = f"SUM({quote_ident(amount_col)})", "销售额", amount_col
        elif any(w in q for w in COUNT_WORDS):
            agg, alias = "COUNT(*)", "数量"
        elif not any(w in q for w in ("明细", "列出", "哪些", "趋势", "走势", "对比", "分布")):
            agg, alias = "COUNT(*)", "数量"

    if agg is None:
        return _fallback(primary)

    date_col = _date_column(cols)
    group_expr, group_alias = None, None
    if date_col and any(w in q for w in TREND_WORDS):
        if any(w in q for w in ("每月", "按月", "各月", "趋势", "走势")):
            group_expr, group_alias = _month_expr(dialect, date_col), "月份"
        else:
            group_expr, group_alias = quote_ident(date_col), date_col
    elif any(w in q for w in GROUP_CUES):
        col = _pick_group_col(cols, qt, exclude=agg_col)
        if col:
            group_expr, group_alias = quote_ident(col), col

    sel = []
    if group_expr:
        sel.append(f"{group_expr} AS \"{group_alias}\"")
    sel.append(f"{agg} AS \"{alias}\"")
    sql = f"SELECT {', '.join(sel)} FROM {quote_ident(primary['name'])}"

    where = _time_filter(dialect, date_col, question)
    if where:
        sql += f" WHERE {where}"
    if group_expr:
        sql += f" GROUP BY {group_expr}"
        sql += " ORDER BY 1" if group_alias in ("月份", date_col) else " ORDER BY 2 DESC"

    n = 10
    singular = bool(
        re.search(r"(哪个|哪一个|哪家|哪种|什么).{0,6}最", question)
        or re.search(r"(最高|最低|最多|最少)的(?![0-9])", question)
    )
    m = re.search(r"(?:前|top)\s*(\d+)", question, re.I) or re.search(r"(\d+)\s*(?:个|名|条|家|款)", question)
    if m:
        n = max(1, min(int(m.group(1)), 100))
    elif singular:
        n = 1
    elif group_alias == "月份":
        n = 24

    if ranking and group_expr:
        sql += f" LIMIT {n}"
    elif group_alias == "月份":
        sql += " LIMIT 24"

    return sql


def _guess_agg_col(formula, cols):
    m = re.search(r"(?:SUM|AVG|MAX|MIN)\(\s*(?:CASE[\s\S]*?THEN\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\)", formula or "", re.I)
    if not m:
        m = re.search(r"(?:SUM|AVG|MAX|MIN)\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)", formula or "", re.I)
    if m:
        name = m.group(1).lower()
        if any(c["name"].lower() == name for c in cols):
            return next(c["name"] for c in cols if c["name"].lower() == name)
    return None


def _pick_column(cols, qt, words):
    for c in cols:
        label = (c["name"] + " " + (c.get("comment") or "")).lower()
        if any(w in label for w in words) and _is_numeric(c.get("type")):
            return c["name"]
    for c in cols:
        if _is_numeric(c.get("type")) and re.search(r"(amount|price|total|qty|num|count)", c["name"], re.I):
            return c["name"]
    return None


def _pick_group_col(cols, qt, exclude=None):
    best, best_score = None, 0
    for c in cols:
        if c.get("pk") or (exclude and c["name"] == exclude):
            continue
        if _is_numeric(c.get("type")) or _is_date(c.get("type"), c["name"], c.get("comment") or ""):
            continue
        label_tokens = tokens(c["name"] + " " + (c.get("comment") or ""))
        score = len(label_tokens & qt)
        if score > best_score:
            best, best_score = c["name"], score
    return best if best_score >= 1 else None


def _fallback(primary):
    return f"SELECT * FROM {quote_ident(primary['name'])}"
