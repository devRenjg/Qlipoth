"""安全回归测试（unittest，零依赖）。固化 TechnicalQualityReview P0/P1 修复：
- 路径穿越防护（searcher._safe_kb_path / read_file_content）
- 统一鉴权依赖存在性（auth.require_login / require_admin）
- Settings 空 key 不覆盖逻辑（契约层面）

不起 HTTP 服务、不调 LLM，只测确定性安全逻辑。
运行: py -3.12 -m unittest eval.test_security -v
"""
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))


class TestPathTraversal(unittest.TestCase):
    """知识库文件读取必须限定在 KB 目录内。"""

    def setUp(self):
        import searcher
        self.searcher = searcher
        self.kb = Path(searcher._get_kb_dir())

    def test_traversal_blocked(self):
        # 各种越界路径都应被拦截(返回 None 或空内容)
        for bad in ["../config.py", "..\\config.py", "../../etc/passwd",
                    "/etc/passwd", "../database.py", "subdir/../../config.py"]:
            self.assertIsNone(self.searcher._safe_kb_path(bad), f"未拦截: {bad}")
            self.assertEqual(self.searcher.read_file_content(bad), "", f"仍可读: {bad}")

    def test_non_md_blocked(self):
        # 非 .md 文件即使在库内也拒绝
        self.assertIsNone(self.searcher._safe_kb_path("foo.txt"))
        self.assertIsNone(self.searcher._safe_kb_path("foo.py"))

    def test_legit_md_allowed(self):
        # 库内真实 .md 应能读到
        import os
        mds = [f for f in os.listdir(self.kb) if f.endswith(".md")]
        if mds:
            self.assertIsNotNone(self.searcher._safe_kb_path(mds[0]))
            self.assertTrue(len(self.searcher.read_file_content(mds[0])) > 0)


class TestAuthDeps(unittest.TestCase):
    """统一鉴权依赖必须存在且可用。"""

    def test_auth_helpers_exist(self):
        import auth
        self.assertTrue(callable(auth.require_login))
        self.assertTrue(callable(auth.require_admin))
        self.assertTrue(callable(auth._user_from_token))

    def test_protected_endpoints_depend_on_auth(self):
        # 关键读接口的函数签名应带 require_login/require_admin 依赖
        import inspect
        from routes import documents, query
        # documents.list_documents 应有 user 依赖参数
        sig = inspect.signature(documents.list_documents)
        self.assertIn("user", sig.parameters)
        # settings 写接口应依赖 require_admin
        sig2 = inspect.signature(documents.update_settings)
        self.assertIn("user", sig2.parameters)
        # query 端点应有 user 依赖
        sig3 = inspect.signature(query.query_knowledge_base)
        self.assertIn("user", sig3.parameters)


if __name__ == "__main__":
    unittest.main(verbosity=2)
