"""模式召回：按中文/英文 token 与指标别名，从全库中挑出最相关的表与口径。"""
import re

CJK = re.compile(r"[\u4e00-\u9fff]+")
NUMERIC_HINTS = ("INT", "DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "REAL", "NUMBER", "MONEY")
AGG_WORDS = ("金额", "销售额", "成交额", "gmv", "营收", "收入", "总额", "客单价", "平均",
             "趋势", "走势", "排名", "最多", "最高", "最好", "畅销", "热销", "占比", "分布",
             "各", "每", "按", "分别")


def tokens(text):
    text = (text or "").lower()
    out = set()
    for w in re.findall(r"[a-z0-9]+", text):
        out.add(w)
        if "_" in w:
            out.update(p for p in w.split("_") if p)
    for run in CJK.findall(text):
        if len(run) <= 2:
            out.add(run)
        else:
            out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out


def _is_numeric(ctype):
    up = (ctype or "").upper()
    return any(h in up for h in NUMERIC_HINTS)


def _has_date(t):
    for c in t.get("columns", []):
        up = (c.get("type") or "").upper()
        txt = (c.get("name") or "") + " " + (c.get("comment") or "")
        if any(h in up for h in ("DATE", "TIME")) or any(k in txt for k in ("日期", "时间", "date", "time")):
            return True
    return False


def _table_tokens(t):
    name_tokens = tokens(t.get("name", "") + " " + (t.get("comment") or ""))
    all_tokens = set(name_tokens)
    for c in t.get("columns", []):
        all_tokens |= tokens((c.get("name") or "") + " " + (c.get("comment") or ""))
    return name_tokens, all_tokens


def matched_metrics(question, metrics):
    """按别名长度降序匹配，避免“金额”抢走“销售金额”的口径。"""
    hits = []
    for m in metrics:
        aliases = [m.get("name") or ""]
        aliases += [a.strip() for a in re.split(r"[,，、/]", m.get("aliases") or "") if a.strip()]
        best = ""
        for a in aliases:
            if a and a in question and len(a) > len(best):
                best = a
        if best:
            hits.append((len(best), m))
    hits.sort(key=lambda x: -x[0])
    return [m for _, m in hits]


def visible_tables(catalog):
    return [t for t in catalog.get("tables", [])
            if not t["name"].startswith("_") and t["name"] != "sqlite_sequence"]


def retrieve(question, catalog, metrics=None, top_k=6):
    metrics = metrics or []
    qt = tokens(question)
    hits = matched_metrics(question, metrics)
    # 纯实体计数（一共多少客户/商品）不该被事实表加成带偏
    agg_like = any(w in question.lower() for w in AGG_WORDS)

    scored = []
    for t in catalog.get("tables", []):
        if t["name"].startswith("_") or t["name"] == "sqlite_sequence":
            continue
        name_tokens, all_tokens = _table_tokens(t)
        score = 0
        for tok in qt:
            if tok in name_tokens:
                score += 3
            elif tok in all_tokens:
                score += 1
        # 聚合问题优先事实表：度量丰富、带时间维度、命名含事实层标识
        if agg_like:
            numeric_cols = sum(1 for c in t.get("columns", []) if _is_numeric(c.get("type")))
            if numeric_cols >= 2:
                score += 2
            if _has_date(t):
                score += 1
            if any(k in (t["name"] or "").lower() for k in ("fact", "f_", "dwd", "dws", "事实", "明细")):
                score += 2
        for m in hits:
            if (m.get("table_name") or "").lower() == t["name"].lower():
                score += 6
        scored.append((score, t))

    scored.sort(key=lambda x: (-x[0], -len(x[1]["columns"])))
    picked = [t for s, t in scored[:top_k] if s > 0]
    if not picked:
        picked = [t for _, t in scored[:top_k]]

    # 不足 top_k 时按得分顺序补齐：宁可多给几张零分表，也不让真正的目标表因为没命中关键词而缺席
    want = min(top_k, len(scored))
    if len(picked) < want:
        names = {t["name"] for t in picked}
        for _, t in scored:
            if t["name"] in names:
                continue
            picked.append(t)
            if len(picked) >= want:
                break
    return {"tables": picked, "metrics": hits}


def render_schema(tables, max_cols=40):
    """把召回的表渲染成给模型看的紧凑 DDL。"""
    blocks = []
    for t in tables:
        head = f"表 {t['name']}"
        if t.get("comment"):
            head += f"（{t['comment']}）"
        lines = [head]
        for c in t["columns"][:max_cols]:
            parts = [f"  {c['name']} {c.get('type') or ''}".rstrip()]
            if c.get("pk"):
                parts.append("PK")
            if c.get("comment"):
                parts.append(f"-- {c['comment']}")
            if c.get("values"):
                parts.append(f"-- 取值: {' / '.join(c['values'][:8])}")
            lines.append(" ".join(parts))
        if t.get("fks"):
            for f in t["fks"]:
                lines.append(f"  外键: {', '.join(f['columns'])} -> {f['ref_table']}({', '.join(f['ref_columns'])})")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
