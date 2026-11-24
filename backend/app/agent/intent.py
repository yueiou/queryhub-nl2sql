"""意图识别：结构类问题直接读目录，数据类问题进入问数链路。"""
from ..schemas.catalog import describe_catalog

SCHEMA_WORDS = ("有哪些表", "表结构", "字段", "哪些列", "哪些字段", "数据字典", "schema")
DATA_WORDS = ("统计", "多少", "金额", "数量", "排名", "趋势", "对比", "最近", "近", "本月",
              "今年", "占比", "分布", "top", "最多", "最高", "列出", "明细")


def is_schema_question(question):
    ql = question.lower()
    return any(w in ql for w in SCHEMA_WORDS) and not any(w in ql for w in DATA_WORDS)


def schema_answer(catalog):
    return describe_catalog(catalog)
