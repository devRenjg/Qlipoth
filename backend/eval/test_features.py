"""克里珀功能性测试（unittest，零依赖，气隙可跑）。

覆盖演进后的新 Feature 的功能正确性（非效果/质量——效果交给 golden set 评测）：
- 问题分类路由（question_router）：分类 + 模型选择 + 零回归回退
- 导入历史展示（upload）：counts 三分计数 + display_title 兜底
- BM25 参数与融合（bm25）：b=1.0、fuse_select 旁路与异常回退
- 标签过滤（routes.query）：tag_ids → stored_path 集合、结果裁剪

不调用 LLM（避免网络依赖与不确定性），只测确定性逻辑与 API 契约。
运行: py -3.12 -m unittest eval.test_features -v
"""
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


class TestQuestionRouter(unittest.TestCase):
    """问题分类路由：分类规则 + 强/快模型选择 + 零回归。"""

    def setUp(self):
        from question_router import classify_question, route_model
        self.classify = classify_question
        self.route = route_model

    def test_classify_five_types(self):
        cases = {
            "这个特效弹幕负责同学都有谁": "负责人类",
            "红包总共要传几个字段": "数量类",
            "主态和客态到底哪个是我发的": "歧义类",
            "弹幕上飘配置上怎么搞": "方案类",
            "gift-play可用率告警什么原因触发": "排查类",
        }
        for q, expected in cases.items():
            self.assertEqual(self.classify(q), expected, f"{q} 应分类为 {expected}")

    def test_route_strong_types_to_opus(self):
        for q in ["谁负责弹幕", "配置怎么搞", "到底是A还是B"]:
            model, _ = self.route(q, "opus", "sonnet")
            self.assertEqual(model, "opus", f"{q} 应走强模型")

    def test_route_simple_types_to_fast(self):
        for q in ["一共几个房间", "告警什么原因触发的"]:
            model, _ = self.route(q, "opus", "sonnet")
            self.assertEqual(model, "sonnet", f"{q} 应走快模型")

    def test_zero_regression_when_no_fast_model(self):
        """快模型为空 → 所有问题回退强模型（零回归）。"""
        for q in ["谁负责", "几个", "怎么搞", "到底", "为什么触发"]:
            model, _ = self.route(q, "opus", "")
            self.assertEqual(model, "opus")


class TestImportHistoryDisplay(unittest.TestCase):
    """导入历史展示：三类计数 + display_title 兜底。"""

    def setUp(self):
        from routes.upload import _tree_node_counts, _tree_display_title
        self.counts = _tree_node_counts
        self.title = _tree_display_title

    def test_counts_three_way(self):
        tree = [
            {"stored_as": "a.md"},                    # success
            {"stored_as": "b.md"},                    # success
            {"stored_as": "(已存在)"},                 # skipped
            {"error": "页面加载超时"},                  # failed
            {"error": "未能获取文档数据"},               # failed
        ]
        c = self.counts(tree)
        self.assertEqual(c["success"], 2)
        self.assertEqual(c["skipped"], 1)
        self.assertEqual(c["failed"], 2)
        self.assertEqual(c["total"], 5)

    def test_display_title_fallback_on_unknown(self):
        tree = [{"title": "", "error": "x"}, {"title": "S15项目复盘", "stored_as": "s.md"}]
        self.assertEqual(self.title("未知", tree, "http://u"), "S15项目复盘")

    def test_display_title_keeps_valid(self):
        self.assertEqual(self.title("CNY复盘", [], "http://u"), "CNY复盘")

    def test_display_title_url_last_resort(self):
        tree = [{"title": "", "error": "x"}]
        self.assertEqual(self.title("未知", tree, "http://doc"), "http://doc")


class TestBM25(unittest.TestCase):
    """BM25 零依赖检索：参数、topk、fuse_select 旁路与异常回退。"""

    def setUp(self):
        import bm25
        self.bm25 = bm25

    def test_param_b_is_tuned(self):
        """长度归一参数 b 已调优为 1.0（v20260605.3）。"""
        self.assertEqual(self.bm25.B, 1.0)
        self.assertEqual(self.bm25.K1, 1.5)

    def test_tokenize_char_bigram(self):
        toks = self.bm25.tokenize("版本覆盖率")
        self.assertIn("版本", toks)   # bigram
        self.assertIn("版", toks)      # 单字兜底
        self.assertIn("覆盖", toks)

    def test_fuse_select_returns_within_max(self):
        baseline = [f"doc{i}.md" for i in range(20)]
        out = self.bm25.fuse_select(baseline, "测试问题", max_files=10)
        self.assertLessEqual(len(out), 10)
        self.assertIsInstance(out, list)

    def test_fuse_select_fallback_on_empty_index(self):
        """索引不可用时原样返回 baseline（零回归）。"""
        import bm25
        orig = bm25._get_index
        bm25._get_index = lambda: None
        try:
            baseline = ["a.md", "b.md"]
            self.assertEqual(bm25.fuse_select(baseline, "q"), baseline)
        finally:
            bm25._get_index = orig


class TestTagFilter(unittest.TestCase):
    """标签过滤搜索：tag_ids → stored_path 集合、结果裁剪正确性。"""

    @classmethod
    def setUpClass(cls):
        import asyncio
        cls.asyncio = asyncio
        from routes.query import _tagged_stored_paths, _filter_results_by_paths
        from searcher import SearchResults, SearchResult
        cls._tagged = staticmethod(_tagged_stored_paths)
        cls._filter = staticmethod(_filter_results_by_paths)
        cls.SearchResults = SearchResults
        cls.SearchResult = SearchResult

    def test_empty_tags_returns_empty_set(self):
        """不选标签 → 空集，调用方据此跳过过滤（零回归）。"""
        result = self.asyncio.run(self._tagged([]))
        self.assertEqual(result, set())

    def test_tagged_paths_nonempty_for_real_tag(self):
        """选一个真实存在的标签 → 返回非空 stored_path 集合。"""
        import sqlite3
        from config import load_settings
        db = sqlite3.connect(load_settings().db_path)
        row = db.execute("SELECT id FROM tags LIMIT 1").fetchone()
        db.close()
        if not row:
            self.skipTest("无标签数据")
        paths = self.asyncio.run(self._tagged([row[0]]))
        self.assertIsInstance(paths, set)
        self.assertGreater(len(paths), 0, "真实标签应有关联文档")

    def test_filter_results_keeps_only_allowed(self):
        """裁剪后只保留 allowed 路径集合内的结果。"""
        results = self.SearchResults()
        for f in ["keep1.md", "drop.md", "keep2.md"]:
            r = self.SearchResult(file=f, line_number=1, content="x",
                                  context_before=[], context_after=[])
            results.append(r)
        results.original_keywords = ["k"]
        results.keyword_df = {}
        allowed = {"keep1.md", "keep2.md"}
        filtered = self._filter(results, allowed)
        files = {r.file for r in filtered}
        self.assertEqual(files, allowed)


# PLACEHOLDER
if __name__ == "__main__":
    unittest.main(verbosity=2)
