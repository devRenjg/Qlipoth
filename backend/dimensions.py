"""保障维度 —— 单一事实源（权威定义）。

两个语义层：
- `tag`：知识库真实标签名（作战地图按此查文档，必须与 tags 表一致）
- `label`：展示名（保障清单分类名 / UI 显示，可比 tag 更完整）

顺序即此列表顺序。改维度只动这一处。前端对应 frontend/src/dimensions.js（保持一致）。
"""

# (tag, label, icon)
DIMENSION_DEFS = [
    ("事故/故障", "事故/故障", "🚨"),
    ("高可用保障", "高可用保障", "🛡️"),
    ("直播体验", "直播播放体验", "📺"),
    ("成本", "成本", "💰"),
    ("安全", "安全", "🔒"),
    ("业务需求", "业务需求", "🎯"),
]

# 标签名列表（作战地图按标签查文档用）
DIMENSION_TAGS = [d[0] for d in DIMENSION_DEFS]
# 展示名列表（保障清单分类 / 排序用）
DIMENSION_LABELS = [d[1] for d in DIMENSION_DEFS]
# 标签名 → 展示名
TAG_TO_LABEL = {d[0]: d[1] for d in DIMENSION_DEFS}
# 标签名 → 图标
TAG_TO_ICON = {d[0]: d[2] for d in DIMENSION_DEFS}
