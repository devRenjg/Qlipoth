// 标签配色：按标签名给出语义代表色，首页与文档管理页共用同一份，保证全站一致。
// 基于标签语义的固定配色：安全=危险红、红包=喜庆深红、弹幕=欢快绿……
const TAG_COLORS = {
  '安全': '#e63946',        // 危险/重要 → 红
  '红包': '#c0392b',        // 喜庆 → 深红
  '弹幕': '#27ae60',        // 欢快 → 绿
  '高可用保障': '#2f6fed',  // 稳定可靠 → 蓝
  '业务需求': '#8e44ad',    // 业务 → 紫
  '直播体验': '#e67e22',    // 体验/暖 → 橙
  '成本': '#d4a017',        // 金钱 → 金黄
  '模板与名单': '#16a3a3',  // 规整 → 青
  '项目管理': '#5c6bc0',    // 管理 → 靛蓝
  '接口与配置': '#607d8b',  // 技术 → 蓝灰
}

function hexToRgb(hex) {
  const h = hex.replace('#', '')
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ]
}

// 未知标签：用名称哈希生成稳定色相，保证同名同色
export function colorForTag(name) {
  if (TAG_COLORS[name]) return TAG_COLORS[name]
  let hash = 0
  for (let i = 0; i < name.length; i++) hash = (hash * 31 + name.charCodeAt(i)) >>> 0
  const hue = hash % 360
  return `hsl(${hue}, 55%, 45%)`
}

// 标签胶囊样式：selected=实色填充白字；未选中=浅色底 + 同色描边/文字
export function tagChipStyle(name, selected = false) {
  const color = colorForTag(name)
  if (selected) {
    return { background: color, borderColor: color, color: '#fff' }
  }
  if (color.startsWith('#')) {
    const [r, g, b] = hexToRgb(color)
    return {
      background: `rgba(${r}, ${g}, ${b}, 0.10)`,
      borderColor: `rgba(${r}, ${g}, ${b}, 0.45)`,
      color,
    }
  }
  const tint = color.replace(')', ', 0.10)').replace('hsl', 'hsla')
  const ring = color.replace(')', ', 0.45)').replace('hsl', 'hsla')
  return { background: tint, borderColor: ring, color }
}
