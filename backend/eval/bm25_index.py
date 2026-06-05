"""依赖无关的 BM25 中文检索原型（Task5 调研用，非生产链路）。

为什么不用 rank_bm25/jieba：
- 机器离线（无外网），pip 内网镜像今晚极慢/挂起，引入外部依赖在气隙环境上脆弱。
- 领域词（"版本覆盖率""时移压测"）大量 OOV，jieba 分词反而切碎；中文字符二元组
  (char bigram) 对这类专有名词召回更稳，且零依赖、纯标准库。

BM25 公式（Okapi BM25，k1=1.5, b=0.75）：
  score(D,Q) = Σ_t IDF(t) · f(t,D)·(k1+1) / (f(t,D) + k1·(1-b+b·|D|/avgdl))
  IDF(t) = ln( (N - df + 0.5)/(df + 0.5) + 1 )
"""
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

K1 = 1.5
B = 0.75

_TOKEN_RE = re.compile(r'[a-zA-Z0-9]+|[一-鿿]')


def tokenize(text: str) -> list[str]:
    """中文按字符二元组 + 单字，英文/数字按整词。

    例：'版本覆盖率' → ['版本','本覆','覆盖','盖率','版','本','覆','盖','率']
    既保留 bigram 的区分度，又用单字兜底召回。英文 'gaia' 整体保留。
    """
    units = _TOKEN_RE.findall(text.lower())
    han = [u for u in units if '一' <= u <= '鿿']
    other = [u for u in units if not ('一' <= u <= '鿿')]
    toks = list(other)
    toks.extend(han)  # 单字
    for i in range(len(han) - 1):
        toks.append(han[i] + han[i + 1])  # 相邻 bigram（仅在原文相邻处）
    return toks


def tokenize_jieba(text: str) -> list[str]:
    """jieba 词级分词，过滤纯空白/标点。用于和 char-bigram 方案对照。"""
    import jieba
    toks = []
    for w in jieba.cut(text.lower()):
        w = w.strip()
        if w and _TOKEN_RE.search(w):
            toks.append(w)
    return toks


class BM25Index:
    def __init__(self, tokenizer=tokenize):
        self.tok = tokenizer
        self.doc_names: list[str] = []
        self.doc_tokens: list[Counter] = []
        self.doc_len: list[int] = []
        self.df: dict[str, int] = defaultdict(int)
        self.avgdl: float = 0.0
        self.n: int = 0

    def build(self, kb_dir: str):
        kb = Path(kb_dir)
        files = sorted(f for f in kb.rglob("*.md") if f.is_file())
        for f in files:
            try:
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError):
                continue
            toks = self.tok(text)
            tf = Counter(toks)
            self.doc_names.append(f.name)
            self.doc_tokens.append(tf)
            self.doc_len.append(len(toks))
            for term in tf:
                self.df[term] += 1
        self.n = len(self.doc_names)
        self.avgdl = sum(self.doc_len) / self.n if self.n else 0.0
        return self

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log((self.n - df + 0.5) / (df + 0.5) + 1.0)

    def score_all(self, query: str) -> list[tuple[str, float]]:
        q_terms = set(self.tok(query))
        scores = [0.0] * self.n
        for term in q_terms:
            if term not in self.df:
                continue
            idf = self._idf(term)
            for i in range(self.n):
                f = self.doc_tokens[i].get(term, 0)
                if not f:
                    continue
                denom = f + K1 * (1 - B + B * self.doc_len[i] / self.avgdl)
                scores[i] += idf * f * (K1 + 1) / denom
        ranked = sorted(zip(self.doc_names, scores), key=lambda x: (-x[1], x[0]))
        return ranked

    def topk(self, query: str, k: int = 10) -> list[str]:
        return [name for name, s in self.score_all(query)[:k] if s > 0]

    def rerank(self, candidates: list[str], query: str, k: int = 10) -> list[str]:
        """只在候选文件名集合内按 BM25 重排（hybrid：grep 召回 + BM25 精排）。"""
        cand = set(candidates)
        ranked = [(n, s) for n, s in self.score_all(query) if n in cand]
        out = [n for n, s in ranked[:k] if s > 0]
        for n in candidates:  # BM25 全 0 的候选按原顺序兜底补齐
            if n not in out and len(out) < k:
                out.append(n)
        return out
