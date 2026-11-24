"""多轮上下文：判断追问、清理跨库历史、按明确指令改写上一轮 SQL。"""
import re

import sqlglot

# 短句或带这些词，基本可以判定是在接着上一轮说
FOLLOWUP_HINTS = (
    "去掉", "不要", "不用", "取消", "换成", "改成", "加上", "增加", "再看", "再查",
    "继续", "那", "它", "这个", "上面", "刚才", "上一个", "按上个", "同样", "再来",
    "试试", "能不能查到", "为什么", "什么原因", "解释一下",
    "还是", "上次", "之前", "回到", "那条", "这条", "同一",
    "排名", "排序", "前几", "前5", "前 5", "top", "再",
    "按月份", "按年", "按天", "按周", "只看", "仅看", "限定", "只保留", "只查",
)

DROP_LIMIT_HINTS = (
    "去掉limit", "去掉 limit", "取消limit", "取消 limit", "不要limit", "不要 limit",
    "不用limit", "移除limit", "去掉行数限制", "去掉条数限制", "不限条数", "去掉200",
)

DATE_HINT_RE = re.compile(r"\d{2,4}\s*年|\d{1,2}\s*月|上个月|本月|去年|今年|季度")
_COMMON_BIGRAMS = {
    "查询", "统计", "信息", "数据", "一下", "帮我", "多少", "哪些", "怎么",
    "这个", "那个", "现在", "今年", "去年", "本月", "上个", "还是", "可以",
}


def _entity_tokens(text):
    tokens = set()
    for run in re.findall(r"[\u4e00-\u9fff]+", text or ""):
        for i in range(max(0, len(run) - 1)):
            gram = run[i:i + 2]
            if gram not in _COMMON_BIGRAMS:
                tokens.add(gram)
    return tokens


def is_followup(question, history):
    """没有历史就不是追问；短句只在同主题、换时间或明确指代时算追问。

    旧版把所有 ≤10 字的短句都当追问，会把“统计商品销量”这类新问题接到会员上下文里；
    现在短句至少要和上一轮共享一个实体词，或共享日期上下文，才继承上文。
    """
    if not history:
        return False
    q = (question or "").strip().lower()
    if not q:
        return False
    last = history[-1]
    last_q = (last.get("question") if isinstance(last, dict) else getattr(last, "question", "")) or ""
    if len(q) <= 10:
        if _entity_tokens(q) & _entity_tokens(last_q):
            return True
        if DATE_HINT_RE.search(q) and DATE_HINT_RE.search(last_q):
            return True
        return any(h in q for h in FOLLOWUP_HINTS)
    if any(h in q for h in FOLLOWUP_HINTS):
        return True
    # “26年4月” -> “26年5月”这类只换时间、主题名词不变的追问
    if DATE_HINT_RE.search(q) and DATE_HINT_RE.search(last_q):
        return bool(_entity_tokens(q) & _entity_tokens(last_q))
    return False


def wants_drop_limit(question):
    q = re.sub(r"\s+", "", (question or "").lower())
    return any(re.sub(r"\s+", "", h) in q for h in DROP_LIMIT_HINTS)


def only_drop_limit(question):
    """判断“只要求去掉 LIMIT、没有夹带别的修改”。

    “只看天猫，去掉limit”不能直接拿上一条 SQL 去 LIMIT，否则“只看天猫”会被静默丢掉；
    只有去掉 LIMIT 相关词和语气词后不剩内容，才允许确定性改写。
    """
    q = re.sub(r"\s+", "", (question or "").lower())
    for hint in DROP_LIMIT_HINTS:
        q = q.replace(re.sub(r"\s+", "", hint), "")
    for filler in ("请", "帮我", "麻烦", "再", "那", "试试", "看看", "一下", "吧", "呢", "吗", "。", "，", ",", "."):
        q = q.replace(filler, "")
    return not q.strip()


def drop_limit(sql, dialect):
    """去掉顶层 LIMIT；解析失败时退回正则，避免把整条 SQL 弄坏。"""
    try:
        ast = sqlglot.parse_one(sql, read=dialect or None)
        ast.set("limit", None)
        ast.set("limit_options", None)
        return ast.sql(dialect=dialect or None)
    except Exception:
        return re.sub(r"\s+limit\s+\d+\s*$", "", sql, flags=re.I).strip()


def sanitize_history(history, catalog):
    """历史必须属于当前库：只丢弃用了当前 catalog 里不存在表的轮次。

    以前是一轮脏表名就整段作废；现在保留其余有效轮次，避免一次幻觉把长期记忆全部清空。
    如果换库后整段历史都不属于当前库，返回空列表。
    """
    if not history:
        return []
    allowed = {t["name"].lower() for t in catalog.get("tables", [])}
    valid = []
    for turn in history:
        tables = turn.get("tables") if isinstance(turn, dict) else getattr(turn, "tables", None)
        if tables and not {t.lower() for t in tables} <= allowed:
            continue
        valid.append(turn)
    return valid


def previous_tables(history):
    """上一轮真正用到的表，追问时强制带进召回结果，避免重新猜到无关表。"""
    if not history:
        return []
    last = history[-1]
    tables = last.get("tables") if isinstance(last, dict) else getattr(last, "tables", None)
    return list(tables or [])
