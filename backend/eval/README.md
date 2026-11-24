# QueryHub 项目评测集 v1

这套评测基于仓库实际 SQLite 表结构，覆盖 SQL 语义正确性、业务口径、多轮上下文与只读护栏。题目、标准 SQL、运行器均已落地。它评估 `agent.pipeline.answer` 的完整流水线，不依赖前端启动，不写入平台历史和日志。

## 交付与规模

| 内容 | 数量 | 用途 |
|---|---:|---|
| 7 个业务域，每域 5 题 × A/B 两库 | 70 | 多表关联、去重、关联膨胀、反连接、比例、时长、窗口函数 |
| 150 表宽库 | 4 | 选表、同类表干扰、事实表分别聚合后关联 |
| 演示电商 | 12 | 单表、指标口径、枚举、时间边界、空结果、NULL |
| 连续对话 | 6 轮 | 增加过滤、替换时间、切换主题、移除 LIMIT、回到早期轮次 |
| SQL 护栏 | 12 | 9 条拒绝题、3 条合法放行题 |
| 人工验收 | 12 | 澄清、拒绝危险自然语言、跨库隔离、解释与导出等 |

自动集共 **104 条：92 条问数 + 12 条护栏**。A/B 是同一问题在不同合成数据上复测，并非 70 个独立题型。`dev` 47 条（A 库和演示题），`holdout` 57 条（B 库、宽库、多轮、护栏）。B 库用于检查数据分布变化，不代表未见过的 schema 泛化；本目录的 holdout 答案是公开的，正式盲测需另留未公开数据和题目。

- `questions.jsonl`：机器可读用例，包含数据库相对路径、问题、标准 SQL、排序要求、难度、类别、划分；多轮题额外包含 session、turn、expected_context。
- `CASES.md`：可直接阅读和复制提问的完整题目及 SQL。
- `MANUAL_CASES.md`：人工验收步骤及逐项通过标准。
- `build_cases.py`：固定题集构建源；使用脱敏结构清单 databases.json，运行后同时更新 JSONL 和 Markdown。
- `results/gold_validation.json`：标准 SQL 与护栏校验记录。
- `results/rules_baseline.json`：明确关闭模型后的规则基线，不能当成配置模型后的成绩。

## 运行

在项目根目录运行（已安装 backend/requirements.txt）：

```powershell
# 不调用模型：执行所有标准 SQL，验证题集及护栏
python backend/scripts/eval_run.py --mode validate --output backend/eval/results/gold_validation.json

# 不调用模型：测规则降级模式的实际能力
python backend/scripts/eval_run.py --mode rules --output backend/eval/results/rules_baseline.json

# 使用项目已有的 backend/.env 或环境变量配置，真实调用模型
python backend/scripts/eval_run.py --mode llm --output backend/eval/results/llm_run1.json

# 调试单题、某类题或开发集
python backend/scripts/eval_run.py --mode llm --id demo_02
python backend/scripts/eval_run.py --mode llm --category fanout
python backend/scripts/eval_run.py --mode llm --split dev

# 评分器自身的回归测试
python -m unittest discover -s backend/eval -p test_eval_runner.py
```

`llm` 需 `LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL` 完整配置；正常问数会调用生成、解释，失败时还可能自修。缺配置会直接退出，不把无模型运行伪装成模型评测。评测过程中模型调用失败而退回规则时，报告保留真实结果并计入 `fallback_rate`，应单列说明。

公开仓库不包含本地数据库。首次运行先执行 `python backend/scripts/seed_demo.py` 和 `python backend/scripts/generate_testdata.py --scale 0.8`；这些脚本会重建对应合成库，已有数据时无需重复运行。数据迁移时使用 `--data-dir` 指向包含 demo_shop.db 与 testdata/ 的目录。运行器按路径定位数据，不依赖连接ID或第一个注册的数据源。输出默认写入 results/latest.json；正式对比请显式指定不同文件名，避免覆盖。

过滤到任一多轮题时，运行器自动包含该会话的全部6轮。每轮接收模型前一轮的实际 SQL 和摘要，未使用标准答案替换上下文，前序出错会影响后续，这属于端到端会话能力。运行器使用内存中的历史，不覆盖服务端持久化和跨库校验；后两者见人工验收。

## 口径与标准答案

1. 演示库 GMV 为 `SUM(pay_amount)`，包含退款状态；客单价为 `AVG(pay_amount)`；退款数按 `status='refunded'`。固定指标使用与项目一致的 `formula/table_name/note` 字段。
2. 其他域的题目直接写清金额来源、是否筛状态、去重粒度、分母和 NULL 规则，不将未定义的“收入”“流失率”当作唯一标准答案。
3. 日期使用明确年份和左闭右开区间，不依赖今天；金额不擅自四舍五入。比例默认0到1小数。
4. 类目题按 products.category_id 直接所属类目统计，不将父类目误称一级类目。贷款题统计本金还款，不以 principal 充当实时贷款余额。
5. 宽库 f_sales 只关联 d_region，f_orders 只关联 d_channel；schema 没有共同订单键，不能臆造 region/channel/product 的同一销售明细 JOIN。
6. 多行明细、支付、退款先聚合到合适粒度，避免一对多乘积。Gold SQL 是一种参考写法，允许 CTE、子查询等其他等价实现。
7. 标准结果必须不超过200行；规模较大的分组题明确按ID排序取前100行，不以展示截断后的前缀比较全量答案。

## 自动评分

| 指标 | 定义 |
|---|---|
| execution_rate | 成功完成查询执行且最终无 error 的问数题 / 92；合法空结果也是成功 |
| result_accuracy | 与标准查询结果一致的问数题 / 92 |
| 多轮逐轮通过 | 结果一致且 context_used 符合该轮期望；按 multi_turn 分组查看 |
| session_accuracy | 全部轮次均通过的会话 / 会话数；v1仅1段会话，不能视为稳定统计估计 |
| 护栏拒绝率 | 9条危险SQL实际被拒绝的比例；从 guard 逐题明细统计 |
| 合法放行率 | 3条合法SQL被放行的比例；防止全部拒绝也得高分 |
| fallback_rate | 有规则降级轨迹的问数题 / 92 |
| P50 / P95 | 有 Agent 响应的问数题中 elapsed_ms 的最近秩分位数，包含成功和失败响应，不含数据库自省和 gold执行 |

结果比对要求列数与列位置一致，忽略别名；有排序要求时按行顺序比较，否则按保留重复行的多重集比较。NULL不等于0、空串或字符串NULL。数值采用绝对误差1e-6、相对误差1e-9。多行、少行、列位置颠倒、错误截断都判失败；不按 SQL 文本完全相等打分。

`validate` 的通过仅表示标准 SQL 可执行且没有超过行数上限，以及护栏行为符合预期，**不代表 Agent 的准确率**。报告的 `passed/total` 混合了多种测试，只用于回归状态；能力结论应引用各分组和 query_metrics，不将12条护栏题计入问数准确率。`--mode rules/llm` 有未通过项会返回退出码1，仍会完整写报告；全通过返回0。

建议初始验收目标：标准SQL校验100%、护栏危险拒绝100%、合法放行100%、模型问数结果准确率至少85%、多轮全部通过。这里的85%是项目建议目标，不是行业标准，也不是已达到的成绩。实际验收按业务重要性收紧；不要用能执行替代答对。

## 实验与报告

先对 dev 调整提示词/指标，再冻结版本对 holdout 评测。模型模式建议重复3次，每次保存独立报告，报告均值及范围，同时记录模型版本、温度（当前客户端默认0）、代码版本、schema模式、数据哈希和题集哈希。使用相同题集、数据、配置才能比较。A/B应分别报分，再汇总，避免掩盖某个域失败。

报告建议填写：模型及版本；92题执行成功率/结果一致率；各业务域、难度、类别准确率；6轮逐轮及整会话结果；危险拒绝9/9与合法放行3/3；降级比例；P50/P95；人工验收12项结果；最常见3类错误与对应题号。未经实际模型运行的项写“未测”。Token与费用当前客户端未暴露，报告不虚构。

所有数据库通过 SQLite `mode=ro` 和 `query_only` 连接，攻击SQL只交给护栏，不执行；每条实际SQL设15秒VM进度中断。报告包含原始SQL、标准结果、实际响应与轨迹、数据SHA256，不包含模型密钥。

边界：本套是项目定制的合成SQLite评测，未验证MySQL/PostgreSQL等真实方言、数据库权限、生产数据分布、前端交互和并发性能。Gold执行通过不证明业务口径绝对正确，正式验收应由领域人员抽查SQL。空结果题尤其检查是否被当前流水线的“自动放宽”改掉明确过滤条件。人工项不得并入已自动通过的数字。
