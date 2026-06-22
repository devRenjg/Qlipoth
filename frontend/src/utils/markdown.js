import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({ gfm: true, breaks: true })

// 外链在新标签打开，并补全 rel 防止 reverse tabnabbing
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.tagName === 'A' && node.getAttribute('href')) {
    node.setAttribute('target', '_blank')
    node.setAttribute('rel', 'noopener noreferrer')
  }
})

// 抓取入库的文档里，图片 URL 多以裸文本嵌在 protobuf 解码残片中（非 ![]() 语法），
// 故渲染前先把内容图 URL 提取、清洗、去重，统一追加成「文档图片」区块。
// 仅识别已知图床 host 的内容图，避免把头像/乱码误当图片。
const IMG_PATTERNS = [
  // 企微/腾讯文档内容图：...?w=NNN&h=NNN&type=image/xxx（结尾即干净边界，砍掉后续乱码）
  /https?:\/\/[\w.-]*qpic\.cn\/[\w%./~-]+\?w=\d+&h=\d+&type=image\/[a-z]+/gi,
  // 显式图片扩展名（B站图床 examplecdn / 其他）
  /https?:\/\/(?:[\w-]+\.)?(?:qpic\.cn|gtimg\.cn)\/[\w%./~-]+\.(?:png|jpe?g|gif|webp|bmp)(?:@[\w_]+)?/gi,
]

function extractImages(text) {
  const urls = []
  const seen = new Set()
  for (const re of IMG_PATTERNS) {
    for (const m of text.matchAll(re)) {
      const u = m[0]
      if (!seen.has(u)) { seen.add(u); urls.push(u) }
    }
  }
  return urls
}

export function renderMarkdown(text) {
  if (!text) return ''
  const images = extractImages(text)
  let md = text
  if (images.length) {
    const block = images.map((u, i) => `![文档图片${i + 1}](${u})`).join('\n\n')
    md = `${text}\n\n## 文档图片（${images.length}）\n\n${block}\n`
  }
  const raw = marked.parse(md)
  const safe = DOMPurify.sanitize(raw, { ADD_ATTR: ['target', 'rel'] })
  return forceLinkNewTab(rewriteInfoImages(safe))
}

// 兜底确保所有链接新标签打开(不依赖 DOMPurify hook 行为)：给 <a ...href...> 强制补 target/rel。
function forceLinkNewTab(html) {
  return html.replace(/<a\b([^>]*?)>/gi, (m, attrs) => {
    if (!/\bhref=/i.test(attrs)) return m
    let a = attrs
    a = a.replace(/\s*target=(["']).*?\1/gi, '').replace(/\s*rel=(["']).*?\1/gi, '')
    return `<a${a} target="_blank" rel="noopener noreferrer">`
  })
}

// 内部 wiki 图片需要登录态，浏览器直连会被重定向到登录页而加载失败。
// 把这些图片改指向后端代理（后端带 cookie 取图后回传）。
function rewriteInfoImages(html) {
  return html.replace(
    /(<img\b[^>]*\bsrc=)(["'])(https?:\/\/wiki\.example\.com\/[^"']+)\2/gi,
    (_m, pre, q, url) => `${pre}${q}/api/documents/img-proxy?url=${encodeURIComponent(url)}${q}`
  )
}

