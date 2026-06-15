// 保障维度 —— 前端单一事实源（与后端 backend/dimensions.py 保持一致）。
// tag: 知识库真实标签名；label: 展示名；icon: 图标。顺序即此列表。改维度只动此处。

export const DIMENSION_DEFS = [
  { tag: '事故/故障', label: '事故/故障', icon: '🚨' },
  { tag: '高可用保障', label: '高可用保障', icon: '🛡️' },
  { tag: '直播体验', label: '直播播放体验', icon: '📺' },
  { tag: '成本', label: '成本', icon: '💰' },
  { tag: '安全', label: '安全', icon: '🔒' },
  { tag: '业务需求', label: '业务需求', icon: '🎯' },
]

// 展示名列表（保障清单分类/排序用）
export const DIMENSION_LABELS = DIMENSION_DEFS.map(d => d.label)
// 标签名 → 图标（作战地图按标签名取图标）
export const TAG_TO_ICON = Object.fromEntries(DIMENSION_DEFS.map(d => [d.tag, d.icon]))
