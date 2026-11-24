"""结果解释节点：图表类型推断 + 一句话结论（有模型时用模型，否则规则）。"""
import re


def _column_kind(columns, rows):
    """按样本判断列类型：date / number / text。"""
    kinds = []
    for i, col in enumerate(columns):
        name = (col or "").lower()
        vals = [r[i] for r in rows[:20] if i < len(r) and r[i] is not None]
        if any(k in name for k in ("date", "time", "日", "月")) or (
            vals and all(isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}", v) for v in vals)
        ):
            kinds.append("date")
        elif vals and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            kinds.append("number")
        else:
            kinds.append("text")
    return kinds


def chart_hint(columns, rows, question=""):
    if not rows or not columns:
        return {"type": "table", "x": None, "y": []}
    kinds = _column_kind(columns, rows)
    numbers = [c for c, k in zip(columns, kinds) if k == "number"]
    dates = [c for c, k in zip(columns, kinds) if k == "date"]
    texts = [c for c, k in zip(columns, kinds) if k == "text"]

    if dates and numbers:
        return {"type": "line", "x": dates[0], "y": numbers}
    if texts and numbers:
        if any(w in question for w in ("占比", "分布", "构成")) and len(rows) <= 12:
            return {"type": "pie", "x": texts[0], "y": numbers[:1]}
        return {"type": "bar", "x": texts[0], "y": numbers}
    return {"type": "table", "x": None, "y": []}


def _fmt(v):
    if isinstance(v, float):
        return f"{v:,.2f}"
    if isinstance(v, int) and abs(v) >= 10000:
        return f"{v:,}"
    return str(v)


def rule_summary(columns, rows):
    if not rows:
        return "没有查到符合条件的数据，可以放宽时间范围或去掉部分筛选条件再试。"
    n = len(rows)
    if n == 1:
        pairs = "，".join(f"{columns[i]} {_fmt(rows[0][i])}" for i in range(min(len(columns), 4)))
        return f"查询结果：{pairs}。"
    kinds = _column_kind(columns, rows)
    num_idx = [i for i, k in enumerate(kinds) if k == "number"]
    if not num_idx:
        return f"共返回 {n} 行结果。"
    idx = num_idx[-1]
    label_idx = next((i for i, k in enumerate(kinds) if k != "number"), None)
    try:
        best = max(range(n), key=lambda r: float(rows[r][idx] or 0))
    except Exception:
        return f"共返回 {n} 行结果。"
    val = rows[best][idx]
    label = rows[best][label_idx] if label_idx is not None else f"第 {best + 1} 行"
    return f"共返回 {n} 行，{columns[idx]}最高的是 {label}（{_fmt(val)}）。"


def summarize(question, columns, rows, llm=None):
    """返回 (结论文本, trace 说明)。"""
    if rows and llm is not None and getattr(llm, "available", False):
        try:
            preview = [dict(zip(columns, r)) for r in rows[:8]]
            messages = [
                {"role": "system", "content": "你是数据分析助手，用一句中文给出结论，不要复述 SQL，不要罗列全部数据。"},
                {"role": "user", "content": f"问题：{question}\n结果预览：{preview}\n请给一句结论。"},
            ]
            return llm.chat(messages, max_tokens=200).strip(), "LLM 生成结论"
        except Exception as e:
            return rule_summary(columns, rows), f"回退规则摘要：{e}"
    return rule_summary(columns, rows), "规则摘要 + 图表推断"
