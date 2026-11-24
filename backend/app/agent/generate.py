"""SQL 生成节点：优先模型，模型不可用时回退规则引擎。"""
from ..llm.client import extract_sql
from ..llm.prompts import build_messages
from .rule_engine import heuristic_sql


def generate(question, tables, metrics, dialect, schema_block, llm=None, error=None,
             previous_sql=None, history_block="", followup=False):
    """返回 {sql, note, status, detail}；error 用于带报错重写。"""
    if llm is not None and getattr(llm, "available", False):
        try:
            text = llm.chat(build_messages(
                question, dialect, schema_block, metrics,
                error=error, previous_sql=previous_sql,
                history_block=history_block, followup=followup,
            ))
            sql, note = extract_sql(text)
            if error:
                detail = "LLM 按报错重写"
            elif followup:
                detail = "LLM 基于上文改写"
            else:
                detail = "LLM 生成"
            return {"sql": sql, "note": note, "status": "ok", "detail": detail}
        except Exception as e:
            if error:
                # 修正阶段模型挂了，交给上层决定是否终止
                return {"sql": "", "note": "", "status": "err", "detail": f"模型重写失败：{e}"}
            return {"sql": heuristic_sql(question, tables, metrics, dialect), "note": "",
                    "status": "warn", "detail": f"LLM 不可用，回退规则引擎：{e}"}
    if error:
        return {"sql": "", "note": "", "status": "err", "detail": "未配置模型，无法自修"}
    return {"sql": heuristic_sql(question, tables, metrics, dialect), "note": "",
            "status": "warn", "detail": "未配置 LLM，已回退规则引擎（仅支持单表聚合，多表联查必失败）"}
