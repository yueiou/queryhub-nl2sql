"""生成演示用 SQLite 电商库，并注册数据源 / 指标口径，开箱即可问数。

运行：python scripts/seed_demo.py
"""
import os
import random
import sqlite3
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core import config  # noqa: E402
from app.db import engine as engine_factory  # noqa: E402
from app.db.introspect import introspect  # noqa: E402
from app.db.metadata import MetadataStore  # noqa: E402

DB_PATH = os.path.join(config.DATA_DIR, "demo_shop.db")
random.seed(20261122)

CHANNELS = [("天猫", "华东"), ("京东", "华北"), ("抖音", "华南"), ("线下门店", "华东")]
PRODUCTS = {
    "数码": [("耳机", 399), ("机械键盘", 549), ("移动硬盘", 429), ("智能手表", 1299)],
    "家电": [("空气炸锅", 329), ("扫地机器人", 2199), ("电饭煲", 459), ("吹风机", 269)],
    "服饰": [("冲锋衣", 699), ("运动鞋", 499), ("卫衣", 259), ("羽绒服", 999)],
    "食品": [("坚果礼盒", 129), ("咖啡豆", 99), ("牛肉干", 89), ("水果礼盒", 159)],
}
CITIES = ["北京", "上海", "广州", "成都", "杭州", "济南", "深圳", "武汉"]
STATUS = ["completed"] * 62 + ["shipped"] * 22 + ["paid"] * 10 + ["refunded"] * 6


def build():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE fact_order(
          order_id INTEGER PRIMARY KEY,
          order_date TEXT NOT NULL,
          channel TEXT NOT NULL,
          product TEXT NOT NULL,
          category TEXT NOT NULL,
          city TEXT NOT NULL,
          qty INTEGER NOT NULL,
          pay_amount REAL NOT NULL,
          status TEXT NOT NULL
        );
        CREATE TABLE dim_channel(
          channel_id INTEGER PRIMARY KEY,
          channel TEXT NOT NULL,
          region TEXT NOT NULL
        );
        CREATE TABLE dim_product(
          product_id INTEGER PRIMARY KEY,
          product TEXT NOT NULL,
          category TEXT NOT NULL,
          unit_price REAL NOT NULL
        );
        CREATE TABLE dim_customer(
          customer_id INTEGER PRIMARY KEY,
          customer TEXT NOT NULL,
          city TEXT NOT NULL,
          level TEXT NOT NULL,
          reg_date TEXT NOT NULL
        );
        CREATE TABLE _schema_notes(
          table_name TEXT,
          column_name TEXT,
          note TEXT
        );
        CREATE INDEX idx_order_date ON fact_order(order_date);
        """
    )

    notes = [
        ("fact_order", "", "订单事实表"),
        ("fact_order", "order_date", "下单日期"),
        ("fact_order", "channel", "销售渠道"),
        ("fact_order", "product", "商品名称"),
        ("fact_order", "category", "商品品类"),
        ("fact_order", "city", "收货城市"),
        ("fact_order", "qty", "购买数量"),
        ("fact_order", "pay_amount", "实付金额"),
        ("fact_order", "status", "订单状态（completed已完成/shipped已发货/paid已支付/refunded已退款）"),
        ("dim_channel", "", "渠道维度表"),
        ("dim_channel", "channel", "渠道名称"),
        ("dim_channel", "region", "所属大区"),
        ("dim_product", "", "商品维度表"),
        ("dim_product", "product", "商品名称"),
        ("dim_product", "category", "商品品类"),
        ("dim_product", "unit_price", "商品单价"),
        ("dim_customer", "", "客户维度表"),
        ("dim_customer", "customer", "客户名称"),
        ("dim_customer", "city", "所在城市"),
        ("dim_customer", "level", "会员等级"),
        ("dim_customer", "reg_date", "注册日期"),
    ]
    cur.executemany("INSERT INTO _schema_notes VALUES(?,?,?)", notes)

    for i, (name, region) in enumerate(CHANNELS, 1):
        cur.execute("INSERT INTO dim_channel VALUES(?,?,?)", (i, name, region))

    pid = 0
    for cat, items in PRODUCTS.items():
        for name, price in items:
            pid += 1
            cur.execute("INSERT INTO dim_product VALUES(?,?,?,?)", (pid, name, cat, price))

    surnames = ["张", "李", "王", "刘", "陈", "杨", "赵", "孙", "周", "吴"]
    for i in range(1, 501):
        cur.execute(
            "INSERT INTO dim_customer VALUES(?,?,?,?,?)",
            (i, random.choice(surnames) + f"客户{i:03d}", random.choice(CITIES),
             random.choice(["普通", "银牌", "金牌", "钻石"]),
             (date(2024, 1, 1) + timedelta(days=random.randint(0, 900))).isoformat(),
            ),
        )

    start = date(2026, 4, 1)
    total_days = 230
    for oid in range(1, 3201):
        d = start + timedelta(days=random.randint(0, total_days))
        cat = random.choice(list(PRODUCTS))
        product, price = random.choice(PRODUCTS[cat])
        qty = random.choice([1, 1, 1, 2, 2, 3, 5])
        discount = random.choice([1.0, 1.0, 0.95, 0.9, 0.85])
        amount = round(price * qty * discount, 2)
        cur.execute(
            "INSERT INTO fact_order VALUES(?,?,?,?,?,?,?,?,?)",
            (oid, d.isoformat(), random.choice(CHANNELS)[0], product, cat,
             random.choice(CITIES), qty, amount, random.choice(STATUS)),
        )

    conn.commit()
    conn.close()
    print(f"已生成演示库：{DB_PATH}")


def register():
    store = MetadataStore(config.STORE_PATH)
    existing = [c for c in store.list_connections() if c["name"] == "演示电商库"]
    if existing:
        cid = existing[0]["id"]
        print(f"数据源已存在，刷新结构：{cid}")
    else:
        url = engine_factory.resolve_url(DB_PATH, config.DATA_DIR)
        cid = store.add_connection("演示电商库", url, "sqlite", "内置演示数据，可直接提问")
    engine = engine_factory.make_engine(engine_factory.resolve_url(DB_PATH, config.DATA_DIR))
    catalog = introspect(engine)
    store.save_catalog(cid, catalog)

    if not store.list_metrics(cid):
        store.add_metric(cid, "GMV", "销售额,成交额,营收,收入,销售金额", "SUM(pay_amount)", "fact_order", "订单实付金额合计")
        store.add_metric(cid, "订单量", "订单数,单量,订单数量,多少单", "COUNT(*)", "fact_order", "订单笔数")
        store.add_metric(cid, "客单价", "平均订单金额,平均客单,均值", "AVG(pay_amount)", "fact_order", "平均每单实付金额")
        store.add_metric(cid, "退款单量", "退款订单,退款数", "COUNT(CASE WHEN status='refunded' THEN 1 END)", "fact_order", "状态为已退款的订单数")
    print(f"数据源已就绪：{cid}，共 {len(catalog['tables'])} 张表、{len(store.list_metrics(cid))} 个指标口径")


if __name__ == "__main__":
    build()
    register()
