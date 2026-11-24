"""生成一套用于 QueryHub 终测的多业务 SQLite 测试库。

特点：
- 每个业务域生成 2 个独立库（种子不同、数据不同），覆盖 7 个域，另加 1 个 150 表宽库；
- 事实表不冗余总额，金额/状态/时间拆在多张表里，必须多表 JOIN 才能查全；
- 表之间用外键串联，覆盖 3~5 层链路、桥接表、自连接、反连接、窗口函数场景；
- 每张表写 `_schema_notes` 中文注释，平台自省后模型能看懂业务含义。

运行：python scripts/generate_testdata.py
生成后自动注册到平台；重复运行会覆盖同名 DB 并刷新 schema 目录。
"""
import argparse
import json
import random
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import config
from app.db import engine as engine_factory
from app.db.introspect import introspect
from app.db.metadata import MetadataStore

OUT_DIR = Path(config.DATA_DIR) / "testdata"
MANIFEST = OUT_DIR / "manifest.json"
BASE = date(2025, 1, 1)
END = date(2026, 11, 20)
DAYS = (END - BASE).days

CITY = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "南京", "西安", "苏州"]
REGION = ["华北", "华东", "华南", "西南", "华中", "西北", "东北"]
CHANNEL = ["APP", "小程序", "网页", "线下", "电话"]
LEVEL = ["普通", "银卡", "金卡", "钻石"]
STATUS = ["完成", "处理中", "取消", "异常"]


def d(offset=None):
    return (BASE + timedelta(days=random.randint(0, DAYS) if offset is None else offset)).isoformat()


def ts():
    day = BASE + timedelta(days=random.randint(0, DAYS))
    return (datetime.combine(day, datetime.min.time()) + timedelta(seconds=random.randint(0, 86399))).strftime("%Y-%m-%d %H:%M:%S")


def money(lo=10, hi=5000, nd=2):
    return round(random.uniform(lo, hi), nd)


def pct():
    return round(random.uniform(0, 1), 3)


class Build:
    def __init__(self, path, seed, scale=1.0):
        self.path = Path(path)
        self.rng = random.Random(seed)
        random.seed(seed)
        self.scale = scale
        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=OFF")
        self.conn.execute("PRAGMA synchronous=OFF")
        self.conn.execute("PRAGMA cache_size=-64000")
        self.stats = {}
        self.notes = []

    def n(self, value):
        return max(1, int(value * self.scale))

    def table(self, name, columns, rows, indexes=(), note=""):
        """columns: [(col, ddl)]，ddl 里直接写 REFERENCES 外键。"""
        cols = ", ".join(f"{c} {ddl}" for c, ddl in columns)
        self.conn.execute(f"CREATE TABLE {name}({cols})")
        names = [c for c, _ in columns]
        values = ", ".join("?" for _ in names)
        self.conn.executemany(f"INSERT INTO {name}({', '.join(names)}) VALUES({values})", rows)
        for idx, col in enumerate(indexes):
            self.conn.execute(f"CREATE INDEX idx_{name}_{col} ON {name}({col})")
        self.stats[name] = len(rows)
        if note:
            self.notes.append((name, "", note))

    def finish(self):
        if self.notes:
            self.conn.execute(
                "CREATE TABLE _schema_notes(table_name TEXT, column_name TEXT, note TEXT)"
            )
            self.conn.executemany(
                "INSERT INTO _schema_notes(table_name,column_name,note) VALUES(?,?,?)",
                self.notes,
            )
        self.conn.commit()
        self.conn.execute("ANALYZE")
        self.conn.commit()
        self.conn.close()
        return self.stats


# ---------------------------------------------------------------------------
# 1. 电商零售：订单 / 明细 / 支付 / 退款 / 发货 / 评价拆表
# ---------------------------------------------------------------------------
def build_ecommerce(path, seed, scale=1.0):
    b = Build(path, seed, scale)
    nc, ncat, nprod = b.n(8000), b.n(40), b.n(3000)
    norder = b.n(30000)
    npromo = b.n(120)

    customers = [(i, f"客户{i:06d}", random.choice(["男", "女"]), random.choice(CITY),
                  d(), random.choice(LEVEL), random.choice(["新客", "复购", "高价值", "流失"]))
                 for i in range(1, nc + 1)]
    b.table("customers", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("gender", "TEXT"), ("city", "TEXT"),
        ("register_date", "TEXT"), ("member_level", "TEXT"), ("segment", "TEXT"),
    ], customers, ("city", "member_level", "register_date"), "客户主数据，含注册城市、会员等级和客群分层")

    cats = []
    for i in range(1, ncat + 1):
        parent = random.randint(1, max(1, i - 1)) if i > 1 and random.random() < 0.5 else None
        cats.append((i, f"品类{i:02d}", parent))
    b.table("categories", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"),
        ("parent_id", "INTEGER REFERENCES categories(id)"),
    ], cats, ("parent_id",), "商品类目树，parent_id 自关联，可查一级/二级类目")

    products = [(i, f"SKU{i:06d}", f"商品{i:05d}", random.randint(1, ncat),
                 f"品牌{random.randint(1, 50):02d}", money(20, 3000), money(10, 1500),
                 d(), random.choice(["在售", "下架", "缺货"]))
                for i in range(1, nprod + 1)]
    b.table("products", [
        ("id", "INTEGER PRIMARY KEY"), ("sku", "TEXT"), ("name", "TEXT"),
        ("category_id", "INTEGER REFERENCES categories(id)"), ("brand", "TEXT"),
        ("list_price", "REAL"), ("cost", "REAL"), ("launch_date", "TEXT"), ("status", "TEXT"),
    ], products, ("category_id", "brand", "status"), "商品主数据；类目在 categories，价格和成本分列")

    promos = [(i, f"活动{i:03d}", random.choice(["满减", "折扣", "赠品", "秒杀"]),
               d(), d(), random.choice(CHANNEL), pct() * 0.5)
              for i in range(1, npromo + 1)]
    b.table("promotions", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("promo_type", "TEXT"),
        ("start_date", "TEXT"), ("end_date", "TEXT"), ("channel", "TEXT"), ("discount_rate", "REAL"),
    ], promos, ("promo_type", "channel"), "促销活动；订单只存 promotion_id，优惠力度需回查本表")

    orders = [(i, random.randint(1, nc), d(), random.choice(STATUS), random.choice(CHANNEL),
               random.randint(1, npromo) if random.random() < 0.35 else None, random.choice(CITY))
              for i in range(1, norder + 1)]
    b.table("orders", [
        ("id", "INTEGER PRIMARY KEY"), ("customer_id", "INTEGER REFERENCES customers(id)"),
        ("order_date", "TEXT"), ("status", "TEXT"), ("channel", "TEXT"),
        ("promotion_id", "INTEGER REFERENCES promotions(id)"), ("city", "TEXT"),
    ], orders, ("customer_id", "order_date", "status", "channel", "promotion_id"),
        "订单主表，不存订单总额；金额必须到 order_items 汇总，支付状态在 payments")

    items = []
    item_ids = {}
    cur_id = 1
    for oid in range(1, norder + 1):
        ids = []
        for _ in range(random.randint(1, 4)):
            pid = random.randint(1, nprod)
            qty = random.randint(1, 5)
            unit = money(20, 3000)
            items.append((cur_id, oid, pid, qty, unit, round(qty * unit, 2)))
            ids.append(cur_id)
            cur_id += 1
        item_ids[oid] = ids
    b.table("order_items", [
        ("id", "INTEGER PRIMARY KEY"), ("order_id", "INTEGER REFERENCES orders(id)"),
        ("product_id", "INTEGER REFERENCES products(id)"), ("qty", "INTEGER"),
        ("unit_price", "REAL"), ("amount", "REAL"),
    ], items, ("order_id", "product_id"), "订单明细；订单销售额/商品销量都从这里汇总")

    payments = [(i, i, random.choice(["支付宝", "微信", "银行卡", "余额"]), ts(),
                 money(50, 8000), random.choice(["成功", "失败", "退款"]))
                for i in range(1, norder + 1)]
    b.table("payments", [
        ("id", "INTEGER PRIMARY KEY"), ("order_id", "INTEGER REFERENCES orders(id)"),
        ("pay_method", "TEXT"), ("pay_time", "TEXT"), ("amount", "REAL"), ("status", "TEXT"),
    ], payments, ("order_id", "pay_time", "status"), "支付流水；与订单金额不保证相等，用于对账类问题")

    refunds = []
    for i in range(1, b.n(2500) + 1):
        oid = random.randint(1, norder)
        refunds.append((i, oid, random.choice(item_ids[oid]), d(), money(10, 1000),
                        random.choice(["质量问题", "七天无理由", "发错货", "价格保护"]),
                        random.choice(["已退款", "处理中", "驳回"])))
    b.table("refunds", [
        ("id", "INTEGER PRIMARY KEY"), ("order_id", "INTEGER REFERENCES orders(id)"),
        ("item_id", "INTEGER REFERENCES order_items(id)"), ("refund_date", "TEXT"),
        ("amount", "REAL"), ("reason", "TEXT"), ("status", "TEXT"),
    ], refunds, ("order_id", "item_id", "refund_date", "status"),
        "退款单；同时关联订单和明细，可算单品退款率")

    shipments = [(i, i, f"仓库{random.randint(1, 12):02d}", d(), random.choice(["顺丰", "京东", "中通", "圆通"]),
                  f"SF{random.randint(10**10, 10**11 - 1)}", random.choice(["已签收", "运输中", "异常"]))
                 for i in range(1, b.n(24000) + 1)]
    b.table("shipments", [
        ("id", "INTEGER PRIMARY KEY"), ("order_id", "INTEGER REFERENCES orders(id)"),
        ("warehouse", "TEXT"), ("ship_date", "TEXT"), ("carrier", "TEXT"),
        ("tracking_no", "TEXT"), ("status", "TEXT"),
    ], shipments, ("order_id", "ship_date", "carrier", "status"),
        "发货单；查履约时效需要 orders + shipments")

    reviews = [(i, random.randint(1, norder), random.randint(1, nprod), random.choice([1, 2, 3, 4, 5]),
                random.randint(5, 500), d())
               for i in range(1, b.n(10000) + 1)]
    b.table("reviews", [
        ("id", "INTEGER PRIMARY KEY"), ("order_id", "INTEGER REFERENCES orders(id)"),
        ("product_id", "INTEGER REFERENCES products(id)"), ("rating", "INTEGER"),
        ("content_len", "INTEGER"), ("review_date", "TEXT"),
    ], reviews, ("order_id", "product_id", "rating"), "商品评价；评分和订单/商品关联")
    return b.finish()


# ---------------------------------------------------------------------------
# 2. SaaS 订阅：租户 / 订阅 / 账单 / 支付 / 用量 / 工单拆表
# ---------------------------------------------------------------------------
def build_saas(path, seed, scale=1.0):
    b = Build(path, seed, scale)
    ntenant, nuser, nsub = b.n(5000), b.n(20000), b.n(8000)
    ninv, nusage, nticket = b.n(25000), b.n(80000), b.n(8000)

    tenants = [(i, f"租户{i:05d}", random.choice(["制造", "零售", "金融", "教育", "医疗", "互联网"]),
                random.choice(REGION), d(), random.choice(["活跃", "试用", "流失", "暂停"]))
               for i in range(1, ntenant + 1)]
    b.table("tenants", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("industry", "TEXT"),
        ("region", "TEXT"), ("signup_date", "TEXT"), ("status", "TEXT"),
    ], tenants, ("industry", "region", "status"), "租户主数据；行业/区域用于分层经营分析")

    plans = [(i, f"套餐{i}", money(50, 500), money(10, 80), random.choice(["基础", "专业", "旗舰"]))
             for i in range(1, 13)]
    b.table("plans", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("monthly_price", "REAL"),
        ("seat_price", "REAL"), ("feature_level", "TEXT"),
    ], plans, ("feature_level",), "订阅套餐；月费和席位单价分开，MRR 需结合 subscriptions.seats")

    users = [(i, random.randint(1, ntenant), f"用户{i:05d}", random.choice(["管理员", "成员", "只读"]),
              ts(), random.choice([1, 0]))
             for i in range(1, nuser + 1)]
    b.table("users", [
        ("id", "INTEGER PRIMARY KEY"), ("tenant_id", "INTEGER REFERENCES tenants(id)"),
        ("name", "TEXT"), ("role", "TEXT"), ("created_at", "TEXT"), ("active", "INTEGER"),
    ], users, ("tenant_id", "role", "active"), "租户下的用户；活跃用户数需按 tenant_id 统计")

    subs = [(i, random.randint(1, ntenant), random.randint(1, 12), d(),
             d(), random.choice(["生效", "到期", "取消"]), random.randint(5, 500),
             money(100, 20000))
            for i in range(1, nsub + 1)]
    b.table("subscriptions", [
        ("id", "INTEGER PRIMARY KEY"), ("tenant_id", "INTEGER REFERENCES tenants(id)"),
        ("plan_id", "INTEGER REFERENCES plans(id)"), ("start_date", "TEXT"), ("end_date", "TEXT"),
        ("status", "TEXT"), ("seats", "INTEGER"), ("mrr", "REAL"),
    ], subs, ("tenant_id", "plan_id", "status", "start_date"),
        "订阅记录；MRR、席位、套餐需和 tenants/plans 联查")

    invoices = [(i, random.randint(1, nsub), random.randint(1, ntenant), d(), d(),
                 money(100, 30000), random.choice(["已支付", "待支付", "逾期", "作废"]))
                for i in range(1, ninv + 1)]
    b.table("invoices", [
        ("id", "INTEGER PRIMARY KEY"), ("subscription_id", "INTEGER REFERENCES subscriptions(id)"),
        ("tenant_id", "INTEGER REFERENCES tenants(id)"), ("invoice_date", "TEXT"),
        ("due_date", "TEXT"), ("amount", "REAL"), ("status", "TEXT"),
    ], invoices, ("subscription_id", "tenant_id", "invoice_date", "status"),
        "账单；账单金额和支付流水分开，欠费/回款率需要对 payments")

    inv_items = []
    cur = 1
    for inv_id in range(1, ninv + 1):
        for _ in range(random.randint(1, 2)):
            qty = random.randint(1, 100)
            unit = money(10, 500)
            inv_items.append((cur, inv_id, random.choice(["订阅费", "席位费", "用量费", "服务费"]),
                              qty, unit, round(qty * unit, 2)))
            cur += 1
    b.table("invoice_items", [
        ("id", "INTEGER PRIMARY KEY"), ("invoice_id", "INTEGER REFERENCES invoices(id)"),
        ("item_type", "TEXT"), ("qty", "INTEGER"), ("unit_price", "REAL"), ("amount", "REAL"),
    ], inv_items, ("invoice_id", "item_type"), "账单明细；收入按产品线拆分必须查本表")

    pays = [(i, random.randint(1, ninv), d(), random.choice(["银行转账", "支付宝", "微信", "支票"]),
             money(100, 30000), random.choice(["成功", "失败", "退款"]))
            for i in range(1, b.n(22000) + 1)]
    b.table("payments", [
        ("id", "INTEGER PRIMARY KEY"), ("invoice_id", "INTEGER REFERENCES invoices(id)"),
        ("pay_date", "TEXT"), ("pay_method", "TEXT"), ("amount", "REAL"), ("status", "TEXT"),
    ], pays, ("invoice_id", "pay_date", "status"), "回款流水；账单是否结清要 invoices + payments")

    usage = [(i, random.randint(1, ntenant), d(), random.choice(["API", "报表", "存储", "工作流", "AI"]),
              random.randint(1, 10000), random.randint(1, 3600), random.randint(1, 200))
             for i in range(1, nusage + 1)]
    b.table("usage_events", [
        ("id", "INTEGER PRIMARY KEY"), ("tenant_id", "INTEGER REFERENCES tenants(id)"),
        ("event_date", "TEXT"), ("feature", "TEXT"), ("quantity", "INTEGER"),
        ("duration_sec", "INTEGER"), ("active_users", "INTEGER"),
    ], usage, ("tenant_id", "event_date", "feature"), "功能用量流水；用量收入和功能渗透率都从这里算")

    tickets = [(i, random.randint(1, ntenant), random.randint(1, nuser), ts(),
                random.choice(["低", "中", "高", "紧急"]), random.choice(["已关闭", "处理中", "待响应"]),
                ts(), random.randint(1, 5))
               for i in range(1, nticket + 1)]
    b.table("tickets", [
        ("id", "INTEGER PRIMARY KEY"), ("tenant_id", "INTEGER REFERENCES tenants(id)"),
        ("user_id", "INTEGER REFERENCES users(id)"), ("created_at", "TEXT"), ("priority", "TEXT"),
        ("status", "TEXT"), ("closed_at", "TEXT"), ("satisfaction", "INTEGER"),
    ], tickets, ("tenant_id", "user_id", "priority", "status"),
        "服务工单；满意度、响应时长要结合租户和用户")
    return b.finish()


# ---------------------------------------------------------------------------
# 3. 物流履约：订单 / 包裹 / 路由 / 扫描 / 费用 / 异常拆表
# ---------------------------------------------------------------------------
def build_logistics(path, seed, scale=1.0):
    b = Build(path, seed, scale)
    nsite, ncourier, ncust = b.n(200), b.n(3000), b.n(10000)
    norder, nparcel = b.n(30000), b.n(30000)
    nroute, nscan, nfee, nexc = b.n(80000), b.n(100000), b.n(30000), b.n(4000)

    sites = [(i, f"网点{i:04d}", random.choice(CITY), random.choice(["揽收", "中转", "派送", "综合"]),
              random.randint(50, 5000))
             for i in range(1, nsite + 1)]
    b.table("sites", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("city", "TEXT"),
        ("site_type", "TEXT"), ("capacity", "INTEGER"),
    ], sites, ("city", "site_type"), "物流网点；路由和扫描都落到网点")

    couriers = [(i, f"骑手{i:05d}", random.randint(1, nsite), f"13{random.randint(10**8, 10**9-1)}",
                 random.choice(["初级", "中级", "高级"]), random.choice([1, 0]))
                for i in range(1, ncourier + 1)]
    b.table("couriers", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("site_id", "INTEGER REFERENCES sites(id)"),
        ("phone", "TEXT"), ("level", "TEXT"), ("active", "INTEGER"),
    ], couriers, ("site_id", "level", "active"), "配送员；派送量按 courier/site 统计")

    customers = [(i, f"收件人{i:05d}", random.choice(CITY), f"地址{i}")
                 for i in range(1, ncust + 1)]
    b.table("customers", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("city", "TEXT"), ("address", "TEXT"),
    ], customers, ("city",), "寄件/收件客户")

    orders = [(i, random.randint(1, ncust), d(), random.randint(1, nsite), random.choice(CITY),
               random.choice(["已签收", "运输中", "已取消", "异常"]))
              for i in range(1, norder + 1)]
    b.table("orders", [
        ("id", "INTEGER PRIMARY KEY"), ("customer_id", "INTEGER REFERENCES customers(id)"),
        ("order_date", "TEXT"), ("origin_site_id", "INTEGER REFERENCES sites(id)"),
        ("dest_city", "TEXT"), ("status", "TEXT"),
    ], orders, ("customer_id", "order_date", "origin_site_id", "dest_city", "status"),
        "物流订单；目的城市和起始网点在本表，时效需结合 parcels/routes/scans")

    parcels = [(i, i, f"TRK{i:012d}", round(random.uniform(0.1, 30), 2),
                round(random.uniform(0.01, 2), 3), ts(), random.choice(["已签收", "运输中", "异常", "退回"]))
               for i in range(1, nparcel + 1)]
    b.table("parcels", [
        ("id", "INTEGER PRIMARY KEY"), ("order_id", "INTEGER REFERENCES orders(id)"),
        ("tracking_no", "TEXT"), ("weight", "REAL"), ("volume", "REAL"),
        ("created_at", "TEXT"), ("status", "TEXT"),
    ], parcels, ("order_id", "status", "created_at"), "包裹；一个订单可拆多个包裹，重量体积在本表")

    routes = []
    cur = 1
    for pid in range(1, nparcel + 1):
        for seq in range(1, random.randint(2, 5) + 1):
            routes.append((cur, pid, seq, random.randint(1, nsite), ts(), ts(),
                           random.choice(["揽收", "发车", "到达", "派送", "签收"])))
            cur += 1
    b.table("routes", [
        ("id", "INTEGER PRIMARY KEY"), ("parcel_id", "INTEGER REFERENCES parcels(id)"),
        ("seq", "INTEGER"), ("site_id", "INTEGER REFERENCES sites(id)"), ("arrive_time", "TEXT"),
        ("depart_time", "TEXT"), ("action", "TEXT"),
    ], routes, ("parcel_id", "site_id", "action"), "包裹路由；按 seq 还原轨迹，查时效需要多段路由")

    scans = [(i, random.randint(1, nparcel), random.randint(1, nsite), ts(),
              random.choice(["揽收", "到件", "发件", "派件", "签收"]), random.randint(1, ncourier))
             for i in range(1, nscan + 1)]
    b.table("scans", [
        ("id", "INTEGER PRIMARY KEY"), ("parcel_id", "INTEGER REFERENCES parcels(id)"),
        ("site_id", "INTEGER REFERENCES sites(id)"), ("scan_time", "TEXT"),
        ("scan_type", "TEXT"), ("operator_id", "INTEGER REFERENCES couriers(id)"),
    ], scans, ("parcel_id", "site_id", "scan_type", "scan_time"),
        "扫描流水；操作员、网点、类型拆开，考核需关联 couriers/sites")

    fees = [(i, random.randint(1, nparcel), random.choice(["基础运费", "续重费", "保价费", "派送费"]),
             money(1, 200), d())
            for i in range(1, nfee + 1)]
    b.table("fees", [
        ("id", "INTEGER PRIMARY KEY"), ("parcel_id", "INTEGER REFERENCES parcels(id)"),
        ("fee_type", "TEXT"), ("amount", "REAL"), ("billing_date", "TEXT"),
    ], fees, ("parcel_id", "fee_type", "billing_date"), "费用明细；收入需要按包裹汇总本表")

    exceptions = [(i, random.randint(1, nparcel), random.choice(["丢件", "破损", "延误", "拒收", "地址错误"]),
                   ts(), ts() if random.random() < 0.7 else None, money(0, 500))
                  for i in range(1, nexc + 1)]
    b.table("exceptions", [
        ("id", "INTEGER PRIMARY KEY"), ("parcel_id", "INTEGER REFERENCES parcels(id)"),
        ("exception_type", "TEXT"), ("created_at", "TEXT"), ("resolved_at", "TEXT"),
        ("compensation", "REAL"),
    ], exceptions, ("parcel_id", "exception_type", "created_at"),
        "异常件；赔付和未解决异常需要结合网点/路由分析")
    return b.finish()


# ---------------------------------------------------------------------------
# 4. 银行账户：客户 / 账户 / 卡 / 商户 / 交易 / 转账 / 贷款拆表
# ---------------------------------------------------------------------------
def build_bank(path, seed, scale=1.0):
    b = Build(path, seed, scale)
    nbranch, ncust, nacc = b.n(300), b.n(10000), b.n(20000)
    ncard, nmerchant, ntxn = b.n(15000), b.n(5000), b.n(80000)
    ntransfer, nloan, nrepay = b.n(20000), b.n(5000), b.n(30000)

    branches = [(i, f"分行{i:03d}", random.choice(CITY), random.choice(REGION), random.choice(["一级", "二级", "社区"]))
                for i in range(1, nbranch + 1)]
    b.table("branches", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("city", "TEXT"),
        ("region", "TEXT"), ("branch_level", "TEXT"),
    ], branches, ("city", "region", "branch_level"), "银行网点；客户/账户/贷款都归属网点")

    customers = [(i, f"客户{i:05d}", random.choice(["男", "女"]), d(),
                  random.choice(CITY), random.choice(["企业主", "白领", "学生", "自由职业", "退休"]),
                  random.choice(["普通", "金卡", "白金", "私行"]), random.choice(["低", "中", "高"]))
                 for i in range(1, ncust + 1)]
    b.table("customers", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("gender", "TEXT"), ("birth_date", "TEXT"),
        ("city", "TEXT"), ("occupation", "TEXT"), ("segment", "TEXT"), ("risk_level", "TEXT"),
    ], customers, ("city", "segment", "risk_level"), "零售客户；风险等级和客群分层用于交叉分析")

    accounts = [(i, random.randint(1, ncust), random.randint(1, nbranch),
                 random.choice(["储蓄", "信用", "理财", "对公"]), d(), money(0, 500000),
                 random.choice(["正常", "冻结", "销户"]))
                for i in range(1, nacc + 1)]
    b.table("accounts", [
        ("id", "INTEGER PRIMARY KEY"), ("customer_id", "INTEGER REFERENCES customers(id)"),
        ("branch_id", "INTEGER REFERENCES branches(id)"), ("account_type", "TEXT"),
        ("open_date", "TEXT"), ("balance", "REAL"), ("status", "TEXT"),
    ], accounts, ("customer_id", "branch_id", "account_type", "status"),
        "账户；余额在本表，交易流水在 transactions/transfers")

    cards = [(i, random.randint(1, nacc), f"6222{random.randint(10**11, 10**12-1)}",
              random.choice(["借记卡", "信用卡"]), money(0, 100000), random.choice(["正常", "挂失", "冻结"]))
             for i in range(1, ncard + 1)]
    b.table("cards", [
        ("id", "INTEGER PRIMARY KEY"), ("account_id", "INTEGER REFERENCES accounts(id)"),
        ("card_no", "TEXT"), ("card_type", "TEXT"), ("credit_limit", "REAL"), ("status", "TEXT"),
    ], cards, ("account_id", "card_type", "status"), "银行卡；卡交易要 cards + accounts + customers")

    merchants = [(i, f"商户{i:05d}", random.choice(["餐饮", "零售", "交通", "娱乐", "医疗", "教育"]),
                  random.choice(CITY), random.randint(1000, 9999))
                 for i in range(1, nmerchant + 1)]
    b.table("merchants", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("category", "TEXT"),
        ("city", "TEXT"), ("mcc", "INTEGER"),
    ], merchants, ("category", "city", "mcc"), "收单商户；消费行业分析要关联交易")

    txns = [(i, random.randint(1, nacc), random.randint(1, ncard) if random.random() < 0.7 else None,
             random.randint(1, nmerchant), ts(), money(1, 20000),
             random.choice(["消费", "取现", "还款", "转账"]), random.choice(CHANNEL),
             random.choice(["成功", "失败", "冲正"]))
            for i in range(1, ntxn + 1)]
    b.table("transactions", [
        ("id", "INTEGER PRIMARY KEY"), ("account_id", "INTEGER REFERENCES accounts(id)"),
        ("card_id", "INTEGER REFERENCES cards(id)"), ("merchant_id", "INTEGER REFERENCES merchants(id)"),
        ("txn_time", "TEXT"), ("amount", "REAL"), ("txn_type", "TEXT"),
        ("channel", "TEXT"), ("status", "TEXT"),
    ], txns, ("account_id", "card_id", "merchant_id", "txn_time", "txn_type", "status"),
        "交易流水；账户、卡、商户、时间、类型全部拆列，多表联查才能算客户消费画像")

    transfers = [(i, random.randint(1, nacc), random.randint(1, nacc), ts(), money(1, 100000),
                  random.choice(["网银", "手机银行", "柜面"]), random.choice(["成功", "失败"]))
                 for i in range(1, ntransfer + 1)]
    b.table("transfers", [
        ("id", "INTEGER PRIMARY KEY"), ("from_account_id", "INTEGER REFERENCES accounts(id)"),
        ("to_account_id", "INTEGER REFERENCES accounts(id)"), ("txn_time", "TEXT"),
        ("amount", "REAL"), ("channel", "TEXT"), ("status", "TEXT"),
    ], transfers, ("from_account_id", "to_account_id", "txn_time", "status"),
        "行内转账；转出/转入两个账户都要 JOIN 才能算资金流向")

    loans = [(i, random.randint(1, ncust), random.randint(1, nbranch),
              random.choice(["消费贷", "经营贷", "房贷", "车贷"]), money(10000, 2000000),
              d(), d(), random.choice(["正常", "逾期", "结清"]))
             for i in range(1, nloan + 1)]
    b.table("loans", [
        ("id", "INTEGER PRIMARY KEY"), ("customer_id", "INTEGER REFERENCES customers(id)"),
        ("branch_id", "INTEGER REFERENCES branches(id)"), ("loan_type", "TEXT"),
        ("principal", "REAL"), ("start_date", "TEXT"), ("end_date", "TEXT"), ("status", "TEXT"),
    ], loans, ("customer_id", "branch_id", "loan_type", "status"), "贷款合同；余额和逾期要回查还款计划")

    repayments = [(i, random.randint(1, nloan), d(), money(100, 100000),
                   money(50, 90000), money(0, 10000), random.choice(["正常", "逾期", "提前"]))
                  for i in range(1, nrepay + 1)]
    b.table("repayments", [
        ("id", "INTEGER PRIMARY KEY"), ("loan_id", "INTEGER REFERENCES loans(id)"),
        ("pay_date", "TEXT"), ("amount", "REAL"), ("principal_part", "REAL"),
        ("interest_part", "REAL"), ("status", "TEXT"),
    ], repayments, ("loan_id", "pay_date", "status"),
        "还款流水；本金/利息拆开，贷款余额与逾期天数需按合同汇总")
    return b.finish()


# ---------------------------------------------------------------------------
# 5. 医院就诊：挂号 / 就诊 / 诊断 / 处方 / 药品 / 检验 / 支付拆表
# ---------------------------------------------------------------------------
def build_hospital(path, seed, scale=1.0):
    b = Build(path, seed, scale)
    ndept, ndoctor, npatient = b.n(40), b.n(3000), b.n(15000)
    nreg, nvisit = b.n(30000), b.n(30000)
    ndiag, npresc, nitems, ndrug = b.n(45000), b.n(25000), b.n(60000), b.n(2000)
    nlab, nresult, npay = b.n(30000), b.n(30000), b.n(30000)

    depts = [(i, f"科室{i:02d}", random.choice(["内科", "外科", "儿科", "妇科", "急诊", "检验"]),
              random.randint(1, 20))
             for i in range(1, ndept + 1)]
    b.table("departments", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("dept_type", "TEXT"), ("floor", "INTEGER"),
    ], depts, ("dept_type",), "科室；医生、挂号、就诊、检验都归属科室")

    doctors = [(i, f"医生{i:04d}", random.randint(1, ndept), random.choice(["住院医师", "主治", "副主任", "主任"]),
                random.choice(["心血管", "呼吸", "消化", "骨科", "儿科", "影像"]))
               for i in range(1, ndoctor + 1)]
    b.table("doctors", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("dept_id", "INTEGER REFERENCES departments(id)"),
        ("title", "TEXT"), ("specialty", "TEXT"),
    ], doctors, ("dept_id", "title", "specialty"), "医生；职称、科室、专长用于医生工作量分析")

    patients = [(i, f"患者{i:05d}", random.choice(["男", "女"]), d(),
                 random.choice(CITY), random.choice(["职工医保", "居民医保", "自费", "商保"]))
                for i in range(1, npatient + 1)]
    b.table("patients", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("gender", "TEXT"),
        ("birth_date", "TEXT"), ("city", "TEXT"), ("insurance_type", "TEXT"),
    ], patients, ("city", "insurance_type", "gender"), "患者主数据；医保类型用于费用结构分析")

    regs = [(i, random.randint(1, npatient), random.randint(1, ndept), random.randint(1, ndoctor),
             d(), random.choice(["普通号", "专家号", "急诊"]), money(5, 100), random.choice(["已就诊", "爽约", "退号"]))
            for i in range(1, nreg + 1)]
    b.table("registrations", [
        ("id", "INTEGER PRIMARY KEY"), ("patient_id", "INTEGER REFERENCES patients(id)"),
        ("dept_id", "INTEGER REFERENCES departments(id)"), ("doctor_id", "INTEGER REFERENCES doctors(id)"),
        ("reg_date", "TEXT"), ("reg_type", "TEXT"), ("fee", "REAL"), ("status", "TEXT"),
    ], regs, ("patient_id", "dept_id", "doctor_id", "reg_date", "status"),
        "挂号记录；爽约率、科室挂号量需要和就诊记录区分")

    visits = [(i, i, random.randint(1, npatient), d(), random.randint(1, ndept),
               random.randint(1, ndoctor), f"D{random.randint(1, 999):03d}", random.choice(["轻", "中", "重"]))
              for i in range(1, nvisit + 1)]
    b.table("visits", [
        ("id", "INTEGER PRIMARY KEY"), ("registration_id", "INTEGER REFERENCES registrations(id)"),
        ("patient_id", "INTEGER REFERENCES patients(id)"), ("visit_date", "TEXT"),
        ("dept_id", "INTEGER REFERENCES departments(id)"), ("doctor_id", "INTEGER REFERENCES doctors(id)"),
        ("diagnosis_code", "TEXT"), ("severity", "TEXT"),
    ], visits, ("registration_id", "patient_id", "dept_id", "doctor_id", "visit_date"),
        "就诊记录；挂号→就诊是一对一，费用要再去 prescriptions/lab_orders/payments")

    diags = [(i, random.randint(1, nvisit), f"I{random.randint(10, 99)}.{random.randint(0,9)}",
              random.choice(["高血压", "糖尿病", "肺炎", "骨折", "胃炎", "冠心病"]), random.choice(["主诊断", "次诊断"]))
             for i in range(1, ndiag + 1)]
    b.table("diagnoses", [
        ("id", "INTEGER PRIMARY KEY"), ("visit_id", "INTEGER REFERENCES visits(id)"),
        ("code", "TEXT"), ("name", "TEXT"), ("diag_type", "TEXT"),
    ], diags, ("visit_id", "name", "diag_type"), "诊断；一次就诊可有多个诊断，主/次诊断分开")

    prescs = [(i, random.randint(1, nvisit), random.randint(1, ndoctor), d(), random.choice(["已发药", "待发药", "取消"]))
              for i in range(1, npresc + 1)]
    b.table("prescriptions", [
        ("id", "INTEGER PRIMARY KEY"), ("visit_id", "INTEGER REFERENCES visits(id)"),
        ("doctor_id", "INTEGER REFERENCES doctors(id)"), ("prescribe_date", "TEXT"), ("status", "TEXT"),
    ], prescs, ("visit_id", "doctor_id", "status"), "处方；处方明细和药品金额在 prescription_items")

    drugs = [(i, f"药品{i:04d}", random.choice(["抗生素", "心血管", "消化", "镇痛", "维生素"]),
              random.choice(["片剂", "胶囊", "注射液", "颗粒"]), money(1, 500), f"药厂{random.randint(1,50):02d}")
             for i in range(1, ndrug + 1)]
    b.table("drugs", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("category", "TEXT"),
        ("spec", "TEXT"), ("unit_price", "REAL"), ("manufacturer", "TEXT"),
    ], drugs, ("category", "manufacturer"), "药品主数据；药品收入要关联处方明细")

    presc_items = [(i, random.randint(1, npresc), random.randint(1, ndrug), random.randint(1, 10),
                    money(1, 500), 0.0)
                   for i in range(1, nitems + 1)]
    presc_items = [(i, pid, did, qty, unit, round(qty * unit, 2))
                   for i, pid, did, qty, unit, _ in presc_items]
    b.table("prescription_items", [
        ("id", "INTEGER PRIMARY KEY"), ("prescription_id", "INTEGER REFERENCES prescriptions(id)"),
        ("drug_id", "INTEGER REFERENCES drugs(id)"), ("qty", "INTEGER"),
        ("unit_price", "REAL"), ("amount", "REAL"),
    ], presc_items, ("prescription_id", "drug_id"), "处方明细；药品销量/收入必须从本表汇总")

    labs = [(i, random.randint(1, nvisit), random.randint(1, npatient), d(),
             random.choice(["血常规", "生化", "CT", "MRI", "B超"]), random.choice(["已完成", "待检验"]))
            for i in range(1, nlab + 1)]
    b.table("lab_orders", [
        ("id", "INTEGER PRIMARY KEY"), ("visit_id", "INTEGER REFERENCES visits(id)"),
        ("patient_id", "INTEGER REFERENCES patients(id)"), ("order_date", "TEXT"),
        ("item_name", "TEXT"), ("status", "TEXT"),
    ], labs, ("visit_id", "patient_id", "item_name", "status"), "检验申请；检验结果在 lab_results")

    results = [(i, random.randint(1, nlab), money(0, 100), random.choice(["mg/L", "mmol/L", "%", "U/L"]),
                f"{random.randint(1,50)}-{random.randint(50,100)}", random.choice(["正常", "偏高", "偏低"]))
               for i in range(1, nresult + 1)]
    b.table("lab_results", [
        ("id", "INTEGER PRIMARY KEY"), ("lab_order_id", "INTEGER REFERENCES lab_orders(id)"),
        ("result_value", "REAL"), ("unit", "TEXT"), ("ref_range", "TEXT"), ("abnormal_flag", "TEXT"),
    ], results, ("lab_order_id", "abnormal_flag"), "检验结果；异常率要 lab_orders + lab_results")

    pays = [(i, random.randint(1, nvisit), d(), money(10, 5000),
             random.choice(["医保", "自费", "商保", "混合"]), random.choice(["已支付", "欠费", "退款"]))
            for i in range(1, npay + 1)]
    b.table("payments", [
        ("id", "INTEGER PRIMARY KEY"), ("visit_id", "INTEGER REFERENCES visits(id)"),
        ("pay_date", "TEXT"), ("amount", "REAL"), ("pay_type", "TEXT"), ("status", "TEXT"),
    ], pays, ("visit_id", "pay_date", "pay_type", "status"),
        "就诊支付；和处方/检验金额不保证相等，用于收入对账与欠费分析")
    return b.finish()


# ---------------------------------------------------------------------------
# 6. 在线教育：课程 / 班级 / 报名 / 课次 / 进度 / 作业 / 考试拆表
# ---------------------------------------------------------------------------
def build_education(path, seed, scale=1.0):
    b = Build(path, seed, scale)
    nteacher, nstudent = b.n(500), b.n(15000)
    ncourse, nclass = b.n(500), b.n(3000)
    nenroll, nlesson = b.n(40000), b.n(20000)
    nprogress, nassign, nsub = b.n(100000), b.n(15000), b.n(60000)
    nexam, nscore = b.n(2000), b.n(40000)

    teachers = [(i, f"教师{i:04d}", random.choice(["语文", "数学", "英语", "物理", "化学", "编程"]),
                 random.choice(["初级", "中级", "高级", "特级"]), d())
                for i in range(1, nteacher + 1)]
    b.table("teachers", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("subject", "TEXT"),
        ("teacher_level", "TEXT"), ("join_date", "TEXT"),
    ], teachers, ("subject", "teacher_level"), "教师；课程和班级都绑定教师")

    students = [(i, f"学生{i:05d}", random.choice(["男", "女"]),
                 random.choice(["初一", "初二", "初三", "高一", "高二", "高三"]),
                 random.choice(CITY), d())
                for i in range(1, nstudent + 1)]
    b.table("students", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("gender", "TEXT"),
        ("grade", "TEXT"), ("city", "TEXT"), ("register_date", "TEXT"),
    ], students, ("grade", "city", "gender"), "学生主数据；年级和城市用于区域/学段分析")

    courses = [(i, f"课程{i:04d}", random.choice(["语文", "数学", "英语", "物理", "化学", "编程"]),
                random.choice(["基础", "提高", "冲刺"]), money(100, 5000), random.randint(1, nteacher),
                random.choice(["上架", "下架"]))
               for i in range(1, ncourse + 1)]
    b.table("courses", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("subject", "TEXT"),
        ("course_level", "TEXT"), ("price", "REAL"), ("teacher_id", "INTEGER REFERENCES teachers(id)"),
        ("status", "TEXT"),
    ], courses, ("subject", "course_level", "teacher_id", "status"), "课程；班级、报名、考试都挂在课程下")

    classes = [(i, random.randint(1, ncourse), random.randint(1, nteacher), d(), d(),
                random.randint(10, 200), random.choice(["直播", "录播", "面授"]))
               for i in range(1, nclass + 1)]
    b.table("classes", [
        ("id", "INTEGER PRIMARY KEY"), ("course_id", "INTEGER REFERENCES courses(id)"),
        ("teacher_id", "INTEGER REFERENCES teachers(id)"), ("start_date", "TEXT"),
        ("end_date", "TEXT"), ("capacity", "INTEGER"), ("mode", "TEXT"),
    ], classes, ("course_id", "teacher_id", "mode"), "班级；一个课程多个班，报名和课次都落在班级")

    enrolls = [(i, random.randint(1, nstudent), random.randint(1, nclass), d(),
                random.choice(["在读", "退课", "结课"]), money(100, 5000))
               for i in range(1, nenroll + 1)]
    b.table("enrollments", [
        ("id", "INTEGER PRIMARY KEY"), ("student_id", "INTEGER REFERENCES students(id)"),
        ("class_id", "INTEGER REFERENCES classes(id)"), ("enroll_date", "TEXT"),
        ("status", "TEXT"), ("amount", "REAL"),
    ], enrolls, ("student_id", "class_id", "status"), "报名记录；报名金额在本表，课程收入要 join classes/courses")

    lessons = [(i, random.randint(1, nclass), j, d(), random.randint(30, 120), f"第{j}讲")
               for i in range(1, nlesson + 1) for j in [random.randint(1, 30)]]
    b.table("lessons", [
        ("id", "INTEGER PRIMARY KEY"), ("class_id", "INTEGER REFERENCES classes(id)"),
        ("seq", "INTEGER"), ("lesson_date", "TEXT"), ("duration_min", "INTEGER"), ("topic", "TEXT"),
    ], lessons, ("class_id", "lesson_date"), "课次；学习进度按 lessons 逐节统计")

    progress = [(i, random.randint(1, nenroll), random.randint(1, nlesson), random.randint(1, 180),
                 random.choice([0, 1]), pct(), ts())
                for i in range(1, nprogress + 1)]
    b.table("progress", [
        ("id", "INTEGER PRIMARY KEY"), ("enrollment_id", "INTEGER REFERENCES enrollments(id)"),
        ("lesson_id", "INTEGER REFERENCES lessons(id)"), ("study_min", "INTEGER"),
        ("completed", "INTEGER"), ("video_pct", "REAL"), ("last_time", "TEXT"),
    ], progress, ("enrollment_id", "lesson_id", "completed"), "学习进度；完课率要 enrollment→lesson→progress 三表")

    assigns = [(i, random.randint(1, nlesson), f"作业{i:05d}", random.randint(10, 100), d())
               for i in range(1, nassign + 1)]
    b.table("assignments", [
        ("id", "INTEGER PRIMARY KEY"), ("lesson_id", "INTEGER REFERENCES lessons(id)"),
        ("title", "TEXT"), ("total_score", "REAL"), ("due_date", "TEXT"),
    ], assigns, ("lesson_id", "due_date"), "作业；提交和得分在 submissions")

    submissions = [(i, random.randint(1, nassign), random.randint(1, nstudent), ts(),
                    money(0, 100), random.choice(["已批改", "待批改", "迟交"]))
                   for i in range(1, nsub + 1)]
    b.table("submissions", [
        ("id", "INTEGER PRIMARY KEY"), ("assignment_id", "INTEGER REFERENCES assignments(id)"),
        ("student_id", "INTEGER REFERENCES students(id)"), ("submit_time", "TEXT"),
        ("score", "REAL"), ("status", "TEXT"),
    ], submissions, ("assignment_id", "student_id", "status"), "作业提交；得分和作业总分要 join assignments")

    exams = [(i, random.randint(1, ncourse), d(), random.randint(50, 150), random.choice(["月考", "期中", "期末", "模拟"]))
             for i in range(1, nexam + 1)]
    b.table("exams", [
        ("id", "INTEGER PRIMARY KEY"), ("course_id", "INTEGER REFERENCES courses(id)"),
        ("exam_date", "TEXT"), ("total_score", "REAL"), ("exam_type", "TEXT"),
    ], exams, ("course_id", "exam_date", "exam_type"), "考试；成绩在 exam_scores")

    scores = [(i, random.randint(1, nexam), random.randint(1, nstudent), money(0, 150), random.randint(1, 1000))
              for i in range(1, nscore + 1)]
    b.table("exam_scores", [
        ("id", "INTEGER PRIMARY KEY"), ("exam_id", "INTEGER REFERENCES exams(id)"),
        ("student_id", "INTEGER REFERENCES students(id)"), ("score", "REAL"), ("rank_no", "INTEGER"),
    ], scores, ("exam_id", "student_id", "rank_no"), "考试成绩；排名、班级均分需关联学生/课程")
    return b.finish()


# ---------------------------------------------------------------------------
# 7. 制造供应链：物料 / BOM / 采购 / 库存 / 工单 / 工序 / 质检拆表
# ---------------------------------------------------------------------------
def build_manufacturing(path, seed, scale=1.0):
    b = Build(path, seed, scale)
    nsup, nmat, nprod = b.n(1000), b.n(5000), b.n(2000)
    nbom, nwh, ninv = b.n(12000), b.n(30), b.n(20000)
    npo, npoi, nreceipt = b.n(25000), b.n(60000), b.n(20000)
    nwo, nop, nqc, nmachine = b.n(25000), b.n(80000), b.n(30000), b.n(300)

    suppliers = [(i, f"供应商{i:04d}", random.choice(CITY), random.choice(["战略", "核心", "普通"]), random.randint(1, 5))
                 for i in range(1, nsup + 1)]
    b.table("suppliers", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("city", "TEXT"),
        ("supplier_level", "TEXT"), ("rating", "INTEGER"),
    ], suppliers, ("city", "supplier_level", "rating"), "供应商；采购单和物料都关联供应商评级")

    materials = [(i, f"物料{i:05d}", random.choice(["电子", "结构", "包材", "化工", "五金"]),
                 random.choice(["个", "kg", "m", "L"]), money(1, 500), random.randint(10, 1000))
                 for i in range(1, nmat + 1)]
    b.table("materials", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("material_category", "TEXT"),
        ("unit", "TEXT"), ("standard_price", "REAL"), ("safety_stock", "INTEGER"),
    ], materials, ("material_category", "unit"), "物料主数据；库存、BOM、采购明细都引用")

    products = [(i, f"产品{i:04d}", random.choice(["家电", "汽车配件", "消费电子", "工业设备"]),
                 money(100, 2000), money(200, 5000))
                for i in range(1, nprod + 1)]
    b.table("products", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("product_category", "TEXT"),
        ("standard_cost", "REAL"), ("price", "REAL"),
    ], products, ("product_category",), "成品主数据；BOM 和工单都挂产品")

    bom = [(i, random.randint(1, nprod), random.randint(1, nmat), random.randint(1, 20),
            random.choice(["个", "kg", "m"]), pct())
           for i in range(1, nbom + 1)]
    b.table("bom", [
        ("id", "INTEGER PRIMARY KEY"), ("product_id", "INTEGER REFERENCES products(id)"),
        ("material_id", "INTEGER REFERENCES materials(id)"), ("qty", "INTEGER"),
        ("unit", "TEXT"), ("scrap_rate", "REAL"),
    ], bom, ("product_id", "material_id"), "BOM 桥接表；产品成本要 product→bom→material 三表")

    whs = [(i, f"仓库{i:02d}", random.choice(CITY), random.choice(["原料", "成品", "混合"]))
           for i in range(1, nwh + 1)]
    b.table("warehouses", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("city", "TEXT"), ("warehouse_type", "TEXT"),
    ], whs, ("city", "warehouse_type"), "仓库；库存、采购收货、工单都归属仓库")

    inv = [(i, random.randint(1, nwh), random.randint(1, nmat), random.randint(0, 100000), d())
           for i in range(1, ninv + 1)]
    b.table("inventory", [
        ("id", "INTEGER PRIMARY KEY"), ("warehouse_id", "INTEGER REFERENCES warehouses(id)"),
        ("material_id", "INTEGER REFERENCES materials(id)"), ("qty", "INTEGER"), ("last_update", "TEXT"),
    ], inv, ("warehouse_id", "material_id", "last_update"), "库存快照；缺料分析要 inventory→material→bom")

    pos = [(i, random.randint(1, nsup), random.randint(1, nwh), d(),
            random.choice(["已收货", "部分收货", "待收货", "取消"]), money(1000, 500000))
           for i in range(1, npo + 1)]
    b.table("purchase_orders", [
        ("id", "INTEGER PRIMARY KEY"), ("supplier_id", "INTEGER REFERENCES suppliers(id)"),
        ("warehouse_id", "INTEGER REFERENCES warehouses(id)"), ("order_date", "TEXT"),
        ("status", "TEXT"), ("total_amount", "REAL"),
    ], pos, ("supplier_id", "warehouse_id", "order_date", "status"), "采购订单；明细在 po_items，收货在 receipts")

    pois = []
    cur = 1
    for po in range(1, npo + 1):
        for _ in range(random.randint(1, 3)):
            mid = random.randint(1, nmat)
            qty = random.randint(1, 1000)
            price = money(1, 500)
            pois.append((cur, po, mid, qty, price, round(qty * price, 2)))
            cur += 1
    b.table("po_items", [
        ("id", "INTEGER PRIMARY KEY"), ("po_id", "INTEGER REFERENCES purchase_orders(id)"),
        ("material_id", "INTEGER REFERENCES materials(id)"), ("qty", "INTEGER"),
        ("unit_price", "REAL"), ("amount", "REAL"),
    ], pois, ("po_id", "material_id"), "采购明细；供应商采购额要 po→po_items→material")

    receipts = [(i, random.randint(1, npo), d(), random.randint(1, 500), random.randint(1, nwh), random.choice(["合格", "待检", "退货"]))
                for i in range(1, nreceipt + 1)]
    b.table("receipts", [
        ("id", "INTEGER PRIMARY KEY"), ("po_id", "INTEGER REFERENCES purchase_orders(id)"),
        ("receipt_date", "TEXT"), ("qty", "INTEGER"), ("warehouse_id", "INTEGER REFERENCES warehouses(id)"),
        ("status", "TEXT"),
    ], receipts, ("po_id", "receipt_date", "warehouse_id", "status"), "采购收货；到货及时率要 PO + receipts")

    wos = [(i, random.randint(1, nprod), random.randint(1, nwh), d(),
            d() if random.random() < 0.8 else None, d() if random.random() < 0.7 else None,
            random.randint(10, 1000), random.choice(["完工", "生产中", "计划", "暂停"]))
           for i in range(1, nwo + 1)]
    b.table("work_orders", [
        ("id", "INTEGER PRIMARY KEY"), ("product_id", "INTEGER REFERENCES products(id)"),
        ("warehouse_id", "INTEGER REFERENCES warehouses(id)"), ("plan_date", "TEXT"),
        ("start_date", "TEXT"), ("end_date", "TEXT"), ("qty", "INTEGER"), ("status", "TEXT"),
    ], wos, ("product_id", "warehouse_id", "plan_date", "status"), "生产工单；进度/良率要 operations + quality_checks")

    machines = [(i, f"设备{i:03d}", random.choice(["冲压", "注塑", "装配", "检测", "包装"]),
                 random.choice(["一车间", "二车间", "三车间"]), random.randint(10, 1000))
                for i in range(1, nmachine + 1)]
    b.table("machines", [
        ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("machine_type", "TEXT"),
        ("workshop", "TEXT"), ("capacity", "INTEGER"),
    ], machines, ("machine_type", "workshop"), "设备；工序和质检都关联设备/车间")

    ops = [(i, random.randint(1, nwo), j, random.choice(["切割", "焊接", "装配", "检测", "包装"]),
            random.randint(1, nmachine), random.randint(1, 120), random.randint(1, 120))
           for i in range(1, nop + 1) for j in [random.randint(1, 6)]]
    b.table("operations", [
        ("id", "INTEGER PRIMARY KEY"), ("work_order_id", "INTEGER REFERENCES work_orders(id)"),
        ("seq", "INTEGER"), ("operation", "TEXT"), ("machine_id", "INTEGER REFERENCES machines(id)"),
        ("planned_min", "INTEGER"), ("actual_min", "INTEGER"),
    ], ops, ("work_order_id", "machine_id", "operation"), "工单工序；实际工时/设备效率要三表关联")

    qcs = [(i, random.randint(1, nwo), d(), random.choice(["外观", "尺寸", "性能", "包装"]),
            random.choice(["合格", "不合格", "让步接收"]), random.randint(0, 20))
           for i in range(1, nqc + 1)]
    b.table("quality_checks", [
        ("id", "INTEGER PRIMARY KEY"), ("work_order_id", "INTEGER REFERENCES work_orders(id)"),
        ("check_date", "TEXT"), ("check_item", "TEXT"), ("result", "TEXT"), ("defect_qty", "INTEGER"),
    ], qcs, ("work_order_id", "check_date", "result"), "质检记录；不良率和返工成本要工单+工序+质检")
    return b.finish()


# ---------------------------------------------------------------------------
# 8. 150 表宽库：12 维表 + 12 事实表 + 126 卫星表，全部有业务角色注释
# ---------------------------------------------------------------------------
WIDE_ROLES = [
    "会员基础", "会员标签", "会员积分", "会员等级", "会员流失", "会员活跃", "会员偏好", "会员画像", "会员触达", "会员生命周期",
    "商品主档", "商品类目", "商品品牌", "商品价格", "商品库存", "商品批次", "商品保质期", "商品组合", "商品替代", "商品召回",
    "订单主档", "订单明细", "订单优惠", "订单支付", "订单退款", "订单履约", "订单取消", "订单拆单", "订单合并", "订单结算",
    "渠道主档", "渠道佣金", "渠道费用", "渠道活动", "渠道返点", "渠道结算", "渠道对账", "渠道排行", "渠道转化", "渠道归因",
    "库存快照", "库存流水", "库存调拨", "库存盘点", "库存预警", "库存周转", "库存成本", "库存库位", "库存锁定", "库存损耗",
    "采购申请", "采购订单", "采购到货", "采购退货", "采购价格", "采购合同", "采购对账", "采购配额", "采购交期", "采购质检",
    "物流运单", "物流路由", "物流扫描", "物流费用", "物流异常", "物流时效", "物流签收", "物流退回", "物流承运商", "物流网点",
    "门店主档", "门店销售", "门店库存", "门店客流", "门店租金", "门店人效", "门店坪效", "门店促销", "门店盘点", "门店关店",
    "营销活动", "营销预算", "营销投放", "营销转化", "营销优惠券", "营销裂变", "营销内容", "营销触达", "营销归因", "营销ROI",
    "财务总账", "财务应收", "财务应付", "财务成本", "财务费用", "财务预算", "财务凭证", "财务税务", "财务结算", "财务利润",
    "客服工单", "客服会话", "客服满意度", "客服响应", "客服升级", "客服知识库", "客服质检", "客服排班", "客服绩效", "客服投诉",
    "系统用户", "系统角色", "系统权限", "系统日志", "系统任务", "系统接口", "系统配置", "系统告警", "系统备份", "系统审计",
]


def build_wide(path, seed, scale=1.0):
    b = Build(path, seed, scale)
    dims = ["region", "channel", "customer", "product", "store", "supplier", "employee",
            "campaign", "warehouse", "device", "account", "contract"]
    dim_rows = {}
    for i, dname in enumerate(dims, 1):
        rows = [(j, f"{dname}_{j:05d}", random.choice(CITY), random.randint(1, 100), d())
                for j in range(1, b.n(500) + 1)]
        b.table(f"d_{dname}", [
            ("id", "INTEGER PRIMARY KEY"), ("name", "TEXT"), ("city", "TEXT"),
            ("level_no", "INTEGER"), ("created_date", "TEXT"),
        ], rows, ("city", "level_no"), f"{dname} 维表；宽库所有事实/卫星表都汇到这张维表")
        dim_rows[dname] = len(rows)

    facts = ["sales", "orders", "inventory", "shipment", "payment", "return",
             "campaign_cost", "service_ticket", "stock_move", "production", "quality", "ledger"]
    for i, fname in enumerate(facts, 1):
        dname = dims[(i - 1) % len(dims)]
        rows = [(j, random.randint(1, dim_rows[dname]), random.randint(1, 1000),
                 money(1, 10000), d(), random.choice(["正常", "异常", "完成"]))
                for j in range(1, b.n(8000) + 1)]
        b.table(f"f_{fname}", [
            ("id", "INTEGER PRIMARY KEY"), (f"{dname}_id", f"INTEGER REFERENCES d_{dname}(id)"),
            ("qty", "INTEGER"), ("amount", "REAL"), ("fact_date", "TEXT"), ("status", "TEXT"),
        ], rows, (f"{dname}_id", "fact_date", "status"), f"{fname} 事实表；和 d_{dname} 维表关联")

    for i in range(1, 127):
        role = WIDE_ROLES[(i - 1) % len(WIDE_ROLES)]
        dname = dims[(i - 1) % len(dims)]
        rows = [(j, random.randint(1, dim_rows[dname]), money(0, 1000), d())
                for j in range(1, b.n(200) + 1)]
        b.table(f"t_{i:03d}", [
            ("id", "INTEGER PRIMARY KEY"), (f"{dname}_id", f"INTEGER REFERENCES d_{dname}(id)"),
            ("metric_value", "REAL"), ("stat_date", "TEXT"),
        ], rows, (f"{dname}_id", "stat_date"), f"{role}（卫星表 {i:03d}），按 {dname} 维表聚合")
    return b.finish()


# ---------------------------------------------------------------------------
# 规格与注册
# ---------------------------------------------------------------------------
SPECS = [
    ("ec_retail", "测试 · 电商零售 A", "电商零售", 1101, build_ecommerce),
    ("ec_market", "测试 · 电商零售 B", "电商零售", 1102, build_ecommerce),
    ("saas_cloud", "测试 · SaaS 订阅 A", "SaaS 订阅", 1201, build_saas),
    ("saas_growth", "测试 · SaaS 订阅 B", "SaaS 订阅", 1202, build_saas),
    ("log_parcel", "测试 · 物流履约 A", "物流履约", 1301, build_logistics),
    ("log_network", "测试 · 物流履约 B", "物流履约", 1302, build_logistics),
    ("bank_core", "测试 · 银行账户 A", "银行金融", 1401, build_bank),
    ("bank_credit", "测试 · 银行账户 B", "银行金融", 1402, build_bank),
    ("his_clinic", "测试 · 医院就诊 A", "医院医疗", 1501, build_hospital),
    ("his_lab", "测试 · 医院就诊 B", "医院医疗", 1502, build_hospital),
    ("edu_learning", "测试 · 在线教育 A", "在线教育", 1601, build_education),
    ("edu_exam", "测试 · 在线教育 B", "在线教育", 1602, build_education),
    ("mfg_production", "测试 · 制造供应链 A", "制造供应链", 1701, build_manufacturing),
    ("mfg_supply", "测试 · 制造供应链 B", "制造供应链", 1702, build_manufacturing),
    ("wide_150", "测试 · 150 表宽库", "大 schema", 1801, build_wide),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", type=float, default=0.8,
                        help="行数缩放，默认 0.8；1.0 约比默认多 25%% 数据")
    parser.add_argument("--domain", default="", help="只生成指定业务域，例如 电商零售")
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    store = MetadataStore(config.STORE_PATH)
    manifest = []
    t_all = time.perf_counter()
    for slug, name, domain, seed, builder in SPECS:
        if args.domain and args.domain not in domain:
            continue
        path = OUT_DIR / f"{slug}.db"
        tmp = path.with_suffix(".db.tmp")
        if tmp.exists():
            tmp.unlink()
        print(f"[{domain}] {name} -> {path.name}")
        stats = builder(tmp, seed, args.scale)
        if path.exists():
            path.unlink()
        tmp.replace(path)
        url = f"sqlite:///testdata/{slug}.db"
        existing = next((c for c in store.list_connections() if c["name"] == name), None)
        if existing:
            cid = existing["id"]
        else:
            cid = store.add_connection(name, url, "sqlite", f"testdata:{slug}")
        engine = engine_factory.make_engine(engine_factory.resolve_url(url, config.DATA_DIR))
        catalog = introspect(engine)
        store.save_catalog(cid, catalog)
        engine.dispose()
        manifest.append({
            "slug": slug, "name": name, "domain": domain,
            "connection_id": cid, "url": url,
            "tables": len(catalog["tables"]),
            "rows": sum(stats.values()),
            "scale": args.scale,
            "row_by_table": stats,
        })
        print(f"  tables={len(catalog['tables'])} rows={sum(stats.values()):,} cid={cid}")

    existing_manifest = []
    if MANIFEST.exists():
        try:
            existing_manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        except Exception:
            existing_manifest = []
    if args.domain:
        by_slug = {x["slug"]: x for x in existing_manifest}
        by_slug.update({x["slug"]: x for x in manifest})
        manifest = list(by_slug.values())
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    total_rows = sum(x["rows"] for x in manifest)
    total_size = sum((OUT_DIR / f"{x['slug']}.db").stat().st_size for x in manifest)
    print(f"\n完成：{len(manifest)} 个库，{total_rows:,} 行，{total_size/1048576:.1f} MB，耗时 {time.perf_counter()-t_all:.1f}s")


if __name__ == "__main__":
    main()
