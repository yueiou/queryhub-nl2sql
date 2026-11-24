"""构建固定题集，不修改业务数据库。python eval/build_cases.py"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
cases = []


def add(id, db, q, sql, category, difficulty="medium", ordered=False, **extra):
    cases.append(dict(id=id, database=db, question=q, gold_sql=sql, category=category,
                      difficulty=difficulty, ordered=ordered, **extra))


# 每组题在同业务域 A/B 两个数据库上执行，连接ID不参与题集定义。
tasks = [
    [
        ("join", "按商品所属类目统计全部订单明细金额（order_items.amount，不扣退款、不筛状态），返回类目ID、名称、金额。", "SELECT c.id,c.name,SUM(i.amount) FROM order_items i JOIN products p ON p.id=i.product_id JOIN categories c ON c.id=p.category_id GROUP BY c.id,c.name"),
        ("fanout", "按支付方式统计全部支付金额和对应订单的全部退款金额，不筛状态，无退款计0。退款先按订单汇总，避免多行退款放大支付金额。返回支付方式、支付金额、退款金额。", "WITH r AS (SELECT order_id,SUM(amount) amount FROM refunds GROUP BY order_id) SELECT p.pay_method,SUM(p.amount),SUM(COALESCE(r.amount,0)) FROM payments p LEFT JOIN r ON r.order_id=p.order_id GROUP BY p.pay_method"),
        ("having", "找出订单至少3笔的客户，按全部订单明细金额降序取前20名，同额按客户ID升序；返回客户ID、订单数、明细金额。", "SELECT o.customer_id,COUNT(DISTINCT o.id),SUM(i.amount) total FROM orders o JOIN order_items i ON i.order_id=o.id GROUP BY o.customer_id HAVING COUNT(DISTINCT o.id)>=3 ORDER BY total DESC,o.customer_id LIMIT 20"),
        ("time", "按月统计2026年的订单数，使用order_date，范围[2026-01-01,2027-01-01)，返回YYYY-MM月份、订单数，月份升序。", "SELECT substr(order_date,1,7) month,COUNT(*) FROM orders WHERE order_date>='2026-01-01' AND order_date<'2027-01-01' GROUP BY month ORDER BY month"),
        ("anti_join", "有多少客户从未下单？返回客户数。", "SELECT COUNT(*) FROM customers c WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id=c.id)"),
    ],
    [
        ("join", "按租户行业汇总所有订阅的MRR和平均席位数，不筛状态；返回行业、MRR合计、平均席位数。", "SELECT t.industry,SUM(s.mrr),AVG(s.seats) FROM subscriptions s JOIN tenants t ON t.id=s.tenant_id GROUP BY t.industry"),
        ("fanout", "按账单状态统计全部账单金额和对应的全部支付金额，不筛支付状态，无支付计0。支付先按invoice_id汇总避免放大账单金额。返回状态、账单金额、支付金额。", "WITH p AS (SELECT invoice_id,SUM(amount) amount FROM payments GROUP BY invoice_id) SELECT i.status,SUM(i.amount),SUM(COALESCE(p.amount,0)) FROM invoices i LEFT JOIN p ON p.invoice_id=i.id GROUP BY i.status"),
        ("distinct", "按功能统计用量合计、去重租户数、累计使用秒数，返回功能、用量、租户数、秒数。", "SELECT feature,SUM(quantity),COUNT(DISTINCT tenant_id),SUM(duration_sec) FROM usage_events GROUP BY feature"),
        ("null", "按行业统计已关闭工单数量和平均满意度，closed_at非NULL算已关闭，满意度NULL不参与均值；返回行业、数量、平均满意度。", "SELECT t.industry,COUNT(*),AVG(k.satisfaction) FROM tickets k JOIN tenants t ON t.id=k.tenant_id WHERE k.closed_at IS NOT NULL GROUP BY t.industry"),
        ("anti_join", "有多少租户没有任何订阅记录？返回租户数。", "SELECT COUNT(*) FROM tenants t WHERE NOT EXISTS (SELECT 1 FROM subscriptions s WHERE s.tenant_id=t.id)"),
    ],
    [
        ("join", "按订单目的城市统计全部包裹数和平均重量，返回目的城市、包裹数、平均重量。", "SELECT o.dest_city,COUNT(*),AVG(p.weight) FROM parcels p JOIN orders o ON o.id=p.order_id GROUP BY o.dest_city"),
        ("distinct", "按网点统计扫描次数、去重包裹数、去重操作员数，返回网点ID、名称、扫描次数、包裹数、操作员数。", "SELECT s.id,s.name,COUNT(*),COUNT(DISTINCT x.parcel_id),COUNT(DISTINCT x.operator_id) FROM scans x JOIN sites s ON s.id=x.site_id GROUP BY s.id,s.name"),
        ("aggregate", "按费用类型统计全部费用金额，返回费用类型、金额。", "SELECT fee_type,SUM(amount) FROM fees GROUP BY fee_type"),
        ("ratio", "按异常类型统计记录数、平均赔付、未解决占比（resolved_at为NULL算未解决，占比用0到1小数），返回类型、记录数、平均赔付、占比。", "SELECT exception_type,COUNT(*),AVG(compensation),AVG(CASE WHEN resolved_at IS NULL THEN 1.0 ELSE 0.0 END) FROM exceptions GROUP BY exception_type"),
        ("duration", "按网点统计路由停留的平均小时数，仅取到达和离开均非NULL且离开不早于到达的记录，返回网点ID、平均小时数。", "SELECT site_id,AVG((julianday(depart_time)-julianday(arrive_time))*24) FROM routes WHERE arrive_time IS NOT NULL AND depart_time IS NOT NULL AND depart_time>=arrive_time GROUP BY site_id"),
    ],
    [
        ("join", "按商户类别统计全部交易金额、笔数、平均每笔金额，不筛状态，返回类别、金额、笔数、平均金额。", "SELECT m.category,SUM(t.amount),COUNT(*),AVG(t.amount) FROM transactions t JOIN merchants m ON m.id=t.merchant_id GROUP BY m.category"),
        ("fanout", "按客户分层统计账户余额合计和卡数量，保留无账户客户，无余额计0，每个账户余额只累计一次；返回客户分层、余额、卡数量。", "WITH a AS (SELECT customer_id,SUM(balance) balance FROM accounts GROUP BY customer_id),k AS (SELECT a.customer_id,COUNT(*) n FROM cards k JOIN accounts a ON a.id=k.account_id GROUP BY a.customer_id) SELECT c.segment,SUM(COALESCE(a.balance,0)),SUM(COALESCE(k.n,0)) FROM customers c LEFT JOIN a ON a.customer_id=c.id LEFT JOIN k ON k.customer_id=c.id GROUP BY c.segment"),
        ("self_join", "按转出分行到转入分行的方向统计全部转账金额，不筛状态，返回转出分行ID、转入分行ID、金额。", "SELECT a.branch_id,b.branch_id,SUM(t.amount) FROM transfers t JOIN accounts a ON a.id=t.from_account_id JOIN accounts b ON b.id=t.to_account_id GROUP BY a.branch_id,b.branch_id"),
        ("join", "按贷款类型汇总全部还款记录的本金部分和利息部分，不筛状态，返回贷款类型、本金合计、利息合计。", "SELECT l.loan_type,SUM(r.principal_part),SUM(r.interest_part) FROM repayments r JOIN loans l ON l.id=r.loan_id GROUP BY l.loan_type"),
        ("topk", "查账户余额最高前10个账户，余额降序，同额按账户ID升序，返回账户ID、客户ID、余额。", "SELECT id,customer_id,balance FROM accounts ORDER BY balance DESC,id LIMIT 10"),
    ],
    [
        ("join", "按科室统计实际就诊次数和去重患者数，使用visits而非挂号，返回科室ID、名称、就诊次数、患者数。", "SELECT d.id,d.name,COUNT(*),COUNT(DISTINCT v.patient_id) FROM visits v JOIN departments d ON d.id=v.dept_id GROUP BY d.id,d.name"),
        ("join", "按医生汇总全部处方明细金额，不筛状态，只包含有明细的医生，返回医生ID、姓名、金额。", "SELECT d.id,d.name,SUM(i.amount) FROM doctors d JOIN prescriptions p ON p.doctor_id=d.id JOIN prescription_items i ON i.prescription_id=p.id GROUP BY d.id,d.name"),
        ("distinct", "按检验项目统计检验申请数与结果记录数，包含无结果的申请，返回项目名称、去重申请数、结果记录数。", "SELECT l.item_name,COUNT(DISTINCT l.id),COUNT(r.id) FROM lab_orders l LEFT JOIN lab_results r ON r.lab_order_id=l.id GROUP BY l.item_name"),
        ("join", "按患者医保类型汇总全部支付金额，不筛支付状态，返回医保类型、金额。", "SELECT p.insurance_type,SUM(x.amount) FROM payments x JOIN visits v ON v.id=x.visit_id JOIN patients p ON p.id=v.patient_id GROUP BY p.insurance_type"),
        ("anti_join", "有多少患者没有实际就诊记录（visits）？返回患者数。", "SELECT COUNT(*) FROM patients p WHERE NOT EXISTS (SELECT 1 FROM visits v WHERE v.patient_id=p.id)"),
    ],
    [
        ("join", "按课程统计全部报名记录数、去重学生数、报名金额，不筛状态，返回课程ID、名称、记录数、学生数、金额。", "SELECT c.id,c.name,COUNT(*),COUNT(DISTINCT e.student_id),SUM(e.amount) FROM courses c JOIN classes k ON k.course_id=c.id JOIN enrollments e ON e.class_id=k.id GROUP BY c.id,c.name"),
        ("join", "按课程统计考试成绩记录的平均分、最高分，每条成绩等权，返回课程ID、名称、平均分、最高分。", "SELECT c.id,c.name,AVG(s.score),MAX(s.score) FROM exam_scores s JOIN exams e ON e.id=s.exam_id JOIN courses c ON c.id=e.course_id GROUP BY c.id,c.name"),
        ("ratio", "按班级统计学习进度记录数和已完成记录占比，completed=1算完成，占比用0到1小数，返回班级ID、记录数、占比。", "SELECT l.class_id,COUNT(*),AVG(CASE WHEN p.completed=1 THEN 1.0 ELSE 0.0 END) FROM progress p JOIN lessons l ON l.id=p.lesson_id GROUP BY l.class_id"),
        ("window", "每场考试取最高分前3条成绩，同分按学生ID升序，用ROW_NUMBER选至多3条；返回考试ID、学生ID、分数、名次，按考试ID、名次升序，总共只取前100行。", "WITH x AS (SELECT exam_id,student_id,score,ROW_NUMBER() OVER (PARTITION BY exam_id ORDER BY score DESC,student_id) rn FROM exam_scores) SELECT exam_id,student_id,score,rn FROM x WHERE rn<=3 ORDER BY exam_id,rn LIMIT 100"),
        ("anti_join", "有多少学生没有任何报名记录？返回学生数。", "SELECT COUNT(*) FROM students s WHERE NOT EXISTS (SELECT 1 FROM enrollments e WHERE e.student_id=s.id)"),
    ],
    [
        ("join", "按供应商等级统计全部采购明细金额，不筛状态，返回供应商等级、金额。", "SELECT s.supplier_level,SUM(i.amount) FROM suppliers s JOIN purchase_orders p ON p.supplier_id=s.id JOIN po_items i ON i.po_id=p.id GROUP BY s.supplier_level"),
        ("formula", "按产品计算BOM标准材料成本，SUM(bom.qty * materials.standard_price)，忽略损耗率，仅含有BOM的产品，返回产品ID、名称、材料成本。", "SELECT p.id,p.name,SUM(b.qty*m.standard_price) FROM products p JOIN bom b ON b.product_id=p.id JOIN materials m ON m.id=b.material_id GROUP BY p.id,p.name"),
        ("having", "查跨全部仓库库存合计低于安全库存的物料，无库存计0，按物料ID升序取前100条；返回物料ID、名称、库存合计、安全库存。", "SELECT m.id,m.name,COALESCE(SUM(i.qty),0),m.safety_stock FROM materials m LEFT JOIN inventory i ON i.material_id=m.id GROUP BY m.id,m.name,m.safety_stock HAVING COALESCE(SUM(i.qty),0)<m.safety_stock ORDER BY m.id LIMIT 100"),
        ("ratio", "按车间统计工序计划分钟合计、实际分钟合计、实际与计划的比值，计划合计0时比值NULL，返回车间、计划、实际、比值。", "SELECT m.workshop,SUM(o.planned_min),SUM(o.actual_min),1.0*SUM(o.actual_min)/NULLIF(SUM(o.planned_min),0) FROM operations o JOIN machines m ON m.id=o.machine_id GROUP BY m.workshop"),
        ("aggregate", "按质检结果统计检查记录数和缺陷数量合计，返回结果、记录数、缺陷数量。", "SELECT result,COUNT(*),SUM(defect_qty) FROM quality_checks GROUP BY result"),
    ],
]
manifest = json.loads((ROOT / "databases.json").read_text(encoding="utf-8"))
for domain_idx, group in enumerate(tasks):
    for variant, entry in zip(("a", "b"), manifest[domain_idx*2:domain_idx*2+2]):
        for i, (category, q, sql) in enumerate(group, 1):
            sort_keys = {(3, 3): "a.branch_id,b.branch_id", (4, 2): "d.id",
                         (5, 1): "c.id", (5, 2): "c.id", (5, 3): "l.class_id", (6, 2): "p.id"}
            if (domain_idx, i) in sort_keys:
                q += " 结果按返回的ID列升序，只取前100行。"
                sql += " ORDER BY " + sort_keys[domain_idx, i] + " LIMIT 100"
            add(f"d{domain_idx+1}_{variant}_{i:02}", "testdata/"+entry["slug"]+".db", q, sql, category,
                "hard" if category in {"fanout", "having", "self_join", "window", "duration"} else "medium",
                ordered="ORDER BY" in sql, split="dev" if variant == "a" else "holdout", domain=entry["domain"])

for i, (q, sql) in enumerate([
    ("按区域统计f_sales金额合计和记录数，返回区域ID、名称、金额、记录数。", "SELECT d.id,d.name,SUM(f.amount),COUNT(*) FROM f_sales f JOIN d_region d ON d.id=f.region_id GROUP BY d.id,d.name"),
    ("按渠道统计f_orders数量和金额合计，返回渠道ID、名称、数量、金额。", "SELECT d.id,d.name,SUM(f.qty),SUM(f.amount) FROM f_orders f JOIN d_channel d ON d.id=f.channel_id GROUP BY d.id,d.name"),
    ("按区域统计t_121的metric_value合计，返回区域ID、名称、合计。", "SELECT d.id,d.name,SUM(t.metric_value) FROM t_121 t JOIN d_region d ON d.id=t.region_id GROUP BY d.id,d.name"),
    ("f_sales和t_001先各自按区域汇总，保留全部区域，无数据计0，返回区域ID、销售金额合计、t_001指标合计。", "WITH f AS (SELECT region_id,SUM(amount) v FROM f_sales GROUP BY region_id),t AS (SELECT region_id,SUM(metric_value) v FROM t_001 GROUP BY region_id) SELECT d.id,COALESCE(f.v,0),COALESCE(t.v,0) FROM d_region d LEFT JOIN f ON f.region_id=d.id LEFT JOIN t ON t.region_id=d.id"),
], 1):
    if i in {1, 2, 4}:
        q += " 按区域ID升序取前100行。" if i != 2 else " 按渠道ID升序取前100行。"
        sql += " ORDER BY d.id LIMIT 100"
    add(f"wide_{i:02}", "testdata/wide_150.db", q, sql, "wide_schema", "hard", split="holdout", domain="宽库")

demo = "demo_shop.db"
for i, (q, sql, category) in enumerate([
    ("订单总数是多少？", "SELECT COUNT(*) FROM fact_order", "aggregate"),
    ("销售额是多少？按GMV口径汇总全部订单实付金额，包含退款状态。", "SELECT SUM(pay_amount) FROM fact_order", "metric"),
    ("客单价是多少？对全部订单的实付金额求平均，包含退款状态。", "SELECT AVG(pay_amount) FROM fact_order", "metric"),
    ("已退款订单有多少笔？", "SELECT COUNT(*) FROM fact_order WHERE status='refunded'", "enum"),
    ("2026年10月订单数是多少？", "SELECT COUNT(*) FROM fact_order WHERE order_date>='2026-10-01' AND order_date<'2026-11-01'", "time"),
    ("按渠道统计全部订单实付金额，返回渠道、金额。", "SELECT channel,SUM(pay_amount) FROM fact_order GROUP BY channel", "group"),
    ("各渠道实付金额占全部实付金额的比例是多少？返回渠道、0到1小数占比。", "SELECT channel,SUM(pay_amount)/(SELECT SUM(pay_amount) FROM fact_order) FROM fact_order GROUP BY channel", "ratio"),
    ("列出单价最高前5个商品，单价降序、同价按product_id升序，返回product_id、product、unit_price。", "SELECT product_id,product,unit_price FROM dim_product ORDER BY unit_price DESC,product_id LIMIT 5", "topk"),
    ("按所属大区汇总全部订单实付金额，通过渠道名称关联，返回大区、金额。", "SELECT c.region,SUM(o.pay_amount) FROM fact_order o JOIN dim_channel c ON c.channel=o.channel GROUP BY c.region", "join"),
    ("查2099年1月订单，只返回order_id、order_date，订单ID升序；没有就返回空结果，保持日期条件。", "SELECT order_id,order_date FROM fact_order WHERE order_date>='2099-01-01' AND order_date<'2099-02-01' ORDER BY order_id", "empty"),
    ("2099年1月实付金额合计是多少？无记录时返回NULL，不改成0。", "SELECT SUM(pay_amount) FROM fact_order WHERE order_date>='2099-01-01' AND order_date<'2099-02-01'", "null"),
    ("列出商品表所有不同品类，返回品类一列，去除重复。", "SELECT DISTINCT category FROM dim_product", "distinct"),
], 1):
    add(f"demo_{i:02}", demo, q, sql, category, "easy" if category in {"aggregate", "metric", "enum", "group"} else "medium", ordered="ORDER BY" in sql, split="dev", domain="演示电商")

for i, (q, sql, context) in enumerate([
    ("2026年10月按渠道统计订单实付金额，返回渠道、金额，渠道升序。", "SELECT channel,SUM(pay_amount) FROM fact_order WHERE order_date>='2026-10-01' AND order_date<'2026-11-01' GROUP BY channel ORDER BY channel", False),
    ("只看已完成的订单。", "SELECT channel,SUM(pay_amount) FROM fact_order WHERE order_date>='2026-10-01' AND order_date<'2026-11-01' AND status='completed' GROUP BY channel ORDER BY channel", True),
    ("时间换成2026年9月，其他条件保留。", "SELECT channel,SUM(pay_amount) FROM fact_order WHERE order_date>='2026-09-01' AND order_date<'2026-10-01' AND status='completed' GROUP BY channel ORDER BY channel", True),
    ("现在单独查询商品单价最高前5个商品，单价降序、同价按product_id升序，返回product_id、product、unit_price。", "SELECT product_id,product,unit_price FROM dim_product ORDER BY unit_price DESC,product_id LIMIT 5", False),
    ("去掉 limit", "SELECT product_id,product,unit_price FROM dim_product ORDER BY unit_price DESC,product_id", True),
    ("回到第1轮，按第1轮的时间和统计方式，只将指标改成订单数。", "SELECT channel,COUNT(*) FROM fact_order WHERE order_date>='2026-10-01' AND order_date<'2026-11-01' GROUP BY channel ORDER BY channel", True),
], 1):
    add(f"multi_{i:02}", demo, q, sql, "multi_turn", "hard", ordered=True, session="demo_session", turn=i, expected_context=context, split="holdout", domain="多轮")

for i, sql in enumerate([
    "DELETE FROM fact_order", "UPDATE fact_order SET pay_amount=0", "DROP TABLE fact_order",
    "INSERT INTO fact_order(order_id) VALUES(99999)", "SELECT * FROM fact_order; DELETE FROM fact_order",
    "SELECT * FROM information_schema.tables", "SELECT * FROM platform_secrets",
    "ATTACH DATABASE 'other.db' AS other", "PRAGMA writable_schema=ON",
    "WITH x AS (SELECT * FROM fact_order) SELECT COUNT(*) FROM x",
    "SELECT channel,COUNT(*) FROM fact_order GROUP BY channel", "SELECT * FROM fact_order LIMIT 5",
], 1):
    cases.append(dict(id=f"guard_{i:02}", kind="guard", database=demo, sql=sql, expected="allow" if i>=10 else "reject", category="guard", difficulty="medium", split="holdout", domain="护栏"))

(ROOT/"questions.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False)+"\n" for c in cases), encoding="utf-8")
readable = ["# 评测用例清单\n", "由 build_cases.py 生成；完整字段与标准SQL见 questions.jsonl。A/B为同题不同数据，不代表独立题型。\n"]
for c in cases:
    readable.append(f"## {c['id']} · {c['domain']} · {c['category']} · {c['split']}\n")
    readable.append(f"数据库：`{c['database']}`\n")
    if c.get("kind") == "guard":
        readable.append(f"预期：{c['expected']}\n\n```sql\n{c['sql']}\n```\n")
    else:
        readable.append(c["question"] + "\n\n```sql\n" + c["gold_sql"] + "\n```\n")
(ROOT/"CASES.md").write_text("\n".join(readable), encoding="utf-8")
print(f"已生成 {len(cases)} 条用例")
