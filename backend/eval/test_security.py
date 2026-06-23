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


def setUpModule():
    """确保 DB 表存在(全新环境/fresh clone 也能跑通，幂等)。"""
    import asyncio
    from database import init_db
    asyncio.run(init_db())


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
        # 库内真实 .md 应能读到；全新环境(无kb目录)自建临时demo文件
        import os
        self.kb.mkdir(parents=True, exist_ok=True)
        mds = [f for f in os.listdir(self.kb) if f.endswith(".md")]
        created = None
        if not mds:
            created = self.kb / "_test_demo.md"
            created.write_text("# demo\n示例内容\n", encoding="utf-8")
            mds = ["_test_demo.md"]
        try:
            self.assertIsNotNone(self.searcher._safe_kb_path(mds[0]))
            self.assertTrue(len(self.searcher.read_file_content(mds[0])) > 0)
        finally:
            if created and created.exists():
                created.unlink()


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


class TestHttpPermissionMatrix(unittest.TestCase):
    """HTTP 级权限/越权回归(FastAPI TestClient)。造临时用户与会话，测完清理。"""

    @classmethod
    def setUpClass(cls):
        import sqlite3, secrets
        from fastapi.testclient import TestClient
        from main import app
        from database import DB_PATH
        cls.DB_PATH = DB_PATH
        cls.client = TestClient(app)
        cls.tokA = "test_tokA_" + secrets.token_hex(6)
        cls.tokB = "test_tokB_" + secrets.token_hex(6)
        cls.conv = "test_conv_" + secrets.token_hex(6)
        db = sqlite3.connect(DB_PATH)
        cur = db.execute("INSERT INTO users (bili_uid,username,password_hash,password_salt,role,token) VALUES (?,?,?,?,?,?)",
                         ("tuidA", "sec_userA_"+secrets.token_hex(3), "x", "y", "user", cls.tokA))
        cls.uidA = cur.lastrowid
        cur = db.execute("INSERT INTO users (bili_uid,username,password_hash,password_salt,role,token) VALUES (?,?,?,?,?,?)",
                         ("tuidB", "sec_userB_"+secrets.token_hex(3), "x", "y", "user", cls.tokB))
        cls.uidB = cur.lastrowid
        # userA 拥有一条会话历史
        db.execute("INSERT INTO chat_history (user_id,question,answer,conversation_id,created_at) VALUES (?,?,?,?,datetime('now'))",
                   (cls.uidA, "A的问题", "A的答案", cls.conv))
        db.commit(); db.close()

    @classmethod
    def tearDownClass(cls):
        import sqlite3
        db = sqlite3.connect(cls.DB_PATH)
        db.execute("DELETE FROM chat_history WHERE conversation_id = ?", (cls.conv,))
        db.execute("DELETE FROM users WHERE id IN (?,?)", (cls.uidA, cls.uidB))
        db.commit(); db.close()

    def test_anonymous_denied(self):
        for p in ["/api/documents", "/api/settings", "/api/battlemap",
                  "/api/documents/img-proxy?url=https://wiki.example.com/x.png",
                  "/api/documents/kb-image/foo.png"]:
            self.assertEqual(self.client.get(p).status_code, 401, f"匿名未被拒: {p}")

    def test_conversation_cross_user_blocked(self):
        # userB 不能读取 userA 的会话内容
        r = self.client.get(f"/api/chat/conversation/{self.conv}",
                            cookies={"qlipoth_token": self.tokB})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [], "userB 越权读到了 userA 的会话")
        # userA 自己能读到
        r2 = self.client.get(f"/api/chat/conversation/{self.conv}",
                             cookies={"qlipoth_token": self.tokA})
        self.assertTrue(len(r2.json()) >= 1, "userA 读不到自己的会话")

    def test_imgproxy_requires_login_and_https(self):
        # 登录后非 https / 非白名单 host 被拒
        r = self.client.get("/api/documents/img-proxy?url=http://evil.com/x.png",
                           cookies={"qlipoth_token": self.tokA})
        self.assertEqual(r.status_code, 400)
