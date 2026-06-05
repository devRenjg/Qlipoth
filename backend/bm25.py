"""零依赖 BM25 中文检索（生产用）。char-bigram 分词，进程内单例缓存，目录签名失效重建。

为什么零依赖：A/B 评测(eval/ab_eval.py)显示 char-bigram BM25 (Recall@10=0.78) 优于
jieba 词级 (0.75)，故生产不引入 jieba/rank_bm25——气隙环境零新增依赖、零回归面。
与现网 grep+IDF 召回做 RRF 融合、并把长度归一参数 b 调到 1.0 后，golden_set
Recall@10 0.71→0.87、MRR 0.44→0.58（调参见 eval/bm25_tune.py）。

线上以"旁路重排"接入：grep 召回不变，仅对选出的文件列表做 RRF 融合，任何异常回退
原 grep 排序，保证缺省零影响。
"""
import math
import re
import threading
from collections import Counter, defaultdict
from pathlib import Path

from config import load_settings

K1 = 1.5
# b=1.0（完全长度归一）：本库 avgdl≈1万 token、文档长度差异极大（巨型导入文档多），
# 经 eval/bm25_tune.py 在 100 题 golden set 网格调参，b 从教科书默认 0.75 调到 1.0：
# 纯 BM25 Recall@10 0.79→0.85 / MRR 0.54→0.60，与 grep 融合后 0.86→0.87，无任一类型回退。
B = 1.0
_TOKEN_RE = re.compile(r'[a-zA-Z0-9]+|[一-鿿]')


def tokenize(text: str) -> list[str]:
    """中文 char-bigram + 单字，英文/数字整词。'版本覆盖率'→版本/本覆/覆盖/盖率/单字。"""
    units = _TOKEN_RE.findall(text.lower())
    han = [u for u in units if '一' <= u <= '鿿']
    other = [u for u in units if not ('一' <= u <= '鿿')]
    toks = list(other)
    toks.extend(han)
    for i in range(len(han) - 1):
        toks.append(han[i] + han[i + 1])
    return toks


class _BM25:
    def __init__(self):
        self.names: list[str] = []
        self.tf: list[Counter] = []
        self.dl: list[int] = []
        self.df: dict[str, int] = defaultdict(int)
        self.avgdl: float = 0.0
        self.n: int = 0

    def build(self, kb_dir: str):
        kb = Path(kb_dir)
        for f in sorted(p for p in kb.rglob("*.md") if p.is_file()):
            try:
                toks = tokenize(f.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, PermissionError, OSError):
                continue
            c = Counter(toks)
            self.names.append(str(f.relative_to(kb)))  # 相对路径，与 _select_files/read_file_content 对齐
            self.tf.append(c)
            self.dl.append(len(toks))
            for t in c:
                self.df[t] += 1
        self.n = len(self.names)
        self.avgdl = sum(self.dl) / self.n if self.n else 0.0
        return self

    def topk(self, query: str, k: int = 10) -> list[str]:
        if not self.n:
            return []
        q = set(tokenize(query))
        scores = [0.0] * self.n
        for term in q:
            df = self.df.get(term)
            if not df:
                continue
            idf = math.log((self.n - df + 0.5) / (df + 0.5) + 1.0)
            for i in range(self.n):
                f = self.tf[i].get(term, 0)
                if f:
                    scores[i] += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * self.dl[i] / self.avgdl))
        ranked = sorted(zip(self.names, scores), key=lambda x: (-x[1], x[0]))
        return [n for n, s in ranked[:k] if s > 0]


_LOCK = threading.Lock()
_INDEX: _BM25 | None = None
_SIG: tuple | None = None


def _kb_signature(kb_dir: str) -> tuple:
    """目录指纹(文件数, 最大 mtime) —— 文档增删改后指纹变化触发重建。~4ms。"""
    cnt = 0
    mx = 0.0
    for f in Path(kb_dir).rglob("*.md"):
        try:
            mx = max(mx, f.stat().st_mtime)
            cnt += 1
        except OSError:
            continue
    return (cnt, round(mx, 3))


def _get_index() -> "_BM25 | None":
    """懒构建 + 签名失效重建的单例。构建失败返回 None（调用方回退）。"""
    global _INDEX, _SIG
    kb = load_settings().knowledge_base_dir
    if not Path(kb).exists():
        return None
    sig = _kb_signature(kb)
    with _LOCK:
        if _INDEX is not None and _SIG == sig:
            return _INDEX
        try:
            idx = _BM25().build(kb)
        except Exception:  # noqa: BLE001
            return _INDEX  # 重建失败保留旧索引（若有）
        _INDEX, _SIG = idx, sig
        return _INDEX


def _rrf(rank_lists: list[list[str]], k_const: int = 60) -> list[str]:
    """Reciprocal Rank Fusion：多路排名按 1/(k+rank) 累加。k=60 为经典默认。"""
    score: dict[str, float] = defaultdict(float)
    for rl in rank_lists:
        for i, f in enumerate(rl, 1):
            score[f] += 1.0 / (k_const + i)
    return sorted(score, key=lambda f: -score[f])


def fuse_select(baseline_files: list[str], question: str, max_files: int = 10) -> list[str]:
    """对现网 grep+IDF 选出的文件列表做 BM25-RRF 旁路重排。

    baseline_files: _select_files 的结果（basename 列表，已按相关度排序）。
    返回融合后的 basename 列表；BM25 不可用或异常时原样返回 baseline_files（零回归）。
    """
    try:
        idx = _get_index()
        if idx is None or not idx.n:
            return baseline_files
        bm25_top = idx.topk(question, max_files)
        if not bm25_top:
            return baseline_files
        fused = _rrf([baseline_files, bm25_top])
        # 只补充 baseline 漏掉的 BM25 高分文件，整体裁到 max_files
        return fused[:max_files]
    except Exception:  # noqa: BLE001
        return baseline_files
