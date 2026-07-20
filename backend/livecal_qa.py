"""直播日历精准问答:识别"直播场次PCU/时间/高热业务数据"类问题,
直接从 live_sessions 取精准数据,优先于知识库(query.py 里串行调用)。

设计(与用户对齐 2026-07):
- A 纯规则判断意图(零延迟零成本);
- B 命中→只用日历数据回答(严格串行);
- C 查不到→返回 None,由调用方回退知识库。
"""
import re
import aiosqlite
from database import DB_PATH

# ---- 意图识别:三类信号(数据指标 / 场次时间 / 活动实体),命中即视为日历问题 ----
# 指标类:PCU、在线人数、观看时长、弹幕、涨粉、进房/DAU、预约
_METRIC_RE = re.compile(
    r'PCU|pcu|在线人数|在线峰值|峰值|同时在线|观看时长|观看时间|弹幕|涨粉|新增粉丝|'
    r'进房|入房|DAU|dau|预约|预约人数|热度|观看人数|人气', re.I)
# 时间类:什么时候/哪天/几号/日期/时间(问场次发生时间)
_TIME_RE = re.compile(r'什么时候|哪天|哪一天|几号|几月|日期|时间是|多会|啥时候')
# 场次/直播实体类
_SESSION_RE = re.compile(
    r'场次|直播|开播|赛事|比赛|决赛|半决赛|总决赛|晚会|跨年|跨晚|春晚|阅兵|'
    r'演唱会|前瞻|S\d{1,2}\b|MSI|LPL|总决赛', re.I)
# 明确的活动/主体名(强信号,单独出现也算)
_ENTITY_RE = re.compile(
    r'跨年晚会|跨晚|春晚|春节联欢|阅兵|最美的夜|拜年纪|'
    r'S1[0-9]\b|S[0-9]\b|MSI|英雄联盟|LPL|LCK|世界赛|总决赛|季中赛|'
    r'原神|明日方舟|王者荣耀|无畏契约|CSGO|吃鸡|第五人格|守望先锋|刀塔|'
    r'影视飓风|哔哩哔哩晚会', re.I)


def detect_livecal_intent(q: str) -> bool:
    """纯规则判断:是否为直播日历数据类问题。
    命中条件:(指标|时间) + (场次|实体)  或  单独出现强实体+指标/时间语境。
    """
    q = q or ""
    has_metric = bool(_METRIC_RE.search(q))
    has_time = bool(_TIME_RE.search(q))
    has_session = bool(_SESSION_RE.search(q))
    has_entity = bool(_ENTITY_RE.search(q))
    # 组合1:问指标 且 提到了场次/活动 → 典型"S14决赛PCU多少"
    if has_metric and (has_session or has_entity):
        return True
    # 组合2:问时间 且 提到场次/活动 → "2024跨晚什么时候直播"
    if has_time and (has_session or has_entity):
        return True
    return False


# ---- 取数:从问题抽取检索线索,查 live_sessions ----
# 活动关键词 → 标题/主播名 LIKE 片段(一个活动可能多个候选词)
_ACTIVITY_KEYWORDS = {
    "跨年": ["跨年", "最美的夜", "跨晚"],
    "春晚": ["春节联欢", "春晚"],
    "阅兵": ["阅兵", "抗战", "正义必胜"],
    "拜年纪": ["拜年纪"],
    "原神": ["原神"],
    "明日方舟": ["明日方舟"],
}


# S赛(英雄联盟全球总决赛)届次 → 举办年份。用于"S14决赛"这类无年份但有赛事标识的问题。
_S_SEASON_YEAR = {
    "S10": "2020", "S11": "2021", "S12": "2022", "S13": "2023",
    "S14": "2024", "S15": "2025", "S16": "2026",
}


# 英雄联盟官方赛事直播间。S赛/MSI/LPL/LCK 等官方赛事均在此间开播,间名 2022-2026 稳定(已核实)。
# 命中 LoL 赛事问题时把查询锁定到此间,排除民间解说/转播间——后者标题也含"S15"等赛事标识,
# 曾把 50万级转播场排到官方 499万决赛场前面,导致"S15最高PCU"答错(答成"老实憨厚的笑")。
_LOL_ESPORTS_ANCHOR = "哔哩哔哩英雄联盟赛事"
# LoL 赛事强信号:英雄联盟/LPL/LCK/MSI 或 S届次(S10-S16)
_LOL_ESPORTS_RE = re.compile(r'英雄联盟|LPL|LCK|MSI|(?<![A-Za-z0-9])S1[0-6](?![0-9])', re.I)


def _is_lol_esports_q(q: str) -> bool:
    """问题是否明确指向英雄联盟官方赛事(S赛/MSI/LPL/LCK)。"""
    return bool(_LOL_ESPORTS_RE.search(q or ""))


def _extract_years(q: str) -> list[str]:
    """抽取问题里的年份(2020-2029);并把 S赛届次映射成对应年份补入。"""
    years = re.findall(r'20[2-9]\d', q or "")
    # S赛届次:S后跟1-2位数字,前边界用(?<![A-Za-z0-9])避免误匹配,后边界不强制(常接中文)
    for m in re.findall(r'(?<![A-Za-z0-9])S(1[0-6]|[1-9])(?![0-9])', q or "", re.I):
        key = f"S{m}"
        y = _S_SEASON_YEAR.get(key)
        if y and y not in years:
            years.append(y)
    return years


def _extract_like_terms(q: str) -> list[str]:
    """从问题抽取用于 title/anchor LIKE 的检索词:活动名、战队缩写、赛事标识。"""
    q = q or ""
    terms: list[str] = []
    # 已知活动关键词
    for _, kws in _ACTIVITY_KEYWORDS.items():
        for kw in kws:
            if kw in q:
                terms.extend(kws)
                break
    # 赛事标识:MSI/LPL/LCK 作为 LIKE 词;但 S数字(S10-S16)已在 _extract_years 映射成年份,
    # 不作 LIKE(官方赛事场次标题是"总决赛 队 比分",不含"S14"字样,LIKE 反而会漏掉官方场次)。
    for m in re.findall(r'MSI|LPL|LCK', q, re.I):
        terms.append(m.upper())
    # 战队大写缩写(2-4位字母,如 T1/EDG/BLG/RNG/JDG),排除纯英文常用词
    for m in re.findall(r'\b[A-Z0-9]{2,4}\b', q):
        # S届次(S10-S16)已在 _extract_years 映射成年份,绝不能当 LIKE 词:官方赛事场标题是
        # "决赛 KT 2:3 T1"不含"S15"字样,LIKE '%S15%' 会把官方场滤掉、只留民间转播间(标题带S15)。
        if re.fullmatch(r'S\d{1,2}', m, re.I):
            continue
        if m.upper() not in ("PCU", "DAU", "MSI", "LPL", "LCK"):
            terms.append(m)
    # T1 特殊(数字结尾,后常接中文,不用 \b 尾边界)
    if re.search(r'(?<![A-Za-z0-9])T1(?![0-9])', q, re.I):
        terms.append("T1")
    # 去重保序
    seen, out = set(), []
    for t in terms:
        if t not in seen:
            seen.add(t); out.append(t)
    return out


def _fmt_wan(v) -> str:
    """整数转“万”显示(PCU/进房等大数)。"""
    if v is None:
        return "—"
    try:
        return f"{int(v)/10000:.1f}万" if int(v) >= 10000 else str(int(v))
    except (ValueError, TypeError):
        return str(v)


def _fmt_session(r) -> str:
    """把一条 live_sessions 记录格式化成一行精准数据文本。"""
    d = str(r["session_time"])[:10]
    parts = [f"{d}", f"《{r['title']}》", f"直播间:{r['anchor_name'] or r['room_id'] or '—'}"]
    if r["pcu"]:
        parts.append(f"PCU峰值:{_fmt_wan(r['pcu'])}")
    if r["reservation"]:
        parts.append(f"预约:{_fmt_wan(r['reservation'])}")
    # 高热业务数据(有才带)
    biz = []
    if r["watch_hours_all"]:
        biz.append(f"观看时长(全端):{r['watch_hours_all']:.0f}h")
    if r["danmu_all"]:
        biz.append(f"弹幕(全端):{r['danmu_all']}")
    if r["enter_dau_all"]:
        biz.append(f"进房DAU(全端):{_fmt_wan(r['enter_dau_all'])}")
    if r["fans_growth_all"]:
        biz.append(f"涨粉(全端):{r['fans_growth_all']}")
    if biz:
        parts.append("；".join(biz))
    if r["is_dirty"]:
        parts.append(f"⚠️脏数据({r['dirty_note'] or '异常值,仅供参考'})")
    return " | ".join(parts)


async def fetch_livecal_context(q: str, limit: int = 15) -> str | None:
    """命中日历意图时,从 live_sessions 取精准数据,返回喂给 LLM 的 context 文本。
    查不到相关场次 → 返回 None(调用方回退知识库)。
    """
    years = _extract_years(q)
    terms = _extract_like_terms(q)
    lol_esports = _is_lol_esports_q(q)

    where = ["pcu IS NOT NULL", "pcu > 0", "title NOT LIKE '%预告%'", "COALESCE(hidden, 0) = 0"]
    params: list = []
    # LoL 官方赛事问题 → 锁定官方赛事间,排除民间解说/转播间(它们标题也带 S15 等标识,会抢榜首)
    if lol_esports:
        where.append("anchor_name = ?")
        params.append(_LOL_ESPORTS_ANCHOR)
    # 年份过滤(任一年份)
    if years:
        ors = []
        for y in years:
            ors.append("session_time >= ? AND session_time < ?")
            params += [f"{y}-01-01", f"{int(y)+1}-01-01"]
        where.append("(" + " OR ".join(ors) + ")")
    # 关键词过滤(title 或 anchor 命中任一词)
    if terms:
        ors = []
        for t in terms:
            ors.append("title LIKE ? OR anchor_name LIKE ?")
            params += [f"%{t}%", f"%{t}%"]
        where.append("(" + " OR ".join(ors) + ")")

    # 既无年份也无关键词也非LoL赛事 → 线索不足,不强查(交回知识库更稳)
    if not years and not terms and not lol_esports:
        return None

    sql = (f"SELECT session_time,title,anchor_name,pcu,reservation,room_id,"
           f"watch_hours_all,danmu_all,enter_dau_all,fans_growth_all,is_dirty,dirty_note "
           f"FROM live_sessions WHERE {' AND '.join(where)} "
           f"ORDER BY pcu DESC LIMIT {int(limit)}")

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = await (await db.execute(sql, params)).fetchall()

    if not rows:
        return None

    lines = [_fmt_session(r) for r in rows]
    header = ("以下为【直播日历】数据库的精准场次数据(PCU=直播间同时在线峰值,"
              "数据来自技术侧真实统计,按PCU降序)。请仅依据这些数据回答,"
              "不要编造未列出的数字;若问题涉及的场次不在下列数据中,如实说明日历中暂无:")
    return header + "\n" + "\n".join(f"- {ln}" for ln in lines)
