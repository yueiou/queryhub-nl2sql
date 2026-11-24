"""评分器回归测试：避免评测代码本身制造虚高或虚低分数。"""
import importlib.util
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("eval_run", Path(__file__).resolve().parents[1]/"scripts/eval_run.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class ScoringTests(unittest.TestCase):
    def test_empty_and_null(self):
        self.assertTrue(runner.rows_equal([], []))
        self.assertFalse(runner.rows_equal([], [[None]]))
        self.assertFalse(runner.rows_equal([[None]], [[0]]))

    def test_extra_rows_and_duplicates(self):
        self.assertFalse(runner.rows_equal([[1], [2]], [[1]]))
        self.assertFalse(runner.rows_equal([[1], [1]], [[1], [2]]))
        self.assertTrue(runner.rows_equal([[1], [2], [1]], [[1], [1], [2]]))

    def test_order_and_column_position(self):
        self.assertTrue(runner.rows_equal([[2], [1]], [[1], [2]]))
        self.assertFalse(runner.rows_equal([[2], [1]], [[1], [2]], ordered=True))
        self.assertFalse(runner.rows_equal([[1, 2]], [[2, 1]]))
        self.assertFalse(runner.rows_equal([[1]], [[1, 2]]))

    def test_float_tolerance_and_types(self):
        self.assertTrue(runner.rows_equal([[0.1+0.2]], [[0.3]]))
        self.assertFalse(runner.rows_equal([[1.004]], [[1.0]]))
        self.assertFalse(runner.rows_equal([["1"]], [[1]]))
        # 最大匹配能处理第一个候选与两行均近似相等的情况。
        self.assertTrue(runner.rows_equal([[0.8e-6], [0]], [[0], [1.6e-6]]))

    def test_readonly_connection(self):
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"data.db"
            with closing(sqlite3.connect(path)) as conn:
                conn.execute("CREATE TABLE t(id INTEGER)")
                conn.execute("INSERT INTO t VALUES(1)")
                conn.commit()
            engine = runner.readonly_engine(path)
            try:
                with engine.connect() as conn:
                    self.assertEqual(conn.execute(text("SELECT * FROM t")).fetchall(), [(1,)])
                    with self.assertRaises(DBAPIError):
                        conn.execute(text("DELETE FROM t"))
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
