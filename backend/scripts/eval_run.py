"""SQLite评测：默认仅验证标准SQL；--mode rules/llm 才运行Agent。"""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import sys
import time

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))


def cell_equal(a, b):
    if a is None or b is None:
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)
    return type(a) is type(b) and a == b


def rows_equal(actual, expected, ordered=False):
    """严格行数；无序结果按多重集匹配，保留重复行。"""
    if len(actual) != len(expected):
        return False
    def same(a, b):
        return len(a) == len(b) and all(cell_equal(x, y) for x, y in zip(a, b))
    if ordered:
        return all(same(a, b) for a, b in zip(actual, expected))
    edges = [[j for j, b in enumerate(expected) if same(a, b)] for a in actual]
    owners = {}
    def assign(i, seen):
        for j in edges[i]:
            if j in seen:
                continue
            seen.add(j)
            if j not in owners or assign(owners[j], seen):
                owners[j] = i
                return True
        return False
    return all(assign(i, set()) for i in range(len(actual)))


def readonly_engine(path):
    from sqlalchemy import create_engine, event
    uri = path.resolve().as_uri() + "?mode=ro"
    engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(uri, uri=True))
    deadline = [float("inf")]
    @event.listens_for(engine, "connect")
    def protect(conn, _):
        conn.execute("PRAGMA query_only=ON")
        conn.set_progress_handler(lambda: int(time.monotonic() > deadline[0]), 10000)
    @event.listens_for(engine, "before_cursor_execute")
    def start(*_):
        deadline[0] = time.monotonic() + 15
    return engine


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["validate", "rules", "llm"], default="validate")
    p.add_argument("--cases", type=Path, default=BASE / "eval/questions.jsonl")
    p.add_argument("--data-dir", type=Path, default=BASE / "data")
    p.add_argument("--split", choices=["dev", "holdout"])
    p.add_argument("--category")
    p.add_argument("--id", help="用例ID；多轮题自动包含整个会话")
    p.add_argument("--output", type=Path, default=BASE / "eval/results/latest.json")
    args = p.parse_args()
    from app.agent.guard import GuardError, prepare
    from app.agent.pipeline import answer
    from app.core import config
    from app.db.executor import execute_sql
    from app.db.introspect import introspect
    from app.llm.client import LLMClient

    all_cases = [json.loads(s) for s in args.cases.read_text(encoding="utf-8").splitlines() if s.strip()]
    if len({c["id"] for c in all_cases}) != len(all_cases):
        p.error("重复用例ID")
    cases = [c for c in all_cases if (not args.split or c["split"] == args.split)
             and (not args.category or c["category"] == args.category)
             and (not args.id or c["id"] == args.id)]
    sessions = {c["session"] for c in cases if c.get("session")}
    cases = [c for c in all_cases if c in cases or c.get("session") in sessions]
    if not cases:
        p.error("没有匹配用例")
    llm = None
    if args.mode == "llm":
        if config.llm_missing():
            p.error("模型配置缺失：" + ",".join(config.llm_missing()))
        llm = LLMClient(config.LLM_BASE_URL, config.LLM_API_KEY, config.LLM_MODEL, config.LLM_TIMEOUT)

    # 固定口径，不读取或写入用户的platform.db。
    metrics = [dict(name=n, aliases=a, formula=e, table_name="fact_order", note=d)
               for n, a, e, d in [
                   ("GMV", "销售额,成交额,营收,收入,销售金额", "SUM(pay_amount)", "全部订单实付金额，包含退款状态"),
                   ("订单量", "订单数,单量,订单数量,多少单", "COUNT(*)", "订单笔数"),
                   ("客单价", "平均订单金额,平均客单,均值", "AVG(pay_amount)", "平均每单实付金额"),
                   ("退款单量", "退款订单,退款数", "COUNT(CASE WHEN status='refunded' THEN 1 END)", "已退款订单数")]]
    engines, catalogs, hashes = {}, {}, {}
    history = defaultdict(list)
    results = []
    for c in cases:
        row = {k: c[k] for k in ("id", "category", "difficulty", "domain", "database", "split")}
        row["passed"] = False
        t0 = time.perf_counter()
        try:
            db = c["database"]
            if db not in engines:
                path = (args.data_dir / db).resolve()
                if not path.is_file():
                    raise FileNotFoundError(f"数据库不存在：{path}")
                hashes[db] = hashlib.sha256(path.read_bytes()).hexdigest()
                engines[db] = readonly_engine(path)
                catalogs[db] = introspect(engines[db])
            engine, catalog = engines[db], catalogs[db]
            allowed = [t["name"] for t in catalog["tables"] if not t["name"].startswith("sqlite_")]
            if c.get("kind") == "guard":
                # 攻击SQL只做护栏判断，不执行。
                try:
                    safe, _, limited = prepare(c["sql"], "sqlite", allowed)
                    row.update(observed="allow", safe_sql=safe, limit_applied=limited)
                except GuardError as e:
                    row.update(observed="reject", reason=str(e))
                row["passed"] = row["observed"] == c["expected"]
            else:
                gold, _, _ = prepare(c["gold_sql"], "sqlite", allowed, enforce_limit=False)
                expected = execute_sql(engine, gold, max_rows=200)
                if expected["truncated"]:
                    raise ValueError("gold超过200行；请明确任务结果上限，避免截断掩盖错误")
                row.update(gold_valid=True, gold_sql=gold, gold_rows=expected["rows"], gold_columns=expected["columns"])
                if args.mode == "validate":
                    row["passed"] = True
                else:
                    h = history[c["session"]] if c.get("session") else []
                    res = answer(c["question"], catalog, metrics if db == "demo_shop.db" else [],
                                 engine, llm, max_rows=200, history=h)
                    executed = not res.get("error") and any(t["node"] == "查询执行" and t["status"] == "ok" for t in res.get("trace", []))
                    fallback = any("规则引擎" in t.get("detail", "") for t in res.get("trace", []))
                    match = executed and not res.get("truncated") and len(res["columns"]) == len(expected["columns"]) and rows_equal(res["rows"], expected["rows"], c["ordered"])
                    context_ok = "expected_context" not in c or res["context_used"] == c["expected_context"]
                    row.update(executed=executed, result_match=match, context_ok=context_ok,
                               fallback=fallback, response=res, passed=bool(match and context_ok))
                    if c.get("session"):
                        h.append(dict(question=c["question"], sql=res.get("sql", ""), summary=res.get("summary", "")))
        except Exception as e:
            row["error"] = str(e)
        row["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        results.append(row)
        print(f"{row['id']}: {'PASS' if row['passed'] else 'FAIL'}", flush=True)

    grouped = {}
    for dimension in ("category", "difficulty", "domain", "split"):
        counts = defaultdict(lambda: [0, 0])
        for r in results:
            counts[r[dimension]][1] += 1
            counts[r[dimension]][0] += int(r["passed"])
        grouped[dimension] = {k: dict(passed=v[0], total=v[1], rate=v[0]/v[1]) for k, v in counts.items()}
    queries = [r for r in results if r["category"] != "guard"]
    guards = [r for r in results if r["category"] == "guard"]
    expected_guards = {c["id"]: c["expected"] for c in cases if c.get("kind") == "guard"}
    guard_metrics = {}
    for expectation, key in (("reject", "dangerous_rejection_rate"), ("allow", "legal_allow_rate")):
        subset = [r for r in guards if expected_guards[r["id"]] == expectation]
        guard_metrics[key] = sum(r["passed"] for r in subset)/len(subset) if subset else None
    latencies = sorted(r["response"]["elapsed_ms"] for r in queries if "response" in r)
    def rate(key):
        return sum(bool(r.get(key)) for r in queries) / len(queries) if queries else None
    session_scores = []
    for session in sessions:
        ids = {c["id"] for c in cases if c.get("session") == session}
        session_scores.append(all(r["passed"] for r in results if r["id"] in ids))
    report = dict(mode=args.mode, created_at=datetime.now(timezone.utc).isoformat(),
                  case_sha256=hashlib.sha256(args.cases.read_bytes()).hexdigest(), database_sha256=hashes,
                  model=config.LLM_MODEL if args.mode == "llm" else None,
                  config={k: getattr(config, k) for k in ("SCHEMA_MODE", "TOP_K", "FULL_SCHEMA_MAX", "MAX_SCHEMA_TABLES", "CONTEXT_RECENT", "MEMORY_STATE_CHARS", "MEMORY_OLD_LIMIT")},
                  total=len(results), passed=sum(r["passed"] for r in results), breakdown=grouped, guard_metrics=guard_metrics,
                  query_metrics=None if args.mode == "validate" else dict(execution_rate=rate("executed"), result_accuracy=rate("result_match"),
                      fallback_rate=rate("fallback"), session_accuracy=sum(session_scores)/len(session_scores) if session_scores else None,
                      p50_ms=latencies[math.ceil(len(latencies)*0.5)-1] if latencies else None,
                      p95_ms=latencies[math.ceil(len(latencies)*0.95)-1] if latencies else None), results=results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for engine in engines.values():
        engine.dispose()
    print(json.dumps({k: report[k] for k in ("mode", "total", "passed", "query_metrics")}, ensure_ascii=False))
    print(f"报告：{args.output.resolve()}")
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
