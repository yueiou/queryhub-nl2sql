"""问数流水线：把意图、召回、生成、执行、解释五个节点串起来，并处理多轮追问。"""
import time

from ..core import config
from ..schemas.catalog import table_names
from . import context as ctx
from . import execute
from . import memory as mem
from .explain import chart_hint, summarize
from .generate import generate
from .intent import is_schema_question, schema_answer
from .retrieve import matched_metrics, render_schema, retrieve, visible_tables
from .state import QueryState, add_step


def _ms(t0):
    return int((time.perf_counter() - t0) * 1000)


def _field(turn, name, default=None):
    if turn is None:
        return default
    if isinstance(turn, dict):
        return turn.get(name, default)
    return getattr(turn, name, default)


def _merge_context_tables(tables, catalog, wanted, cap=8):
    """把上一轮用过的表强行保留在召回结果里，防止追问时重新猜到别的表。"""
    if not wanted:
        return tables
    by_name = {t["name"].lower(): t for t in catalog.get("tables", [])}
    extra = [by_name[n.lower()] for n in wanted if n.lower() in by_name]
    merged = extra + [t for t in tables if t["name"].lower() not in {e["name"].lower() for e in extra}]
    return merged[:cap]


def answer(question, catalog, metrics, engine, llm=None, max_rows=200, history=None,
           memory=None):
    t_all = time.perf_counter()
    question = (question or "").strip()
    if not question:
        raise ValueError("问题不能为空")

    history = ctx.sanitize_history(history or [], catalog)
    followup = ctx.is_followup(question, history)
    last = history[-1] if history else None
    prev_sql = _field(last, "sql", "") or ""
    prev_question = _field(last, "question", "") or ""

    state = QueryState(question=question)
    state.context_used = bool(followup and history)
    state.context_turns = len(history)
    trace = state.trace
    dialect = catalog.get("sqlglot_dialect") or "sqlite"

    # 1. 意图识别
    t0 = time.perf_counter()
    if is_schema_question(question):
        state.answer_type = "schema"
        state.summary = schema_answer(catalog)
        add_step(trace, "意图识别", "ok", "结构类问题，直接读 schema 目录", _ms(t0))
        state.elapsed_ms = _ms(t_all)
        return state.to_dict()
    add_step(trace, "意图识别", "ok",
             "追问，继承上一轮上下文" if followup else "数据查询，进入问数流程", _ms(t0))

    # 2. 模式召回：小库直接全量，大库按相关度取 TOP_K；追问时把上一轮问题和表一起带上
    t0 = time.perf_counter()
    retrieval_question = f"{prev_question} {question}".strip() if followup else question
    visible = visible_tables(catalog)
    use_all = config.SCHEMA_MODE == "all" or (
        config.SCHEMA_MODE == "auto" and len(visible) <= config.FULL_SCHEMA_MAX
    )
    if use_all:
        tables = visible[:config.MAX_SCHEMA_TABLES]
        hit_metrics = matched_metrics(retrieval_question, metrics)
        recall_note = f"全量注入 {len(tables)}/{len(visible)} 张表"
        if len(visible) > config.MAX_SCHEMA_TABLES:
            recall_note += "（已达注入上限，可用 NL2SQL_MAX_SCHEMA_TABLES 调大）"
    else:
        hit = retrieve(retrieval_question, catalog, metrics, top_k=config.TOP_K)
        tables = _merge_context_tables(hit["tables"], catalog, ctx.previous_tables(history))
        hit_metrics = hit["metrics"]
        recall_note = f"相关度召回 {len(tables)}/{len(visible)} 张表"
    # 全量模式下不再按 40 列裁剪，保证表里的列都给到模型
    schema_block = render_schema(tables, max_cols=None if use_all else 40)
    budget_note = f"，DDL {len(schema_block)} 字符"
    if len(schema_block) > config.SCHEMA_WARN_CHARS:
        budget_note += "（已超建议预算，模型上下文不够时会被截断）"
    add_step(trace, "模式召回", "ok",
             f"{recall_note}：{', '.join(t['name'] for t in tables[:12])}"
             + (" …" if len(tables) > 12 else "")
             + f"；匹配口径 {len(hit_metrics)} 个：{', '.join(m['name'] for m in hit_metrics) or '无'}"
             + ("（沿用上轮表）" if followup and not use_all else "")
             + budget_note,
             _ms(t0))

    # 3. SQL 生成：明确的“去掉 limit”直接用上一轮 SQL 改写，不赌模型
    t0 = time.perf_counter()
    no_limit = ctx.wants_drop_limit(question)
    history_block = ""
    memory_info = {"recent": 0, "older": 0, "state_chars": 0, "total_chars": 0}
    if followup:
        built = mem.build_history_block(
            history, memory=memory, question=question,
            recent=config.CONTEXT_RECENT, max_chars=config.MEMORY_STATE_CHARS,
            old_limit=config.MEMORY_OLD_LIMIT, dialect=dialect,
        )
        history_block = built["text"]
        memory_info = built["info"]
        add_step(trace, "记忆装配", "ok",
                 f"状态卡 {memory_info['state_chars']} 字符 · 近期 {memory_info['recent']} 轮 · "
                 f"相关早期 {memory_info['older']} 轮"
                 + (f" · 指定第 {memory_info.get('reference')} 轮" if memory_info.get("reference") else "")
                 + f" · 合计 {memory_info['total_chars']} 字符",
                 _ms(t0))
    state.metrics_used = [m.get("name", "") for m in hit_metrics if m.get("name")]

    if followup and prev_sql and no_limit and ctx.only_drop_limit(question):
        gen = {
            "sql": ctx.drop_limit(prev_sql, dialect),
            "note": "按上文去掉 LIMIT",
            "status": "ok",
            "detail": "按指令改写上一条 SQL：去掉 LIMIT（展示层仍按行数上限截断）",
        }
    else:
        gen = generate(
            question, tables, hit_metrics, dialect, schema_block, llm,
            previous_sql=prev_sql if followup else None,
            history_block=history_block,
            followup=followup,
        )
        # 用户要求去掉 LIMIT 时，即使模型带了 LIMIT，也在顶层确定性去掉
        if no_limit and gen.get("sql"):
            gen["sql"] = ctx.drop_limit(gen["sql"], dialect)
    state.sql = gen["sql"]
    state.analysis = gen["note"]
    add_step(trace, "SQL 生成", gen["status"], gen["detail"], _ms(t0))

    # 4. 校验 / 执行 / 自修
    execute.run(
        state, question=question, dialect=dialect, schema_block=schema_block,
        tables=tables, metrics=hit_metrics, engine=engine, llm=llm,
        allowed_tables=table_names(catalog), max_rows=max_rows,
        enforce_limit=not no_limit,
        # 用户只是想改查询条件时，不要“空结果自动放宽”把过滤条件偷偷删掉
        allow_relax=not (followup and no_limit),
    )
    if state.error:
        state.elapsed_ms = _ms(t_all)
        return state.to_dict()

    # 5. 解释与图表
    t0 = time.perf_counter()
    state.chart = chart_hint(state.columns, state.rows, question)
    state.summary, explain_detail = summarize(question, state.columns, state.rows, llm)
    add_step(trace, "结果解释", "ok", explain_detail, _ms(t0))

    state.elapsed_ms = _ms(t_all)
    return state.to_dict()
