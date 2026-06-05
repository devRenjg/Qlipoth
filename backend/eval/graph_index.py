"""知识库图谱索引（Task6 调研用，非生产链路，零外部依赖）。

为对比 gbrain / Cognee 两类"知识库构建范式"是否优于现行 grep+LLM 而写。
本环境实测：外网被墙（pypi.org/huggingface 不可达）、内网无 embedding 端点、
无 Docker，因此 Khoj 全向量、Cognee 向量半无法实跑。这里用纯 LLM-chat + 词法
信号复刻两类范式的【非向量核心】，套同一 golden_set 做公平对比。

两类范式的等价原型：

1) gbrain-lite —— "互联 Markdown wiki + agent 跟随链接"
   实测语料里 [[...]] 几乎都是腾讯文档表格单元（带颜色码），不是 wiki 交叉引用，
   故 naive wikilink 图谱不成立。真实可依赖的是文档头块的显式关系：
       > 父文档: XXX        （543/628 篇有）
       > 负责人: @a, @b      （468/628 篇有）
   gbrain-lite = grep 召回种子 → 沿"父文档/同父子文档/共享 owner"做文档级 1-hop
   扩展重排。测"跟随文档关系图谱"能否补回词法漏召。

2) cognee-lite —— "LLM 抽实体关系建知识图谱 + 检索"
   Cognee 的 cognify 用 LLM 抽实体/关系建图谱。这里实体 = owner(@xxx) + 文档头
   父子关系 + 标题词 + 高区分度 token（服务名/CMD/指标，靠正则启发式，零 LLM 成本
   建索引），跨文档共现成边。查询侧用 LLM 把问题映射到实体（与现行同一次策略调用
   复用），再做实体匹配 + 1-hop 共现加权排序。测"实体中心图谱"对本语料的增益。

公开两个索引类：DocGraph（文档级关系图）、EntityGraph（实体级共现图）。
"""
import re
from collections import Counter, defaultdict
from pathlib import Path


# ---- 文档头块解析 ----------------------------------------------------------

_OWNER_RE = re.compile(r'@[A-Za-z0-9_]{1,12}')
# 高区分度实体：英文服务名/接口（gaia-rule-service, card/view_v2）、全大写常量
# (WATCHED_CHANGE)、版本号(v2)、QPS 等领域 token。中文实体走标题词。
_SERVICE_RE = re.compile(r'[a-zA-Z][a-zA-Z0-9]+(?:[-_/][a-zA-Z0-9]+)+')
_CONST_RE = re.compile(r'\b[A-Z][A-Z0-9]{3,}(?:_[A-Z0-9]+)*\b')
_HAN = re.compile(r'[一-鿿]')


def _read(f: Path) -> str:
    try:
        return f.read_text(encoding="utf-8")
    except (UnicodeDecodeError, PermissionError):
        return ""


def parse_header(text: str) -> dict:
    """抽文档头块的结构化字段：title / parent / owners。

    头块形如：
        # 标题
        > 来源: http...
        > 负责人: @a, @b
        > 父文档: XXX
    只扫前 ~30 行，避免被正文乱码污染。
    """
    lines = text.splitlines()[:30]
    title = ""
    parent = ""
    owners: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not title and s.startswith("# "):
            title = s[2:].strip()
        elif s.startswith("> 父文档:"):
            parent = s.split(":", 1)[1].strip()
        elif s.startswith("> 负责人:"):
            owners = _OWNER_RE.findall(s)
    return {"title": title, "parent": parent, "owners": owners}


# ---- gbrain-lite：文档级关系图 --------------------------------------------

class DocGraph:
    """文档级关系图：节点=文档，边=父子关系 / 共享 owner。

    用法：seed = 现行 grep 选出的候选；expand_rerank 把图上 1-hop 邻居补进来重排。
    """

    def __init__(self):
        self.names: list[str] = []
        self.title2name: dict[str, str] = {}
        self.parent: dict[str, str] = {}          # name -> 父文档标题
        self.owners: dict[str, set] = {}           # name -> {@owner}
        self.children: dict[str, list] = defaultdict(list)  # 父标题 -> [子 name]

    def build(self, kb_dir: str):
        kb = Path(kb_dir)
        files = sorted(f for f in kb.rglob("*.md") if f.is_file())
        for f in files:
            text = _read(f)
            if not text:
                continue
            h = parse_header(text)
            name = f.name
            self.names.append(name)
            self.parent[name] = h["parent"]
            self.owners[name] = set(h["owners"])
            # 标题 / 文件名 stem 都登记，便于父文档名解析
            if h["title"]:
                self.title2name.setdefault(h["title"], name)
            self.title2name.setdefault(f.stem, name)
        for name, ptitle in self.parent.items():
            if ptitle:
                self.children[ptitle].append(name)
        return self

    def _resolve(self, title: str) -> str | None:
        """父文档标题 → 实际文件名（精确，再退子串匹配）。"""
        if not title:
            return None
        if title in self.title2name:
            return self.title2name[title]
        for t, n in self.title2name.items():
            if title in t or t in title:
                return n
        return None

    def neighbors(self, name: str) -> list[str]:
        """1-hop 邻居：父文档 + 同父兄弟 + 自己的子文档 + owner 重叠文档。"""
        out: list[str] = []
        ptitle = self.parent.get(name, "")
        pfile = self._resolve(ptitle)
        if pfile:
            out.append(pfile)
            out.extend(c for c in self.children.get(ptitle, []) if c != name)  # 兄弟
        # 自己作为父，被谁引用
        my_title_keys = [t for t, n in self.title2name.items() if n == name]
        for tk in my_title_keys:
            out.extend(self.children.get(tk, []))
        # owner 重叠（仅当 owner 集合不太大，避免巨型枢纽爆炸）
        my_owners = self.owners.get(name, set())
        if 0 < len(my_owners) <= 6:
            for other, ow in self.owners.items():
                if other != name and my_owners & ow:
                    out.append(other)
        seen, uniq = set(), []
        for n in out:
            if n not in seen:
                seen.add(n)
                uniq.append(n)
        return uniq

    def expand_rerank(self, seed: list[str], base_scores: dict, k: int) -> list[str]:
        """gbrain 式：种子文档 + 其图邻居，按 (原始grep分 + 图传播分) 重排取 topk。

        图传播：邻居获得来源种子分数 * 衰减；多个种子指向同一邻居则累加。
        种子自身保留全分。最终按分数降序，原 seed 顺序兜底。
        """
        DECAY = 0.5
        score = defaultdict(float)
        for i, s in enumerate(seed):
            score[s] += base_scores.get(s, 1.0 / (i + 1))  # 缺省按 rank 给分
        for s in seed:
            base = base_scores.get(s, 1.0)
            for nb in self.neighbors(s):
                score[nb] += base * DECAY
        ranked = sorted(score.items(), key=lambda x: -x[1])
        out = [n for n, _ in ranked[:k]]
        for s in seed:  # 兜底补齐
            if s not in out and len(out) < k:
                out.append(s)
        return out[:k]


# ---- cognee-lite：实体级共现图 --------------------------------------------

_STOP = set("的了和与及在是有为对从把被到这那个我们你他她它"
            "啊呢吗吧呀么嘛哦哈一二三四五六七八九十多少几怎么什么哪如何是否")


def extract_entities(text: str, title: str) -> set:
    """从文档抽实体：owner + 服务/接口名 + 全大写常量 + 标题中文双字词。

    纯启发式、零 LLM、零外部依赖。目的是复刻 Cognee"实体节点"的召回信号，
    而非追求 NER 精度。
    """
    ents: set = set()
    head = text[:4000]  # 头部信息密度高、乱码少
    ents.update(m.lower() for m in _OWNER_RE.findall(head))
    ents.update(m.lower() for m in _SERVICE_RE.findall(text)[:200])
    ents.update(m.lower() for m in _CONST_RE.findall(text)[:200])
    # 标题里的中文 2-gram（领域名词），如"特效弹幕""压测告警"
    han = _HAN.findall(title)
    for i in range(len(han) - 1):
        bg = han[i] + han[i + 1]
        if han[i] not in _STOP and han[i + 1] not in _STOP:
            ents.add(bg)
    return ents


class EntityGraph:
    """实体级图谱：实体 → 包含它的文档（倒排）；实体共现 → 边（用于 1-hop 扩展）。"""

    def __init__(self):
        self.doc_ents: dict[str, set] = {}        # name -> {entity}
        self.ent_docs: dict[str, set] = defaultdict(set)  # entity -> {name}
        self.names: list[str] = []

    def build(self, kb_dir: str):
        kb = Path(kb_dir)
        files = sorted(f for f in kb.rglob("*.md") if f.is_file())
        for f in files:
            text = _read(f)
            if not text:
                continue
            h = parse_header(text)
            ents = extract_entities(text, h["title"] or f.stem)
            self.doc_ents[f.name] = ents
            self.names.append(f.name)
            for e in ents:
                self.ent_docs[e].add(f.name)
        return self

    def query_entities(self, query: str, llm_keywords: list[str]) -> set:
        """问题侧实体 = LLM 关键词（复用现行策略调用）映射到实体空间 + 问题自身抽取。"""
        q_ents: set = set()
        for kw in llm_keywords:
            q_ents |= extract_entities(kw, kw)
        q_ents |= extract_entities(query, query)
        # 只保留索引里真实存在的实体，避免噪声
        return {e for e in q_ents if e in self.ent_docs}

    def topk(self, query: str, llm_keywords: list[str], k: int = 10) -> list[str]:
        """实体匹配打分 + 1-hop 共现扩展。

        打分：文档命中查询实体的 IDF 加权和（稀有实体权重高）。
        1-hop：查询实体的共现实体（同现于命中文档）带少量传播分，模拟图谱邻接召回。
        """
        import math
        q_ents = self.query_entities(query, llm_keywords)
        if not q_ents:
            return []
        n = max(len(self.names), 1)
        score = defaultdict(float)
        # 直接命中
        cooccur: Counter = Counter()
        for e in q_ents:
            docs = self.ent_docs.get(e, set())
            idf = math.log((n + 1) / (len(docs) + 1)) + 0.5
            for d in docs:
                score[d] += idf
                cooccur.update(self.doc_ents.get(d, set()))  # 收集共现实体
        # 1-hop：高共现实体（图邻接）带衰减传播
        for e, _c in cooccur.most_common(20):
            if e in q_ents:
                continue
            docs = self.ent_docs.get(e, set())
            if len(docs) > n * 0.3:   # 过于普遍的实体不传播
                continue
            idf = math.log((n + 1) / (len(docs) + 1)) + 0.5
            for d in docs:
                score[d] += idf * 0.15
        ranked = sorted(score.items(), key=lambda x: (-x[1], x[0]))
        return [d for d, s in ranked[:k] if s > 0]
