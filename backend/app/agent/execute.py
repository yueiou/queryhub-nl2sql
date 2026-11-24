"""执行节点：只读校验 -> 执行 -> 按报错自修（最多两次）-> 空结果放宽。"""
import re
import time

from ..db.executor import execute_sql
from .generate import generate
from .guard import GuardError, prepare
from .state import add_step

EMPTY_HINT = "上一次查询返回 0 行，请检查过滤条件是否过严，适当放宽时间范围或去掉部分维度条件。"


def _ms(t0):
    return int((time.perf_counter() - t0) * 1000)


def _can_repair(llm):
    return llm is not None and getattr(llm, "available", False)


def run(state, question, dialect, schema_block, tables, metrics, engine, llm,
        allowed_tables, max_rows=200, enforce_limit=True, allow_relax=True):
    """就地更新 state（sql / rows / used_tables / error / trace），并返回 state。"""
    trace = state.trace
    failure = "校验"

    for attempt in range(3):
        t0 = time.perf_counter()
        try:
            safe_sql, used, limited = prepare(state.sql, dialect, allowed_tables, max_rows,
                                              enforce_limit=enforce_limit)
        except GuardError as e:
            failure = "校验"
            state.error = str(e)
            add_step(trace, "SQL 校验", "err", state.error, _ms(t0))
            if attempt < 2 and _can_repair(llm):
                fix = generate(question, tables, metrics, dialect, schema_block, llm,
                               error=state.error, previous_sql=state.sql)
                if fix["status"] != "err" and fix["sql"]:
                    state.sql = fix["sql"]
                    add_step(trace, "SQL 自修", "warn", fix["detail"])
                    continue
                state.error = fix["detail"] or state.error
            break

        add_step(trace, "SQL 校验", "ok",
                 f"只读校验通过；表 {', '.join(used) or '无'}" + ("；已自动追加 LIMIT" if limited else ""),
                 _ms(t0))

        t0 = time.perf_counter()
        try:
            data = execute_sql(engine, safe_sql, max_rows=max_rows)
        except Exception as e:
            failure = "执行"
            state.error = re.sub(r"\s+", " ", str(e))[:300]
            add_step(trace, "查询执行", "err", state.error, _ms(t0))
            state.sql = safe_sql
            if attempt < 2 and _can_repair(llm):
                fix = generate(question, tables, metrics, dialect, schema_block, llm,
                               error=state.error, previous_sql=safe_sql)
                if fix["status"] != "err" and fix["sql"]:
                    state.sql = fix["sql"]
                    add_step(trace, "SQL 自修", "warn", fix["detail"])
                    continue
                state.error = fix["detail"] or state.error
            break

        state.sql = safe_sql
        state.used_tables = used
        state.limit_applied = limited
        state.error = ""
        state.apply_result(data)
        add_step(trace, "查询执行", "ok",
                 f"{data['row_count']} 行 / {data['latency_ms']} ms"
                 + ("（已截断）" if data["truncated"] else ""), data["latency_ms"])

        if state.row_count == 0 and allow_relax:
            _relax(state, question, dialect, schema_block, tables, metrics, engine,
                   llm, allowed_tables, max_rows, enforce_limit)
        return state

    # 三轮都失败
    state.summary = (f"SQL 未通过安全校验：{state.error}" if failure == "校验"
                     else f"查询执行失败：{state.error}")
    return state


def _relax(state, question, dialect, schema_block, tables, metrics, engine,
           llm, allowed_tables, max_rows, enforce_limit=True):
    if not _can_repair(llm):
        return
    t0 = time.perf_counter()
    try:
        fix = generate(question, tables, metrics, dialect, schema_block, llm,
                       error=EMPTY_HINT, previous_sql=state.sql)
        if fix["status"] == "err" or not fix["sql"]:
            return
        safe_sql, used, limited = prepare(fix["sql"], dialect, allowed_tables, max_rows,
                                          enforce_limit=enforce_limit)
        data = execute_sql(engine, safe_sql, max_rows=max_rows)
        if data["row_count"] > 0:
            state.sql = safe_sql
            state.used_tables = used
            state.limit_applied = limited
            state.apply_result(data)
            add_step(state.trace, "结果放宽", "warn", "空结果，已重试放宽条件", _ms(t0))
    except Exception as e:
        add_step(state.trace, "结果放宽", "warn", f"放宽失败：{e}", _ms(t0))
