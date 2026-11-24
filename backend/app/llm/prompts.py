"""提示词构造：schema + 指标口径 + 多轮上下文 + 修正上下文。"""

SYSTEM_PROMPT = """你是一名严谨的 SQL 分析师，负责把中文业务问题翻译成一条可执行的只读 SQL。

硬性规则：
1. 只输出一条 SELECT（可含 WITH），禁止 INSERT/UPDATE/DELETE/DDL，禁止分号。
2. 只能使用给定 schema 中的表和列，不得编造表名或字段名；列名保持原样。没有出现在 schema 里的表（例如 t_001）绝对不能用。
3. 目标方言是 {dialect}，请使用该方言的函数与语法。
4. 有业务口径（指标定义）时，必须严格按口径计算，不得自行改写条件。
5. 时间范围不明确时，按“最近 30 天”处理，并在 analysis 中说明假设。
6. 结果默认按指标降序，除非用户要求其他排序；聚合查询不要带无意义的明细列。
7. 不确定时给出最合理的一条 SQL，不要返回多条候选。
8. 问“某天/某日办理、注册、开通、创建”的会员、用户、订单等实体时，用实体主表的创建时间列做等值过滤（如 {dialect} 的 DATE '2026-04-10'），默认是计数或列出明细，不要改成交易金额的年度汇总。
9. 中文年份缩写要还原：“26年”就是 2026 年，“25年”是 2025 年。只有出现“趋势、每月、各年、逐年、同比、环比”时才按时间分组；只给一个具体日期时不要 GROUP BY 年份。
10. 状态、类型、渠道、等级这类枚举列，必须使用 schema 中列出的真实取值（on_sale、refunded 这类原值），不得翻译成中文近义词；schema 没给取值时，优先按列名与注释推断原值。
11. 问“会员、用户、商品、门店、课程、患者”等实体时，优先用该实体的主表，不要用订单、流水等交易表代替实体表；只有问金额、销量、笔数时才落到交易/明细表。

输出格式：严格输出 JSON：
{{"sql": "....", "analysis": "一句话说明口径与假设", "chart": "line|bar|pie|table"}}"""


def build_messages(question, dialect, schema_block, metrics, error=None, previous_sql=None,
                   history_block="", followup=False):
    metric_lines = []
    for m in metrics:
        metric_lines.append(
            f"- {m['name']}（别名：{m.get('aliases') or '无'}）：{m['formula']}，主表 {m.get('table_name') or '未指定'}"
        )
    metric_block = "\n".join(metric_lines) if metric_lines else "（无预置口径，按字段语义计算）"

    content = f"""数据库方言：{dialect}

可用表结构：
{schema_block}

业务指标口径：
{metric_block}
"""
    if history_block:
        content += f"""
对话上文（同一会话，越靠后越新）：
{history_block}

记忆使用规则：
- 状态卡是已执行查询的显式事实（表、指标、日期条件、过滤、分组、排序）；追问时优先沿用，不要重新猜表。
- “相关早期轮次”是按当前问题检索出来的片段，不一定连续；引用时核对轮次号，不要把它当成刚刚执行的结果。
- 用户最新说法与状态卡冲突时，以用户最新说法为准；不要保留用户明确要求去掉的条件。
- 用户说“只看 / 仅看 / 限定 / 只保留某个值”时，必须把它落到 WHERE 过滤条件里，不能只改分组或展示。
"""
    content += f"""
当前问题：{question}
"""
    if followup and previous_sql:
        content += f"""
这是一次追问/修改，请以上一条 SQL 为基准改写：
上一条 SQL：
{previous_sql}

改写要求：
- 用户只是增删条件、改排序、改行数、换口径时，保留原有的表、关联关系、过滤条件和聚合方式，只改用户明确提到的部分。
- 除非用户明确换了查询对象，否则不要重新选表，更不要引入上文没出现过的表。
- 如果用户要求“去掉 limit”，就真的不要输出 LIMIT；平台会在展示层做行数截断。
"""
    if error:
        content += f"""
上一次生成的 SQL：
{previous_sql}

执行/校验报错：
{error}

请修正上述错误，仍只输出一条只读 SQL，并返回同样格式的 JSON。"""
    content += "\n请输出 JSON。"
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(dialect=dialect)},
        {"role": "user", "content": content},
    ]
