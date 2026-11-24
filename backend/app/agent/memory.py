"""会话结构化记忆：抽取 SQL 槽位、增量压缩、按相关度回捞早期轮次。

原则：
- 全量轮次仍然完整落库，这里只决定“哪些内容进提示词”；
- 摘要由已执行 SQL 的结构化信息生成，不调用模型转述，避免二次幻觉；
- 状态卡只记事实（表、指标、过滤、分组、排序、行数），不替用户做推断。
"""
import re

import sqlglot
from sqlglot import exp

DATE_COL_RE = re.compile(
    r"(date|time|day|month|year|日期|时间|年月|创建|注册|下单|支付|发生|统计日)",
    re.I,
)
DATE_VALUE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}|\d{4}/\d{2}/\d{2}|current_date|current_timestamp|"
    r"date\s*\(|datetime\s*\(|strftime\s*\(|now\s*\(|unix_timestamp|to_date)",
    re.I,
)
TURN_RE = re.compile(r"第\s*(\d+)\s*轮")
STOP_BIGRAMS = {
    "查询", "统计", "信息", "数据", "一下", "帮我", "多少", "哪些", "怎么",
    "这个", "那个", "现在", "今年", "去年", "本月", "上个", "还是", "可以",
}

MAX_FILTERS = 8
MAX_SQL_STORE = 800
MAX_SUMMARY = 180
OP_LABELS = {
    "EQ": "=", "NEQ": "!=", "GT": ">", "GTE": ">=", "LT": "<", "LTE": "<=",
    "LIKE": "LIKE", "IN": "IN", "BETWEEN": "BETWEEN", "IS": "IS",
}


def _field(turn, name, default=""):
    if isinstance(turn, dict):
        value = turn.get(name, default)
    return value if value is not None else default


def _txt(value, limit=0):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return (text[:limit] + "…") if limit and len(text) > limit else text


def _sql(node, dialect):
    try:
        return _txt(node.sql(dialect=dialect or None), 160)
    except Exception:
        return _txt(node, 160)


def _column_name(node):
    if isinstance(node, exp.Column):
        return _txt(node.name, 60)
    return _sql(node, None)


def _split_and(node):
    """把 WHERE 的 AND 链拆成单条件，OR 不拆（避免把分支误当成两个独立过滤）。"""
    if isinstance(node, exp.Paren):
        return _split_and(node.this)
    if isinstance(node, exp.And):
        return _split_and(node.left) + _split_and(node.right)
    return [node]


def _is_date_condition(column, value_text):
    return bool(DATE_COL_RE.search(column or "") or DATE_VALUE_RE.search(value_text or ""))


def _condition(node, dialect):
    """把一个比较节点渲染成 {column, op, value}；不认识的节点返回 None。"""
    if isinstance(node, exp.Between):
        col = _column_name(node.this)
        value = f"{_sql(node.args.get('low'), dialect)} AND {_sql(node.args.get('high'), dialect)}"
        return {"column": col, "op": "BETWEEN", "value": value}
    if isinstance(node, exp.In):
        col = _column_name(node.this)
        values = ", ".join(_sql(v, dialect) for v in node.expressions[:8])
        return {"column": col, "op": "IN", "value": values}
    if isinstance(node, (exp.Like, exp.ILike)):
        return {"column": _column_name(node.this), "op": "LIKE", "value": _sql(node.expression, dialect)}
    if isinstance(node, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
        left, right = node.left, node.right
        return {"column": _column_name(left), "op": node.key.upper(), "value": _sql(right, dialect)}
    if isinstance(node, exp.Is):
        return {"column": _column_name(node.this), "op": "IS", "value": _sql(node.expression, dialect)}
    return None


def _table_names(ast, dialect):
    names = []
    for table in ast.find_all(exp.Table):
        name = _txt(table.name, 60)
        if name and name not in names:
            names.append(name)
    return names


def _ordinal(node, select_list, dialect):
    """GROUP BY 1 / ORDER BY 2 这类序号，回到对应 SELECT 表达式，避免状态卡只写一个数字。"""
    suffix = ""
    inner = node
    if isinstance(node, exp.Ordered):
        inner = node.this
        suffix = " DESC" if node.args.get("desc") else " ASC"
    try:
        if isinstance(inner, exp.Literal) and inner.is_int:
            idx = int(inner.name) - 1
            if 0 <= idx < len(select_list):
                target = select_list[idx]
                if isinstance(target, exp.Alias):
                    target = target.this
                return _sql(target, dialect) + suffix
    except Exception:
        pass
    return _sql(node, dialect)


def build_facts(seq, question, sql, summary="", tables=None, metrics=None, dialect="sqlite"):
    """从一轮已执行 SQL 抽取结构化记忆；解析失败也保留问题/表/摘要，不阻断主流程。"""
    facts = {
        "seq": int(seq or 0),
        "question": _txt(question, 100),
        "sql": _txt(sql, MAX_SQL_STORE),
        "summary": _txt(summary, MAX_SUMMARY),
        "tables": list(tables or [])[:12],
        "metrics": list(metrics or [])[:8],
        "date_filters": [],
        "filters": [],
        "group_by": [],
        "order_by": [],
        "limit": None,
    }
    if not sql:
        return facts
    try:
        ast = sqlglot.parse_one(sql, read=dialect or None)
    except Exception:
        return facts

    if not facts["tables"]:
        facts["tables"] = _table_names(ast, dialect)

    where = ast.find(exp.Where)
    if where is not None:
        for cond in _split_and(where.this):
            item = _condition(cond, dialect)
            if not item or not item["column"]:
                continue
            if _is_date_condition(item["column"], item["value"]):
                bucket = "date_filters"
            else:
                bucket = "filters"
            if len(facts[bucket]) < MAX_FILTERS:
                facts[bucket].append(item)

    group = ast.find(exp.Group)
    if group is not None:
        facts["group_by"] = [_ordinal(e, ast.expressions, dialect) for e in group.expressions[:8]]

    order = ast.find(exp.Order)
    if order is not None:
        facts["order_by"] = [_ordinal(e, ast.expressions, dialect) for e in order.expressions[:5]]

    limit = ast.args.get("limit")
    if limit is not None:
        raw = limit.expression
        try:
            facts["limit"] = int(raw.name) if hasattr(raw, "name") else int(_sql(raw, dialect))
        except Exception:
            facts["limit"] = _sql(raw, dialect)
    return facts


def facts_from_turn(turn, dialect="sqlite"):
    payload = _field(turn, "payload", {}) or {}
    metrics = _field(turn, "metrics", []) or (payload.get("metrics_used") if isinstance(payload, dict) else [])
    return build_facts(
        _field(turn, "seq", 0),
        _field(turn, "question", ""),
        _field(turn, "sql", ""),
        _field(turn, "summary", ""),
        _field(turn, "tables", []) or [],
        metrics or [],
        dialect,
    )


def rebuild_state(history, conn_id="", dialect="sqlite", topics_limit=12):
    """旧会话没有状态卡时，从完整历史重建；只留最近 topics_limit 轮的结构化摘要。"""
    topics = [facts_from_turn(t, dialect) for t in (history or [])]
    if not topics:
        return None
    topics = topics[-topics_limit:]
    return {
        "version": 1,
        "conn_id": conn_id,
        "updated_seq": topics[-1]["seq"],
        "turn_count": len(history or []),
        "active": topics[-1],
        "topics": topics,
    }


def merge_state(previous, facts, conn_id="", topics_limit=12):
    """最新轮覆盖 active；近期主题保留有限条，更早的靠轮次检索，不在状态卡里堆积。"""
    state = previous if isinstance(previous, dict) and previous.get("version") == 1 else {}
    topics = [t for t in (state.get("topics") or []) if t.get("seq") != facts.get("seq")]
    topics = (topics + [facts])[-int(topics_limit):]
    return {
        "version": 1,
        "conn_id": conn_id or state.get("conn_id", ""),
        "updated_seq": facts.get("seq", state.get("updated_seq", 0)),
        "turn_count": max(int(state.get("turn_count") or 0), int(facts.get("seq") or 0)),
        "active": facts,
        "topics": topics,
    }


def _condition_line(item):
    return f"{item.get('column')} {OP_LABELS.get(item.get('op'), item.get('op'))} {item.get('value')}"


def render_state(memory, max_chars=1200, topic_lines=4):
    """渲染会话状态卡；有硬字符预算，超了先丢“近期主题索引”，不丢当前事实。"""
    active = (memory or {}).get("active") or {}
    if not active:
        return ""
    lines = [
        "【会话状态卡（结构化事实，优先沿用；用户最新说法与之冲突时以最新为准）】",
        f"当前主题（第{active.get('seq')}轮）：{active.get('question') or '-'}",
        f"使用表：{', '.join(active.get('tables') or []) or '无'}",
    ]
    if active.get("metrics"):
        lines.append("指标口径：" + "、".join(active["metrics"]))
    if active.get("date_filters"):
        lines.append("日期条件：" + "；".join(_condition_line(x) for x in active["date_filters"]))
    if active.get("filters"):
        lines.append("过滤条件：" + "；".join(_condition_line(x) for x in active["filters"]))
    if active.get("group_by"):
        lines.append("分组维度：" + "、".join(active["group_by"]))
    if active.get("order_by"):
        lines.append("排序：" + "、".join(active["order_by"]))
    if active.get("limit") is not None:
        lines.append(f"LIMIT：{active.get('limit')}（平台自动补的行数上限不代表业务条件）")
    if active.get("summary"):
        lines.append("上一轮结果：" + active["summary"])

    topics = (memory or {}).get("topics") or []
    recent_topics = [t for t in topics if t.get("seq") != active.get("seq")][-topic_lines:]
    if recent_topics:
        lines.append("近期主题索引：")
        for t in recent_topics:
            lines.append(
                f"- 第{t.get('seq')}轮 {_txt(t.get('question'), 50)}"
                + (f"（表：{', '.join((t.get('tables') or [])[:3])}）" if t.get("tables") else "")
            )

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n…（状态卡已截断）"
    return text


def _tokens(text):
    lowered = (text or "").lower()
    words = set(re.findall(r"[a-z0-9_]{2,}", lowered))
    for run in re.findall(r"[\u4e00-\u9fff]+", lowered):
        if len(run) == 1:
            words.add(run)
            continue
        for i in range(len(run) - 1):
            gram = run[i:i + 2]
            if gram not in STOP_BIGRAMS:
                words.add(gram)
    return words


def _score(turn, q_tokens, active_tables, active_metrics):
    text = " ".join([
        str(_field(turn, "question", "")),
        str(_field(turn, "summary", "")),
        " ".join(_field(turn, "tables", []) or []),
        str(_field(turn, "sql", "")),
    ])
    tokens = _tokens(text)
    score = len(tokens & q_tokens) * 3
    tables = {str(t).lower() for t in (_field(turn, "tables", []) or [])}
    score += len(tables & active_tables) * 2
    metrics = {str(m).lower() for m in (_field(turn, "metrics", []) or [])}
    score += len(metrics & active_metrics) * 2
    return score


def select_older(history, recent, question, memory=None, limit=6):
    """从“近期窗口”之前的轮次里，按关键词/表/指标相关度取 Top-K；不按时间硬塞。"""
    older = (history or [])[:-recent] if recent else list(history or [])
    if not older:
        return []
    explicit = TURN_RE.search(question or "")
    explicit_seq = int(explicit.group(1)) if explicit else None
    active = (memory or {}).get("active") or {}
    active_tables = {str(t).lower() for t in (active.get("tables") or [])}
    active_metrics = {str(m).lower() for m in (active.get("metrics") or [])}
    q_tokens = _tokens(question)

    scored = []
    for turn in older:
        seq = int(_field(turn, "seq", 0) or 0)
        score = 1000 if explicit_seq is not None and seq == explicit_seq else 0
        score += _score(turn, q_tokens, active_tables, active_metrics)
        if score > 0:
            scored.append((score, seq, turn))
    if explicit_seq is not None and not any(s == explicit_seq for _, s, _ in scored):
        for turn in older:
            if int(_field(turn, "seq", 0) or 0) == explicit_seq:
                scored.append((1000, explicit_seq, turn))
                break
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    selected = [t for _, _, t in scored[:max(1, int(limit))]]
    return sorted(selected, key=lambda t: int(_field(t, "seq", 0) or 0))


def render_older(selected, dialect="sqlite", max_sql=300):
    if not selected:
        return ""
    lines = ["【相关早期轮次（按当前问题检索，非连续；引用时以轮次号为准）】"]
    for turn in selected:
        facts = facts_from_turn(turn, dialect)
        bits = [f"第{facts['seq']}轮", f"问：{_txt(facts['question'], 80)}"]
        if facts.get("tables"):
            bits.append("表：" + ", ".join(facts["tables"][:4]))
        conds = facts.get("date_filters", []) + facts.get("filters", [])
        if conds:
            bits.append("条件：" + "；".join(_condition_line(x) for x in conds[:4]))
        lines.append("；".join(bits))
        if facts.get("summary"):
            lines.append(f"结果：{_txt(facts['summary'], 100)}")
        if facts.get("sql"):
            lines.append("SQL：" + _txt(facts["sql"], max_sql))
    return "\n".join(lines)


def referenced_turn(history, question):
    """用户明确说“回到第 N 轮”时，把那轮原文找出来；即使它还在近期窗口里也单独强调。"""
    match = TURN_RE.search(question or "")
    if not match:
        return None
    seq = int(match.group(1))
    for turn in history or []:
        if int(_field(turn, "seq", 0) or 0) == seq:
            return turn
    return None


def render_reference(turn, dialect="sqlite", max_sql=400):
    if not turn:
        return ""
    facts = facts_from_turn(turn, dialect)
    lines = [f"【用户指定回到第{facts['seq']}轮】"]
    lines.append(f"问：{facts['question']}")
    if facts.get("tables"):
        lines.append("表：" + ", ".join(facts["tables"]))
    if facts.get("metrics"):
        lines.append("指标：" + "、".join(facts["metrics"]))
    conds = facts.get("date_filters", []) + facts.get("filters", [])
    if conds:
        lines.append("条件：" + "；".join(_condition_line(x) for x in conds))
    if facts.get("group_by"):
        lines.append("分组：" + "、".join(facts["group_by"]))
    if facts.get("order_by"):
        lines.append("排序：" + "、".join(facts["order_by"]))
    if facts.get("summary"):
        lines.append("结果：" + facts["summary"])
    if facts.get("sql"):
        lines.append("SQL：" + _txt(facts["sql"], max_sql))
    return "\n".join(lines)


def build_history_block(history, memory=None, question="", recent=4, max_sql=600,
                        max_chars=1200, old_limit=6, dialect="sqlite"):
    """组装提示词记忆：状态卡 + 指定轮次 + 近期原文 + 相关早期轮次。

    返回 text 和统计信息，调用方可以把“用了哪些记忆”写进执行轨迹。
    """
    history = history or []
    parts = []
    info = {"recent": 0, "older": 0, "reference": 0, "state_chars": 0, "total_chars": 0}

    state_text = render_state(memory, max_chars=max_chars)
    if state_text:
        parts.append(state_text)
        info["state_chars"] = len(state_text)

    ref = referenced_turn(history, question)
    if ref is not None:
        parts.append(render_reference(ref, dialect=dialect))
        info["reference"] = int(_field(ref, "seq", 0) or 0)

    if recent:
        recent_turns = history[-int(recent):]
        info["recent"] = len(recent_turns)
        for turn in recent_turns:
            seq = _field(turn, "seq", 0)
            sql = _txt(_field(turn, "sql", "") or "（无）", max_sql)
            parts.append(
                f"第{seq}轮\n问：{_field(turn, 'question', '')}\nSQL：{sql}\n"
                f"结果摘要：{_field(turn, 'summary', '') or '（无）'}"
            )

    older = select_older(history, recent, question, memory, old_limit)
    if ref is not None:
        ref_seq = int(_field(ref, "seq", 0) or 0)
        older = [t for t in older if int(_field(t, "seq", 0) or 0) != ref_seq]
    if older:
        parts.append(render_older(older, dialect=dialect))
        info["older"] = len(older)

    text = "\n\n".join(p for p in parts if p)
    info["total_chars"] = len(text)
    return {"text": text, "info": info}
