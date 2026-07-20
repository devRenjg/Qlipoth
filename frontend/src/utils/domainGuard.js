// IP 直连拦截：产品已迁到正式域名，非正式域名(且非本地开发)访问时整页替换为提示页。
// 纯 DOM 实现，不依赖 Vue/Element，确保在应用加载前最早生效。

// 正式域名(平台 nginx 反代入口)。若将来换域名，改这里即可。
export const OFFICIAL_HOST = 'internal.example.local'
export const OFFICIAL_URL = 'https://internal.example.local/'

// 本地开发放行(vite dev / 后端联调)，不拦截。
const LOCAL_HOSTS = ['localhost', '127.0.0.1', '0.0.0.0', '::1']

// 是否为允许访问的 Host：正式域名 或 本地开发。
export function isAllowedHost() {
  const host = window.location.hostname || ''
  if (host === OFFICIAL_HOST) return true
  if (LOCAL_HOSTS.includes(host)) return true
  return false
}

// 渲染"请访问正式域名"提示页：提示文案 + 立即前往按钮 + 30 秒倒计时自动跳转。
export function renderRedirectNotice() {
  const COUNTDOWN = 30
  document.title = '克里珀 · 已迁移至正式域名'
  document.body.innerHTML = `
    <div id="qlipoth-redirect">
      <div class="qr-card">
        <div class="qr-logo">克里珀</div>
        <div class="qr-sub">大型直播活动保障百宝箱</div>
        <div class="qr-msg">产品已正式上线，请通过正式域名访问</div>
        <a class="qr-url" href="${OFFICIAL_URL}">${OFFICIAL_URL}</a>
        <a class="qr-btn" href="${OFFICIAL_URL}">立即前往 →</a>
        <div class="qr-tip"><span id="qr-count">${COUNTDOWN}</span> 秒后自动跳转</div>
      </div>
    </div>
  `
  injectStyle()
  let left = COUNTDOWN
  const el = document.getElementById('qr-count')
  const timer = setInterval(() => {
    left -= 1
    if (el) el.textContent = String(Math.max(left, 0))
    if (left <= 0) {
      clearInterval(timer)
      window.location.href = OFFICIAL_URL
    }
  }, 1000)
}

function injectStyle() {
  const css = `
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif; }
    #qlipoth-redirect {
      min-height: 100vh; display: flex; align-items: center; justify-content: center;
      background: linear-gradient(135deg, #0b1120 0%, #1a2740 100%); color: #e6eefc;
    }
    .qr-card {
      text-align: center; padding: 56px 48px; border-radius: 16px;
      background: rgba(255,255,255,0.04); border: 1px solid rgba(120,170,255,0.18);
      box-shadow: 0 12px 48px rgba(0,0,0,0.4); max-width: 90vw;
    }
    .qr-logo { font-size: 40px; font-weight: 800; letter-spacing: 4px;
      background: linear-gradient(90deg, #6db3ff, #a98bff); -webkit-background-clip: text;
      -webkit-text-fill-color: transparent; background-clip: text; }
    .qr-sub { margin-top: 8px; font-size: 15px; color: #8aa0c4; letter-spacing: 2px; }
    .qr-msg { margin-top: 32px; font-size: 18px; color: #dbe6fb; }
    .qr-url { display: block; margin-top: 16px; font-size: 15px; color: #6db3ff;
      word-break: break-all; text-decoration: none; }
    .qr-url:hover { text-decoration: underline; }
    .qr-btn {
      display: inline-block; margin-top: 28px; padding: 12px 40px; font-size: 16px;
      font-weight: 600; color: #fff; text-decoration: none; border-radius: 999px;
      background: linear-gradient(90deg, #4a90ff, #7b6bff);
      box-shadow: 0 6px 20px rgba(74,144,255,0.4); transition: transform .15s, box-shadow .15s;
    }
    .qr-btn:hover { transform: translateY(-2px); box-shadow: 0 10px 28px rgba(74,144,255,0.55); }
    .qr-tip { margin-top: 24px; font-size: 13px; color: #7d8db0; }
    #qr-count { color: #6db3ff; font-weight: 700; }
  `
  const style = document.createElement('style')
  style.textContent = css
  document.head.appendChild(style)
}
