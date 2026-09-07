/* ============================================
   ctyun Web Panel - Frontend JavaScript
   ============================================ */

const API = {
    getStatus: '/api/status',
    getSettings: '/api/settings',
    saveSettings: '/api/settings',
    testLogin: '/api/test-login',
    clearSession: '/api/clear-session',
    smsCode: '/api/sms-code',
    savePresets: '/api/presets',
    getDeviceCode: '/api/device-code',
    regenerateDeviceCode: '/api/device-code/regenerate',
    saveDeviceCode: '/api/device-code',
    getCron: '/api/cron',
    saveCron: '/api/cron',
    getRedeem: '/api/redeem',
    saveRedeem: '/api/redeem',
    disableRedeem: '/api/redeem/disable',
    getRewards: '/api/rewards',
    fetchRewards: '/api/rewards/fetch',
    executeTask: '/api/task',
    getLogs: '/api/logs',
    clearLogs: '/api/logs/clear',
    logsDownload: '/api/logs/download',
    restart: '/api/restart',
    pointsHistory: '/api/points-history',
    webSettings: '/api/web-settings',
    testNotify: '/api/test-notify',
    pendingNotifies: '/api/pending-notifies',
    // 面板访问鉴权（2026-09-07 默认账号模式）
    authCheck: '/api/auth/check',
    authLogin: '/api/auth/login',
    authChangePassword: '/api/auth/change-password',
    authToggle: '/api/auth/toggle',
    authStatus: '/api/auth/status'
};

// ============================================
// 面板访问鉴权（2026-09-07 默认账号模式）
// token 存 localStorage，随 X-Auth-Token 头提交；401 时清除并弹回登录页
// 默认账号 admin/admin，登录后在设置页修改用户名/密码
// ============================================
const AUTH_TOKEN_KEY = 'ctyun_panel_token';
function getPanelToken() { return localStorage.getItem(AUTH_TOKEN_KEY) || ''; }
function setPanelToken(tok) {
    if (tok) localStorage.setItem(AUTH_TOKEN_KEY, tok);
    else localStorage.removeItem(AUTH_TOKEN_KEY);
}
// 桌面版标记：/api/auth/check 返回 desktop_mode=true 后置位，
// 面板安全卡、相关提示均按"无需登录"处理
let PANEL_DESKTOP_MODE = false;

// 进入主界面（隐藏登录遮罩、启动数据加载）
function enterPanel() {
    const overlay = document.getElementById('panelAuthOverlay');
    if (overlay) {
        overlay.style.display = 'none';
        overlay.classList.remove('show');
    }
    // 主界面数据加载：原 DOMContentLoaded 逻辑在未登录时也会跑，
    // 登录成功后这里补一次完整加载
    refreshStatus();
    loadAccountSettings();
    loadNotifySettings();
    loadCronSettings();
    loadRedeemConfig();
    loadCachedRewards();
    loadPanelAuthSettings();
}

// 面板鉴权初始化：判断是否需要登录
// 2026-09-07 默认账号模式：去掉初始化表单分支（后端已内置 admin/admin）
async function initPanelAuth() {
    const overlay = document.getElementById('panelAuthOverlay');
    const sub = document.getElementById('panelAuthSub');
    const loginForm = document.getElementById('panelAuthLoginForm');
    const errEl = document.getElementById('panelAuthError');
    let check;
    try {
        check = await apiRequest(API.authCheck);
    } catch (e) {
        // 检查失败（后端未就绪）：短暂延迟重试一次，仍失败则放行主界面由轮询报错
        await new Promise(r => setTimeout(r, 1200));
        try { check = await apiRequest(API.authCheck); } catch (e2) { return; }
    }
    PANEL_DESKTOP_MODE = !!check.desktop_mode;
    if (!check.auth_required || check.authenticated) {
        // 无需登录：直接进入（遮罩保持隐藏）
        return;
    }
    // 需要登录：展示遮罩（防闪烁：数据加载由 enterPanel 登录后再触发）
    if (overlay) {
        overlay.style.display = 'flex';
        requestAnimationFrame(() => overlay.classList.add('show'));
    }
    if (sub) sub.textContent = '请输入面板账号密码继续（默认 admin / admin）';
    if (loginForm) loginForm.style.display = 'flex';
    const first = document.getElementById('panelAuthUser');
    if (first) setTimeout(() => first.focus(), 200);
    if (errEl) errEl.textContent = '';
}

// 面板登录表单提交（用户名+密码，默认 admin/admin）
async function handlePanelAuthLogin(e) {
    e.preventDefault();
    const userEl = document.getElementById('panelAuthUser');
    const pwdEl = document.getElementById('panelAuthPwd');
    const btn = document.getElementById('panelAuthLoginBtn');
    const errEl = document.getElementById('panelAuthError');
    const username = (userEl.value || '').trim();
    const pwd = (pwdEl.value || '').trim();
    if (!username || !pwd) { if (errEl) errEl.textContent = '请输入用户名和密码'; return; }
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> 验证中…';
    try {
        const r = await apiRequest(API.authLogin, 'POST', { username: username, password: pwd });
        setPanelToken(r.token || '');
        showToast('面板登录成功', 'success');
        enterPanel();
    } catch (error) {
        if (errEl) errEl.textContent = error.message || '登录失败';
        pwdEl.value = '';
        pwdEl.focus();
    } finally {
        btn.disabled = false;
        btn.innerHTML = '进入控制台';
    }
}

// Current state
let currentLogType = 'all';
let currentPage = 'dashboard';

// ============================================
// Theme & Appearance Management
// 支持多套主题预设（浅蓝/浅暖/暗夜/暗紫/暗绿）、自定义强调色、
// 3D 模式、自定义壁纸、动态光斑。全部持久化到 localStorage。
// ============================================
const APPEARANCE_KEY = 'ctyun_appearance';
const THEME_LIST = ['light', 'light-warm', 'dark', 'dark-purple', 'dark-green'];
const GRADIENT_WALLPAPERS = [
  'linear-gradient(135deg,#1e3a8a,#7c3aed)',
  'linear-gradient(135deg,#0f766e,#22d3ee)',
  'linear-gradient(135deg,#831843,#db2777)',
  'linear-gradient(135deg,#1f2937,#111827)',
  'linear-gradient(135deg,#b45309,#f59e0b)',
  'linear-gradient(135deg,#155e75,#0ea5e9)'
];

function getAppearance() {
  try { return JSON.parse(localStorage.getItem(APPEARANCE_KEY)) || {}; }
  catch (e) { return {}; }
}
function setAppearance(obj) {
  const cur = getAppearance();
  const next = Object.assign(cur, obj);
  localStorage.setItem(APPEARANCE_KEY, JSON.stringify(next));
  return next;
}

// 应用外观（主题/强调色/3D/壁纸/光斑）
function applyAppearance() {
  const a = getAppearance();
  const html = document.documentElement;
  const body = document.body;

  // 1) 主题预设
  const theme = THEME_LIST.includes(a.theme) ? a.theme : 'light';
  html.setAttribute('data-theme', theme);

  // 2) 自定义强调色
  if (a.accent && /^#[0-9a-fA-F]{6}$/.test(a.accent)) {
    html.setAttribute('data-accent', '1');
    const c = a.accent;
    const hover = shadeColor(c, 28);
    const soft = hexToRgba(c, 0.16);
    const glow = `0 0 0 1px ${hexToRgba(c, 0.32)}`;
    html.style.setProperty('--data-accent', c);
    html.style.setProperty('--data-accent-hover', hover);
    html.style.setProperty('--data-accent-soft', soft);
    html.style.setProperty('--data-accent-glow', glow);
  } else {
    html.removeAttribute('data-accent');
    html.style.removeProperty('--data-accent');
    html.style.removeProperty('--data-accent-hover');
    html.style.removeProperty('--data-accent-soft');
    html.style.removeProperty('--data-accent-glow');
  }

  // 3) 3D 模式
  body.classList.toggle('mode-3d', !!a.mode3d);
  syncEl('mode3d', !!a.mode3d);
  syncEl('mode3dMini', !!a.mode3d);

  // 4) 动态光斑
  body.classList.toggle('spotlight-anim', !!a.spotAnim);
  if (a.spotSpeed) html.setAttribute('data-spot-speed', a.spotSpeed); else html.removeAttribute('data-spot-speed');
  if (a.spotDensity) html.setAttribute('data-spot-density', a.spotDensity); else html.removeAttribute('data-spot-density');
  syncEl('spotAnim', !!a.spotAnim);
  syncEl('spotAnimMini', !!a.spotAnim);
  syncEl('spotSpeed', a.spotSpeed || 'normal');
  syncEl('spotDensity', a.spotDensity || 'normal');

  // 5) 壁纸
  applyWallpaper();
  syncWallpaperTab();

  // 高亮当前预设选择
  markActivePreset(theme);
}

// 壁纸滑杆/下拉输入时先保存当前值（oninput/onchange 调用）
function setWpFromSlider(el) {
  if (!el) return;
  const id = el.id;
  const val = (el.type === 'range') ? (parseInt(el.value, 10) || 0) : el.value;
  if (id === 'wallpaperOpacity') setAppearance({ wallpaperOpacity: val });
  else if (id === 'wallpaperMask') setAppearance({ wallpaperMask: val });
  else if (id === 'wallpaperBlend') setAppearance({ wallpaperBlend: val });
}

function applyWallpaper() {
  const a = getAppearance();
  const body = document.body;
  // 壁纸现在由独立的 .wallpaper-layer 固定层承载（见 index.html / style.css），
  // 不再使用 body 的 background-attachment: fixed（滚动跳动根因），这里只负责开关类名。
  body.classList.remove('wallpaper-active');
  body.style.removeProperty('--wallpaper');
  body.style.removeProperty('--wallpaper-overlay');
  body.style.removeProperty('--wp-fade');
  if (!a.wallpaper) return;
  body.style.setProperty('--wallpaper', a.wallpaper);
  // 壁纸透明度（0-70 → 0~0.7）：越大壁纸越淡。
  // 变量挂在 body 上，由 .wallpaper-layer::after 白/深色覆盖层消费（原 body 混合层实现从未生效）
  const fade = parseInt(a.wallpaperOpacity != null ? a.wallpaperOpacity : 0, 10);
  body.style.setProperty('--wp-fade', (fade / 100).toFixed(3));
  // 蒙版强度（0-100）：深色遮罩增强文字可读，越强越深
  const mask = parseInt(a.wallpaperMask != null ? a.wallpaperMask : 20, 10);
  const blend = a.wallpaperBlend || 'soft';
  let overlay;
  if (blend === 'strong') overlay = `rgba(8,12,20,${(0.30 + mask / 140).toFixed(3)})`;
  else if (blend === 'normal') overlay = `rgba(12,16,24,${(0.20 + mask / 160).toFixed(3)})`;
  else overlay = `rgba(12,16,24,${(0.08 + mask / 200).toFixed(3)})`; // soft：轻遮罩，保留通透
  body.style.setProperty('--wallpaper-overlay', overlay);
  body.classList.add('wallpaper-active');
  syncEl('wallpaperBlend', blend);
  syncEl('wallpaperMask', mask);
  syncEl('wallpaperOpacity', fade);
}

// 根据当前生效壁纸自动选中对应 tab，保证界面状态与壁纸一致
function syncWallpaperTab() {
  const a = getAppearance();
  const w = a.wallpaper || '';
  let tab = 'none';
  if (a.wallpaperMode === 'subscribe') tab = 'subscribe';
  else if (w.startsWith('url("data:')) tab = 'upload';
  else if (w.startsWith('url(')) tab = 'url';
  else if (w.startsWith('linear-gradient')) tab = 'gradient';
  document.querySelectorAll('.wallpaper-tabs .cron-preset-btn').forEach(b => {
    const isActive = b.dataset.wpTab === tab;
    b.classList.toggle('active', isActive);
  });
  document.querySelectorAll('.wallpaper-field').forEach(f => {
    f.classList.toggle('active', f.dataset.wp === tab);
  });
}

function markActivePreset(theme) {
  document.querySelectorAll('#themePresets .theme-preset, #themePresetsMini .theme-preset').forEach(el => {
    el.classList.toggle('active', el.dataset.theme === theme);
  });
}

function syncEl(id, val) {
  const el = document.getElementById(id);
  if (!el) return;
  if (el.type === 'checkbox') el.checked = !!val;
  else el.value = val;
}

// 颜色工具
function hexToRgba(hex, alpha) {
  const h = hex.replace('#', '');
  const r = parseInt(h.substring(0, 2), 16);
  const g = parseInt(h.substring(2, 4), 16);
  const b = parseInt(h.substring(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}
function shadeColor(hex, amt) {
  const h = hex.replace('#', '');
  let r = parseInt(h.substring(0, 2), 16) + amt;
  let g = parseInt(h.substring(2, 4), 16) + amt;
  let b = parseInt(h.substring(4, 6), 16) + amt;
  r = Math.max(0, Math.min(255, r)); g = Math.max(0, Math.min(255, g)); b = Math.max(0, Math.min(255, b));
  return '#' + [r, g, b].map(x => x.toString(16).padStart(2, '0')).join('');
}

function initTheme() {
  applyAppearance();
  bindAppearanceUI();
  bind3DMouse();
  // ESC 关闭主题弹窗
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') closeThemePanel();
  });
}

// ---- 外观面板与控件绑定 ----
function openThemePanel() { const p = document.getElementById('themePanel'); if (p) p.classList.add('open'); }
function closeThemePanel() { const p = document.getElementById('themePanel'); if (p) p.classList.remove('open'); }

function bindAppearanceUI() {
  // 主题预设点击（面板+卡片）
  document.querySelectorAll('#themePresets .theme-preset, #themePresetsMini .theme-preset').forEach(el => {
    el.addEventListener('click', () => {
      setAppearance({ theme: el.dataset.theme });
      applyAppearance();
    });
  });
  // 强调色输入
  const acc = document.getElementById('accentColor');
  if (acc) acc.addEventListener('input', e => onAccentInput(e.target.value));
  document.querySelectorAll('#accentPresets .accent-dot').forEach(dot => {
    dot.addEventListener('click', () => {
      const c = dot.dataset.c;
      setAppearance({ accent: c });
      applyAppearance();
      const a = getAppearance();
      if (a.accent === c) document.getElementById('accentColor').value = c;
      document.getElementById('accentHex').textContent = c;
    });
  });
  // 3D
  const m3 = document.getElementById('mode3d');
  if (m3) m3.addEventListener('change', applyMode3d);
  // 壁纸 tab 切换
  document.querySelectorAll('.wallpaper-tabs .cron-preset-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.wallpaper-tabs .cron-preset-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('.wallpaper-field').forEach(f => f.classList.remove('active'));
      btn.classList.add('active');
      const tab = btn.dataset.wpTab;
      const field = document.querySelector(`.wallpaper-field[data-wp="${tab}"]`);
      if (field) field.classList.add('active');
      if (tab === 'none') { setAppearance({ wallpaper: '', wallpaperMode: 'none' }); applyAppearance(); }
      if (tab === 'subscribe') {
        renderWallpaperSources();
        // 有启用源且当前不是订阅壁纸时，自动换一张（让配置可见生效）
        const hasOn = getWallpaperSources().some(s => s.enabled);
        if (hasOn && getAppearance().wallpaperMode !== 'subscribe') refreshWallpaper();
      }
    });
  });
  // 初始化订阅源列表
  renderWallpaperSources();
  // 订阅源启用时，页面加载后自动刷新一张壁纸
  const _enabledSrcs = getWallpaperSources().filter(s => s.enabled);
  if (_enabledSrcs.length && getAppearance().wallpaperMode === 'subscribe') {
    refreshWallpaper();
  }
  // 渐变壁纸 chips
  const gradBox = document.getElementById('wallpaperGradients');
  if (gradBox && gradBox.children.length === 0) {
    GRADIENT_WALLPAPERS.forEach(g => {
      const c = document.createElement('span');
      c.className = 'grad-chip';
      c.style.background = g;
      c.addEventListener('click', () => {
        document.querySelectorAll('.grad-chip').forEach(x => x.classList.remove('active'));
        c.classList.add('active');
        setAppearance({ wallpaper: g, wallpaperMode: 'gradient' });
        applyAppearance();
      });
      gradBox.appendChild(c);
    });
  }
  // 动态光斑（壁纸融合方式/滑杆已在 HTML oninput 中绑定 setWpFromSlider+applyWallpaper）
  const sa = document.getElementById('spotAnim');
  if (sa) sa.addEventListener('change', applySpotlight);
  // 初始化强调色显示
  const a = getAppearance();
  if (a.accent) { document.getElementById('accentColor').value = a.accent; document.getElementById('accentHex').textContent = a.accent; }
}

function onAccentInput(val) {
  if (!/^#[0-9a-fA-F]{6}$/.test(val)) return;
  setAppearance({ accent: val });
  applyAppearance();
  document.getElementById('accentHex').textContent = val;
  document.querySelectorAll('#accentPresets .accent-dot').forEach(d => d.classList.toggle('active', d.dataset.c === val));
}
function clearAccent() {
  const a = getAppearance(); delete a.accent;
  setAppearance(a);
  applyAppearance();
  document.getElementById('accentHex').textContent = '未设置';
  document.getElementById('accentColor').value = '#2f7bff';
  document.querySelectorAll('#accentPresets .accent-dot').forEach(d => d.classList.remove('active'));
}
function applyMode3d() {
  const v = document.getElementById('mode3d').checked;
  setAppearance({ mode3d: v });
  applyAppearance();
}
function applySpotlight() {
  const a = {};
  a.spotAnim = document.getElementById('spotAnim').checked;
  a.spotSpeed = document.getElementById('spotSpeed').value;
  a.spotDensity = document.getElementById('spotDensity').value;
  setAppearance(a);
  applyAppearance();
}
function onWallpaperUpload(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {
    setAppearance({ wallpaper: `url("${e.target.result}")`, wallpaperMode: 'upload' });
    applyAppearance();
  };
  reader.readAsDataURL(file);
}
function onWallpaperUrl(val) {
  if (!val) { setAppearance({ wallpaper: '', wallpaperMode: 'none' }); applyAppearance(); return; }
  setAppearance({ wallpaper: `url("${val}")`, wallpaperMode: 'url' });
  applyAppearance();
}

// ============================================
// 壁纸订阅源（微软 4K / Spotlight / Picsum / Unsplash / 自定义）
// 用户可自由勾选订阅，系统从已启用源中随机取一张高质量壁纸；支持自定义接口。
// ============================================
const WP_SOURCES_KEY = 'ctyun_wp_sources';
const DEFAULT_WP_SOURCES = [
  { id: 'bing', name: '必应每日 4K（微软官方）', type: 'bing', enabled: true },
  { id: 'spotlight', name: '微软 Spotlight 精选', type: 'spotlight', enabled: false },
  { id: 'picsum', name: 'Picsum 随机高清', type: 'picsum', enabled: false },
  { id: 'unsplash', name: 'Unsplash 主题', type: 'unsplash', query: 'nature', enabled: false },
  { id: 'custom', name: '自定义接口 / 直链', type: 'custom', url: '', enabled: false },
];

function getWallpaperSources() {
  try {
    const arr = JSON.parse(localStorage.getItem(WP_SOURCES_KEY));
    if (Array.isArray(arr) && arr.length) return arr;
  } catch (e) {}
  return JSON.parse(JSON.stringify(DEFAULT_WP_SOURCES));
}
function setWallpaperSources(arr) {
  localStorage.setItem(WP_SOURCES_KEY, JSON.stringify(arr));
}
function saveWallpaperSourceField(id, field, val) {
  const arr = getWallpaperSources();
  const s = arr.find(x => x.id === id);
  if (s) { s[field] = val; setWallpaperSources(arr); }
}

// 从单个源获取一张图片 URL（返回 Promise<string>）
// 优先走后端代理（服务器取图避开浏览器 CORS），失败时回退直连
async function fetchWallpaperFromSource(src) {
  if (src.type === 'bing') {
    try {
      const r = await fetch('/api/wallpaper/random?source=bing');
      const j = await r.json();
      if (j.ok && j.url) return j.url;
      throw new Error(j.msg || 'bing 代理失败');
    } catch (e) {
      // 回退：直接请求 www.bing.com（bing.bing.com 会 301 到 www.bing.com 导致 CORS 失败）
      const r = await fetch('https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=zh-CN');
      const j = await r.json();
      let u = j.images[0].url;
      // 替换为 4K UHD 原图（微软官方支持 _UHD.jpg）
      u = u.replace(/_\d+x\d+\.jpg/i, '_UHD.jpg').replace(/&amp;/g, '&');
      if (u.startsWith('//')) u = 'https:' + u;
      if (u.startsWith('/')) u = 'https://www.bing.com' + u;
      return u;
    }
  }
  if (src.type === 'spotlight') {
    try {
      const r = await fetch('/api/wallpaper/random?source=spotlight');
      const j = await r.json();
      if (j.ok && j.url) return j.url;
      throw new Error(j.msg || 'spotlight 代理失败');
    } catch (e) {
      const r = await fetch('https://api.peapix.com/v2/random?type=spotlight&count=1');
      const j = await r.json();
      return j.images[0].imageUrl;
    }
  }
  if (src.type === 'picsum') {
    try {
      const r = await fetch('/api/wallpaper/random?source=picsum');
      const j = await r.json();
      if (j.ok && j.url) return j.url;
    } catch (e) {}
    return 'https://picsum.photos/1920/1080?random=' + Date.now();
  }
  if (src.type === 'unsplash') {
    const q = encodeURIComponent(src.query || 'nature');
    try {
      const r = await fetch('/api/wallpaper/random?source=unsplash&query=' + q);
      const j = await r.json();
      if (j.ok && j.url) return j.url;
    } catch (e) {}
    // 使用免 key 的主题随机直链（source.unsplash 已弃用，改用固定精选图库）
    return 'https://source.unsplash.com/featured/1920x1080/?' + q + '&sig=' + Math.floor(Math.random() * 99999);
  }
  if (src.type === 'custom') {
    const raw = (src.url || '').trim();
    if (!raw) throw new Error('自定义接口为空');
    // 多直链逗号分隔：随机取一张
    if (raw.includes(',')) {
      const list = raw.split(',').map(x => x.trim()).filter(Boolean);
      return list[Math.floor(Math.random() * list.length)];
    }
    // 看起来像图片直链直接返回
    if (/\.(jpg|jpeg|png|webp|gif)(\?.*)?$/i.test(raw) || raw.startsWith('data:image')) return raw;
    // 否则当 JSON 接口：取第一个含 url 字段的值
    const r = await fetch(raw + (raw.includes('?') ? '&' : '?') + '_t=' + Date.now());
    const txt = await r.text();
    let j; try { j = JSON.parse(txt); } catch (e) { return raw; }
    const pick = (o) => {
      if (typeof o === 'string' && /https?:\/\//.test(o)) return o;
      if (Array.isArray(o) && o[0]) return pick(o[0]);
      if (o && typeof o === 'object') {
        for (const k of ['url', 'src', 'image', 'imageUrl', 'link']) if (o[k]) return pick(o[k]);
      }
      return null;
    };
    const found = pick(j);
    return found || raw;
  }
  throw new Error('未知壁纸源: ' + src.type);
}

// 随机选一个已启用源取图并应用
let _wpRefreshing = false;
async function refreshWallpaper() {
  if (_wpRefreshing) return;
  _wpRefreshing = true;
  const btn = document.getElementById('wpRefreshBtn');
  if (btn) { btn.disabled = true; btn.textContent = '获取中…'; }
  try {
    const enabled = getWallpaperSources().filter(s => s.enabled);
    if (!enabled.length) { showToast('请先在「订阅源」中勾选至少一个壁纸源', 'warning'); return; }
    // 随机排序，依次尝试直到成功
    const order = enabled.sort(() => Math.random() - 0.5);
    let lastErr;
    for (const src of order) {
      try {
        const url = await fetchWallpaperFromSource(src);
        if (url) {
          // 预加载图片，确保显示时已就绪（不闪烁）
          try {
            await new Promise((resolve, reject) => {
              const img = new Image();
              img.onload = resolve;
              img.onerror = () => reject(new Error('图片加载失败'));
              img.src = url.replace(/^url\("?|"?\)$/g, '');
            });
          } catch (e) { lastErr = e; continue; }
          setAppearance({ wallpaper: `url("${url}")`, wallpaperMode: 'subscribe' });
          applyAppearance();
          showToast('壁纸已更新（' + src.name + '）', 'success');
          _wpRefreshing = false;
          if (btn) { btn.disabled = false; btn.textContent = '立即换一张'; }
          return;
        }
      } catch (e) { lastErr = e; }
    }
    showToast('壁纸获取失败：' + (lastErr ? lastErr.message : '所有源不可用'), 'error');
  } finally {
    _wpRefreshing = false;
    if (btn) { btn.disabled = false; btn.textContent = '立即换一张'; }
  }
}

// 渲染订阅源列表
function renderWallpaperSources() {
  const box = document.getElementById('wpSources');
  if (!box) return;
  const sources = getWallpaperSources();
  box.innerHTML = '';
  sources.forEach(src => {
    const row = document.createElement('div');
    row.className = 'wp-source' + (src.enabled ? ' on' : '');
    let extra = '';
    if (src.type === 'unsplash') {
      extra = `<input type="text" class="form-control form-control-sm wp-src-extra" data-id="${src.id}" data-field="query" value="${src.query || ''}" placeholder="主题关键词，如 nature/city">`;
    } else if (src.type === 'custom') {
      extra = `<input type="text" class="form-control form-control-sm wp-src-extra" data-id="${src.id}" data-field="url" value="${src.url || ''}" placeholder="图片直链，或多个直链用逗号分隔，或返回 JSON 的接口地址">`;
    }
    row.innerHTML = `
      <label class="wp-src-main">
        <input type="checkbox" class="wp-src-enable" data-id="${src.id}" ${src.enabled ? 'checked' : ''}>
        <span class="wp-src-name">${src.name}</span>
      </label>
      ${extra ? `<div class="wp-src-extra-wrap">${extra}</div>` : ''}`;
    box.appendChild(row);
  });
  box.querySelectorAll('.wp-src-enable').forEach(cb => {
    cb.addEventListener('change', () => {
      saveWallpaperSourceField(cb.dataset.id, 'enabled', cb.checked);
      renderWallpaperSources();
      // 勾选启用后立即换一张，让配置马上生效
      if (cb.checked) refreshWallpaper();
    });
  });
  box.querySelectorAll('.wp-src-extra').forEach(inp => {
    inp.addEventListener('input', () => saveWallpaperSourceField(inp.dataset.id, inp.dataset.field, inp.value));
    inp.addEventListener('change', () => {
      // 自定义/主题改动后若有启用则尝试刷新
      const s = getWallpaperSources().find(x => x.id === inp.dataset.id);
      if (s && s.enabled) refreshWallpaper();
    });
  });
}
// 折叠/展开设置卡片（点击卡片头部），状态持久化到 localStorage
function toggleCardBody(cardId) {
  const card = document.getElementById(cardId);
  if (!card) return;
  const body = card.querySelector('.card-body');
  if (!body) return;
  const collapsed = body.classList.toggle('collapsed');
  const arrow = card.querySelector('.card-fold-arrow');
  if (arrow) arrow.classList.toggle('folded', collapsed);
  try {
    const map = JSON.parse(localStorage.getItem('ctyun_card_collapse') || '{}');
    map[cardId] = collapsed;
    localStorage.setItem('ctyun_card_collapse', JSON.stringify(map));
  } catch (e) {}
}
// 恢复卡片折叠状态（默认：外观卡片收起保持紧凑，通知卡片展开；用户自定义优先）
function restoreCardCollapse() {
  try {
    const map = JSON.parse(localStorage.getItem('ctyun_card_collapse') || '{}');
    ['cardAppearance', 'cardNotify'].forEach(id => {
      const card = document.getElementById(id);
      if (!card) return;
      const body = card.querySelector('.card-body');
      const arrow = card.querySelector('.card-fold-arrow');
      // 用户已手动折叠过 → 尊重其选择；否则按默认（外观收起、通知展开）
      const isCollapsed = (map[id] !== undefined) ? map[id] : (id === 'cardAppearance');
      if (isCollapsed) { body && body.classList.add('collapsed'); arrow && arrow.classList.add('folded'); }
    });
  } catch (e) {}
}
function resetAppearance() {
  localStorage.removeItem(APPEARANCE_KEY);
  applyAppearance();
  document.getElementById('accentHex').textContent = '未设置';
  document.getElementById('accentColor').value = '#2f7bff';
  document.querySelectorAll('#accentPresets .accent-dot').forEach(d => d.classList.remove('active'));
  document.querySelectorAll('.grad-chip').forEach(x => x.classList.remove('active'));
  showToast('外观已恢复默认', 'info');
}

// 3D 鼠标跟随微旋转
// 修复 3D 文字发虚/抖动（二次修复）：原实现把鼠标位置写入 --rx/--ry CSS 变量，
// 由 .glass:hover 的 rotateX/rotateY 消费。旋转导致 hover 卡片内文字逐帧重栅格化
//（发虚、边缘闪烁），且旋转投影会在卡片边缘把光标“甩出”→ hover 反复触发/丢失→抖动。
// CSS 侧已改为纯 translateZ 抬升（文字纹理不变），此处停用变量更新，
// 避免无意义的逐帧写入；保留函数与调用点，后续若需恢复跟随旋转只需在此重新赋值。
function bind3DMouse() {
  /* 停用：详见上方注释。如需恢复，在此重新写入 --rx/--ry。 */
}

// 顶栏按钮：打开主题面板（不再简单切换）
function toggleTheme() { openThemePanel(); }

// ============================================
// Robot / Browser Notification (Webhook)
// ============================================
function setChecked(id, v) { const el = document.getElementById(id); if (el) el.checked = !!v; }
function setVal(id, v) { const el = document.getElementById(id); if (el) el.value = v; }

function loadNotifySettings() {
  apiRequest(API.webSettings, 'GET').then(sett => {
    const n = (sett && sett.notify) || {};
    setChecked('notifyEnabled', !!n.enabled);
    setVal('notifyType', n.type || 'webhook');
    setVal('notifyUrl', n.url || '');
    setVal('notifyUids', n.uids || '');
    setVal('notifyTemplate', n.template || '');
    setVal('notifySmtpHost', n.smtp && n.smtp.host || '');
    setVal('notifySmtpPort', n.smtp && n.smtp.port || '');
    setVal('notifySmtpUser', n.smtp && n.smtp.user || '');
    setVal('notifySmtpPass', n.smtp && n.smtp.pass || '');
    setVal('notifySmtpTo', n.smtp && n.smtp.to || '');
    setChecked('notifyHangStart', n.events ? n.events.includes('hang_start') : true);
    setChecked('notifyHangFail', n.events ? n.events.includes('hang_fail') : true);
    setChecked('notifyLoginFail', n.events ? n.events.includes('login_fail') : true);
    setChecked('notifyTaskDone', n.events ? n.events.includes('task_done') : true);
    setChecked('notifyAiStart', n.events ? n.events.includes('ai_start') : true);
    setChecked('notifyAiDone', n.events ? n.events.includes('ai_done') : true);
    setChecked('notifyAiFail', n.events ? n.events.includes('ai_fail') : true);
    setChecked('notifyPointsUpdate', n.events ? n.events.includes('points_update') : true);
    setChecked('notifyRedeemOk', n.events ? n.events.includes('redeem_ok') : true);
    setChecked('notifyRedeemFail', n.events ? n.events.includes('redeem_fail') : true);
    setChecked('notifyLoginOk', n.events ? n.events.includes('login_ok') : false);
    setChecked('notifySysError', n.events ? n.events.includes('sys_error') : false);
    onNotifyTypeChange(true);
  }).catch(() => {});
}
function onNotifyTypeChange(silent) {
  const t = document.getElementById('notifyType').value;
  const isUrl = ['webhook', 'wecom', 'feishu', 'dingtalk'].includes(t);
  const isWxPusher = t === 'wxpusher';
  const isEmail = t === 'email';
  document.getElementById('notifyUrlWrap').style.display = (isUrl || isWxPusher) ? '' : 'none';
  // WxPusher 仅标准 appToken（AT_ 开头）需要 UID；极简 SPT 推送码无需 UID
  const wxVal = document.getElementById('notifyUrl').value.trim();
  const needUids = isWxPusher && wxVal.toUpperCase().startsWith('AT_');
  document.getElementById('notifyUidsWrap').style.display = needUids ? '' : 'none';
  document.getElementById('notifyEmailWrap').style.display = isEmail ? '' : 'none';
  document.getElementById('notifyTplWrap').style.display = (t === 'webhook' || t === 'email' || isWxPusher) ? '' : 'none';
  if (!silent && t === 'browser') {
    if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission();
  }
}

// ============================================
// 面板安全设置（2026-09-07 默认账号模式）
// 设置页「面板安全」卡片：开关/改用户名+密码/退出登录
// ============================================
async function loadPanelAuthSettings() {
  const card = document.getElementById('panelAuthCard');
  try {
    const st = await apiRequest(API.authStatus);
    window._panelAuthStatus = st;  // 缓存状态供弹窗回显当前用户名
    // 桌面版无需面板密码功能：整卡隐藏
    if (card) card.style.display = st.available ? '' : 'none';
    if (!st.available) return;
    setChecked('panelAuthEnabled', !!st.enabled);
    // 默认账号模式：登录保护可随时开关（默认账号已内置）
    const sw = document.getElementById('panelAuthEnabled');
    if (sw) sw.disabled = false;
    // 卡片内只读框回显当前用户名
    const userEl = document.getElementById('panelAuthNewUser');
    if (userEl && st.username) userEl.value = st.username;
  } catch (e) { /* 静默：未启用鉴权时该接口仍可用 */ }
}

// 开关面板登录保护（change 事件）
async function togglePanelAuth() {
  const sw = document.getElementById('panelAuthEnabled');
  const enable = sw.checked;
  if (!enable && !(await showConfirm('关闭后任何人都可直接访问面板，确定关闭登录保护？', { title: '关闭面板登录保护', confirmText: '关闭', danger: true }))) {
    // 取消：回滚开关状态
    sw.checked = true;
    return;
  }
  try {
    const r = await apiRequest(API.authToggle, 'POST', { enabled: enable });
    showToast(r.message || '已更新', 'success');
  } catch (e) {
    showToast(e.message, 'error');
    sw.checked = !enable;
  }
}

// 修改面板账号（2026-09-07 交互升级）：设置页卡片内只留「修改账号」按钮，
// 点击弹出毛玻璃模态窗填写旧密码/新用户名/新密码（带二次确认与可见切换）
function openPanelAuthModal() {
  const modal = document.getElementById('panelAuthModal');
  if (!modal) return;
  // 每次打开都是干净表单；回显当前用户名到新用户名占位符
  const st = window._panelAuthStatus || {};
  const nu = document.getElementById('modalNewUser');
  if (nu) nu.placeholder = st.username ? ('留空不修改（当前：' + st.username + '）') : '字母/数字/下划线，2-32 位';
  ['modalOldPwd', 'modalNewUser', 'modalNewPwd', 'modalNewPwd2'].forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
  const err = document.getElementById('panelAuthModalError');
  if (err) err.textContent = '';
  modal.style.display = 'flex';
  requestAnimationFrame(() => modal.classList.add('show'));
  setTimeout(() => document.getElementById('modalOldPwd')?.focus(), 200);
}

function closePanelAuthModal() {
  const modal = document.getElementById('panelAuthModal');
  if (!modal) return;
  modal.classList.remove('show');
  setTimeout(() => { modal.style.display = 'none'; }, 200);
}

// 弹窗内密码可见切换（两个眼睛按钮共用逻辑）
function bindPwdEye(btnId, inputId) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  btn.addEventListener('click', () => {
    const input = document.getElementById(inputId);
    if (!input) return;
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    btn.innerHTML = '<i class="mdi ' + (show ? 'mdi-eye-outline' : 'mdi-eye-off-outline') + '"></i>';
  });
}

// 弹窗保存入口：前端预校验后提交
async function submitPanelAuthModal() {
  const oldPwd = document.getElementById('modalOldPwd').value;
  const newUser = document.getElementById('modalNewUser').value.trim();
  const newPwd = document.getElementById('modalNewPwd').value.trim();
  const newPwd2 = document.getElementById('modalNewPwd2').value.trim();
  const err = document.getElementById('panelAuthModalError');
  const showErr = (m) => { if (err) err.textContent = m; };
  showErr('');
  if (!oldPwd) return showErr('请输入旧密码验证身份');
  if (!newUser && !newPwd) return showErr('新用户名和新密码至少填写一项');
  if (newUser && !/^[A-Za-z0-9_]{2,32}$/.test(newUser)) return showErr('新用户名需为 2-32 位字母/数字/下划线');
  if (newPwd) {
    if (newPwd.length < 4) return showErr('新密码至少 4 位');
    if (newPwd !== newPwd2) return showErr('两次输入的新密码不一致');
  }
  const btn = document.getElementById('panelAuthModalSave');
  const spinner = btn?.querySelector('.spinner');
  btn.disabled = true;
  if (spinner) spinner.style.display = '';
  try {
    const r = await apiRequest(API.authChangePassword, 'POST', { username: newUser, old_password: oldPwd, new_password: newPwd });
    showToast(r.message || '面板账号设置已修改', 'success');
    closePanelAuthModal();
    // 卡片内只读框同步显示新用户名
    const ro = document.getElementById('panelAuthNewUser');
    if (ro) ro.value = r.username || window._panelAuthStatus?.username || '';
  } catch (e) {
    showErr(e.message || '保存失败');
  } finally {
    btn.disabled = false;
    if (spinner) spinner.style.display = 'none';
  }
}

// 退出面板登录（清除本地 token，重新弹回登录页）
function panelAuthLogout() {
  setPanelToken('');
  showToast('已退出面板登录', 'info');
  showPanelAuthOverlay();
}
function saveNotifySettings() {
  const events = [];
  if (document.getElementById('notifyHangStart').checked) events.push('hang_start');
  if (document.getElementById('notifyHangFail').checked) events.push('hang_fail');
  if (document.getElementById('notifyLoginFail').checked) events.push('login_fail');
  if (document.getElementById('notifyTaskDone').checked) events.push('task_done');
  if (document.getElementById('notifyAiStart').checked) events.push('ai_start');
  if (document.getElementById('notifyAiDone').checked) events.push('ai_done');
  if (document.getElementById('notifyAiFail').checked) events.push('ai_fail');
  if (document.getElementById('notifyPointsUpdate').checked) events.push('points_update');
  if (document.getElementById('notifyRedeemOk').checked) events.push('redeem_ok');
  if (document.getElementById('notifyRedeemFail').checked) events.push('redeem_fail');
  if (document.getElementById('notifyLoginOk').checked) events.push('login_ok');
  if (document.getElementById('notifySysError').checked) events.push('sys_error');
  const type = document.getElementById('notifyType').value;
  const payload = {
    notify: {
      enabled: document.getElementById('notifyEnabled').checked,
      type,
      url: document.getElementById('notifyUrl').value.trim(),
      uids: document.getElementById('notifyUids').value.trim(),
      template: document.getElementById('notifyTemplate').value.trim(),
      events
    }
  };
  if (type === 'email') {
    payload.notify.smtp = {
      host: document.getElementById('notifySmtpHost').value.trim(),
      port: document.getElementById('notifySmtpPort').value.trim(),
      user: document.getElementById('notifySmtpUser').value.trim(),
      pass: document.getElementById('notifySmtpPass').value.trim(),
      to: document.getElementById('notifySmtpTo').value.trim()
    };
  }
  if (payload.notify.enabled && ['webhook', 'wecom', 'feishu', 'dingtalk'].includes(type) && !payload.notify.url) {
    showAlert('notifyAlert', '启用通知需填写 Webhook 地址', 'error'); return;
  }
  if (payload.notify.enabled && type === 'wxpusher' && !payload.notify.url) {
    showAlert('notifyAlert', '启用 WxPusher 需填写推送码（SPT_ 极简码或 AT_ 标准 appToken）', 'error'); return;
  }
  if (payload.notify.enabled && type === 'wxpusher' && payload.notify.url.toUpperCase().startsWith('AT_') && !payload.notify.uids) {
    showAlert('notifyAlert', '标准 appToken（AT_ 开头）需同时填写接收 UID', 'error'); return;
  }
  if (payload.notify.enabled && type === 'email' && !(payload.notify.smtp && payload.notify.smtp.host && payload.notify.smtp.user && payload.notify.smtp.to)) {
    showAlert('notifyAlert', '启用邮件通知需填写 SMTP 服务器、账号、收件人', 'error'); return;
  }
  apiRequest(API.webSettings, 'POST', payload).then(() => {
    showAlert('notifyAlert', '机器人通知配置已保存', 'success');
    showToast('已自动保存', 'success');
    if (type === 'browser' && 'Notification' in window && Notification.permission === 'default') Notification.requestPermission();
  }).catch(e => showAlert('notifyAlert', e.message, 'error'));
}
function testNotify() {
  const type = document.getElementById('notifyType').value;
  if (['webhook', 'wecom', 'feishu', 'dingtalk'].includes(type) && !document.getElementById('notifyUrl').value.trim()) {
    showAlert('notifyAlert', '请先填写 Webhook 地址', 'error'); return;
  }
  if (type === 'wxpusher' && !document.getElementById('notifyUrl').value.trim()) {
    showAlert('notifyAlert', '请先填写 WxPusher 推送码（SPT_ 或 AT_）', 'error'); return;
  }
  if (type === 'wxpusher' && document.getElementById('notifyUrl').value.trim().toUpperCase().startsWith('AT_') && !document.getElementById('notifyUids').value.trim()) {
    showAlert('notifyAlert', '标准 appToken（AT_ 开头）需填写接收 UID', 'error'); return;
  }
  const body = {
    url: document.getElementById('notifyUrl').value.trim(),
    uids: document.getElementById('notifyUids').value.trim(),
    type,
    template: document.getElementById('notifyTemplate').value.trim(),
    title: '测试通知',
    message: '这是一条来自 ctyun 面板的测试消息',
    event: 'test'
  };
  if (type === 'email') {
    body.smtp = {
      host: document.getElementById('notifySmtpHost').value.trim(),
      port: document.getElementById('notifySmtpPort').value.trim(),
      user: document.getElementById('notifySmtpUser').value.trim(),
      pass: document.getElementById('notifySmtpPass').value.trim(),
      to: document.getElementById('notifySmtpTo').value.trim()
    };
  }
  apiRequest(API.testNotify, 'POST', body).then(r => {
    showAlert('notifyAlert', (r && r.ok) ? '测试消息已发送，请检查通知渠道' : '发送失败：' + ((r && r.msg) || '未知错误'), (r && r.ok) ? 'success' : 'error');
  }).catch(e => showAlert('notifyAlert', e.message, 'error'));
}

// 全局通知：横幅 + 浏览器 Notification
let _lastNotifyKey = '';
function pushNotify(type, message, level) {
  level = level || 'info';
  // 横幅
  const banner = document.getElementById('notifyBanner');
  if (banner) {
    banner.className = 'notify-banner show ' + (level === 'error' ? 'error' : level === 'success' ? 'success' : '');
    document.getElementById('notifyBannerIcon').innerHTML = level === 'error' ? '<i class="mdi mdi-alert-circle"></i>' : level === 'success' ? '<i class="mdi mdi-check-circle"></i>' : '<i class="mdi mdi-information"></i>';
    document.getElementById('notifyBannerText').textContent = message;
    clearTimeout(banner._t);
    banner._t = setTimeout(hideNotifyBanner, 5000);
  }
  // 浏览器通知
  try {
    if ('Notification' in window && Notification.permission === 'granted') {
      new Notification('天翼云签到 · ' + (type || '通知'), { body: message });
    }
  } catch (e) {}
}
function hideNotifyBanner() {
  const b = document.getElementById('notifyBanner');
  if (b) b.classList.remove('show');
}

// 轮询状态，检测异常并触发通知（登录失效 / 挂机失败）
let _prevStatus = null;
function notifyPolling() {
  const s = window._lastStatus;
  if (!s) return;
  // 登录失效检测（cookie 不存在或超期）
  const loggedOut = s.cookie_expired && s.account_configured;
  if (loggedOut && (!_prevStatus || !_prevStatus.cookie_expired)) {
    pushNotify('login_fail', '检测到账号登录已失效，请重新登录', 'error');
  }
  // 挂机状态异常
  const hangStatus = (s.hang_status && s.hang_status.status) || '';
  const prevHang = _prevStatus && _prevStatus.hang_status && _prevStatus.hang_status.status;
  if (hangStatus === '挂机失败' && prevHang !== '挂机失败') {
    pushNotify('hang_fail', '云电脑挂机任务执行失败，请查看日志', 'error');
  }
  _prevStatus = s;
}

// 拉取后端通知队列，弹出浏览器通知/横幅（覆盖所有事件类型，含挂机成功/登录成功/系统错误）
let _pollBusy = false;
function pollNotifyQueue() {
  if (_pollBusy) return;
  _pollBusy = true;
  apiRequest(API.pendingNotifies, 'GET').then(r => {
    const items = (r && r.items) || [];
    (items || []).forEach(it => {
      pushNotify(it.event, it.title + '：' + it.text, it.level || 'info');
    });
  }).catch(() => {}).finally(() => { _pollBusy = false; });
}

// ============================================
// Navigation (topnav)
// ============================================
document.querySelectorAll('.dock-item[data-page]').forEach(item => {
    item.addEventListener('click', function() {
        const page = this.dataset.page;
        navigateTo(page);
    });
});

document.getElementById('btnRefresh').addEventListener('click', function() {
    refreshStatus();
    showToast('状态已刷新', 'info');
});

function navigateTo(page) {
    currentPage = page;
    document.querySelectorAll('.dock-item').forEach(i => i.classList.remove('active'));
    document.querySelector(`.dock-item[data-page="${page}"]`)?.classList.add('active');

    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    const targetPage = document.getElementById(`page-${page}`);
    if (targetPage) targetPage.classList.add('active');

    // 修复：切页后回到顶部。原实现保留上一页的滚动位置，从长页面（如日志）切换到
    // 其他页时会停留在页面中部，用户看不到页头
    window.scrollTo(0, 0);

    // Staggered entrance for stat cards (Apple-like reveal)
    targetPage?.querySelectorAll('.stat-card').forEach((card, i) => {
        card.classList.remove('in');
        setTimeout(() => card.classList.add('in'), 60 + i * 70);
    });

    // Load page specific data
    switch(page) {
        case 'settings': showSettingsSub('account'); break;
        case 'redeem': loadRedeemConfig(); loadCachedRewards(); break;
        case 'logs': loadLogs(); break;
    }
}

// ============================================
// 设置页内子分段切换（账号 / 自动化）
function showSettingsSub(sub) {
    document.querySelectorAll('#settingsSegment .seg-btn').forEach(b => b.classList.toggle('active', b.dataset.sub === sub));
    document.querySelectorAll('.sub-page').forEach(s => s.classList.toggle('active', s.id === `sub-${sub}`));
    if (sub === 'account') { loadAccountSettings(); loadDeviceConfig(); loadPanelAuthSettings(); }
    if (sub === 'automation') loadCronSettings();
}

document.querySelectorAll('#settingsSegment .seg-btn').forEach(btn => {
    btn.addEventListener('click', () => showSettingsSub(btn.dataset.sub));
});

// ============================================
// Toast Notifications
// ============================================
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

function showAlert(elementId, message, type = 'success') {
    const el = document.getElementById(elementId);
    if (!el) return;
    el.className = `alert alert-${type} show`;
    el.textContent = message;
    setTimeout(() => {
        el.style.display = 'none';
        el.className = 'alert';
    }, 5000);
}

// 自定义确认弹窗（替代浏览器原生 confirm，更美观、可控）
function showConfirm(message, { title = '请确认', confirmText = '确定', cancelText = '取消', danger = false } = {}) {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        const box = document.createElement('div');
        box.className = 'modal-box' + (danger ? ' modal-danger' : '');
        box.innerHTML = `
            <div class="modal-header"><i class="mdi">${danger ? '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 2L1 21h22L12 2zm0 15a1.2 1.2 0 110 2.4A1.2 1.2 0 0112 17zm1-7h-2v6h2V10z"/></svg>' : '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 2a10 10 0 100 20 10 10 0 000-20zm1 15h-2v-6h2v6zm0-8h-2V7h2v2z"/></svg>'}</i><span>${title}</span></div>
            <div class="modal-body">${message}</div>
            <div class="modal-actions">
                <button class="btn btn-ghost modal-cancel">${cancelText}</button>
                <button class="btn ${danger ? 'btn-danger' : 'btn-primary'} modal-confirm">${confirmText}</button>
            </div>`;
        overlay.appendChild(box);
        document.body.appendChild(overlay);
        requestAnimationFrame(() => overlay.classList.add('show'));

        const close = (result) => {
            overlay.classList.remove('show');
            setTimeout(() => overlay.remove(), 200);
            resolve(result);
        };
        box.querySelector('.modal-cancel').addEventListener('click', () => close(false));
        box.querySelector('.modal-confirm').addEventListener('click', () => close(true));
        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(false); });
        const onKey = (e) => { if (e.key === 'Escape') { close(false); document.removeEventListener('keydown', onKey); } };
        document.addEventListener('keydown', onKey);
    });
}

// ============================================
// API Helper
// ============================================
async function apiRequest(url, method = 'GET', body = null) {
    try {
        const options = {
            method: method,
            headers: { 'Content-Type': 'application/json' }
        };
        // 面板鉴权（2026-09-06 新增）：携带访问 token（启用面板密码后必需）
        const tok = getPanelToken();
        if (tok) options.headers['X-Auth-Token'] = tok;
        if (body) options.body = JSON.stringify(body);
        const response = await fetch(url, options);
        const data = await response.json();
        if (!response.ok) {
            // 401 = 面板登录过期/无效：清除本地 token 并弹回登录遮罩
            // （排除鉴权接口本身：登录失败提示由表单内展示，不重复跳转）
            if (response.status === 401 && data.auth_required && !url.startsWith('/api/auth/')) {
                setPanelToken('');
                showPanelAuthOverlay();
            }
            throw new Error(data.error || `请求失败 (${response.status})`);
        }
        return data;
    } catch (error) {
        throw new Error(error.message || '网络请求失败');
    }
}

// 弹回面板登录遮罩（token 失效时由 apiRequest 调用）
// 2026-09-07 默认账号模式：移除 setup 表单引用
function showPanelAuthOverlay() {
    const overlay = document.getElementById('panelAuthOverlay');
    if (!overlay) return;
    // 幂等保护（2026-09-07）：遮罩已展示时直接返回。未登录期间后台轮询
    // 每 30 秒触发 401 都会调到这里，若重复重置会清掉登录表单刚显示的
    // 「用户名或密码错误」提示，用户看不到报错原因。
    if (overlay.style.display === 'flex' && overlay.classList.contains('show')) return;
    const sub = document.getElementById('panelAuthSub');
    const loginForm = document.getElementById('panelAuthLoginForm');
    const errEl = document.getElementById('panelAuthError');
    if (sub) sub.textContent = '登录已过期，请重新输入面板账号密码';
    if (loginForm) loginForm.style.display = 'flex';
    if (errEl) errEl.textContent = '';
    overlay.style.display = 'flex';
    requestAnimationFrame(() => overlay.classList.add('show'));
    const first = document.getElementById('panelAuthUser');
    if (first) setTimeout(() => first.focus(), 200);
}

// ============================================
// Dashboard / Status
// ============================================
// 修复：轮询失败时每 30 秒弹一次 toast 骚扰用户。加连续失败计数：
// 前两次静默、之后降频提示；恢复成功后重置计数。
let _statusFailCount = 0;
let _lastStatusFailToastTs = 0;
async function refreshStatus() {
    try {
        const data = await apiRequest(API.getStatus);
        _statusFailCount = 0;
        window._lastStatus = data;
        updateDashboard(data);
        notifyPolling();
        pollNotifyQueue();
    } catch (error) {
        _statusFailCount++;
        // 401（未登录/token 失效）不在这里弹回登录遮罩：那是 initPanelAuth
        // 的职责；且未登录时每 30 秒轮询都会 401，若在此弹回会覆盖
        // 登录表单的「用户名或密码错误」提示，导致看不到报错原因。
        if (!/未登录或登录已过期/.test(error.message)) {
        // 连续失败时降频提示：首败静默（可能是瞬时网络抖动），
        // 之后每 3 次失败（约 90 秒）提示一次
        if (_statusFailCount === 2 || (_statusFailCount > 2 && Date.now() - _lastStatusFailToastTs > 90000)) {
            showToast(error.message, 'error');
            _lastStatusFailToastTs = Date.now();
        }
        const el = document.getElementById('heroTitle');
        if (el) el.textContent = '连接失败';
        const dotWrap = document.getElementById('statusDotWrap');
        if (dotWrap) dotWrap.className = 'status-dot-wrap offline';
        }
    }
}

function updateDashboard(data) {
    const running = data.container_running;
    const setText = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
    // Apple-style number count-up: ease from current value to target
    const animateNumber = (id, target) => {
        const el = document.getElementById(id);
        if (!el) return;
        const from = parseFloat((el.textContent || '0').replace(/[^\d.]/g, '')) || 0;
        const to = parseFloat(target) || 0;
        if (from === to) { el.textContent = to; return; }
        const dur = 700, t0 = performance.now();
        const step = (t) => {
            const p = Math.min(1, (t - t0) / dur);
            const eased = 1 - Math.pow(1 - p, 3); // easeOutCubic
            el.textContent = Math.round(from + (to - from) * eased);
            if (p < 1) requestAnimationFrame(step);
        };
        requestAnimationFrame(step);
    };

    // ---- Status Hero ----
    const dotWrap = document.getElementById('statusDotWrap');
    if (dotWrap) {
        dotWrap.className = 'status-dot-wrap ' + (running ? 'online' : 'offline');
    }
    if (data.account_configured) {
        setText('statAccount', '已配置');
        const el = document.getElementById('statAccount'); if (el) el.className = 'stat-value success';
        const u = data.account_user || '';
        setText('statAccountMeta', u.length > 6 ? u.substring(0, 3) + '****' + u.slice(-4) : u);
    } else {
        setText('statAccount', '未配置');
        const el = document.getElementById('statAccount'); if (el) el.className = 'stat-value warning';
        setText('statAccountMeta', '未设置账号');
    }
    updateLoginBadge(data);
    setText('heroTitle', running ? '服务运行中' : '服务已停止');
    const flags = [];
    if (data.ai_task_enabled) flags.push('AI 对话');
    if (data.hang_running) flags.push('挂机中');
    else if (data.hang_task_enabled) flags.push('挂机待执行');
    setText('heroMeta', (running ? '运行' : '停止') + (flags.length ? ' · ' + flags.join(' · ') : ''));
    setText('statUptime', running ? (data.uptime || '运行中') : '--');

    // ---- AI card ----
    setText('statAi', data.ai_task_enabled ? '已启用' : '未启用');
    const aiEl = document.getElementById('statAi'); if (aiEl) aiEl.className = 'stat-value ' + (data.ai_task_enabled ? 'success' : 'warning');
    setText('statAiMeta', (data.ai_chat_time && data.ai_chat_time.length) ? (data.ai_chat_enabled !== false && data.ai_task_enabled !== false ? data.ai_chat_time.join('、') : '已关闭') : '未配置');

    // ---- Hang card ----
    setText('statHang', data.hang_task_enabled ? '已启用' : '未启用');
    const hangEl = document.getElementById('statHang'); if (hangEl) hangEl.className = 'stat-value ' + (data.hang_task_enabled ? 'success' : 'warning');
    renderHangProgress(data.hang_status, data);
    // 挂机状态变化提示（失败/完成/开始）
    const hs = data.hang_status || {};
    const hsKey = `${hs.status || ''}|${hs.running ? 1 : 0}`;
    if (window._lastHangKey && window._lastHangKey !== hsKey) {
        if (hs.status === '挂机失败') {
            showToast('挂机失败：' + (hs.message || '任务异常退出'), 'error');
        } else if (hs.status === '挂机完成') {
            showToast('挂机已完成 ✓', 'success');
        } else if (hs.running && hs.status === '挂机中') {
            showToast('挂机已开始', 'info');
        }
    }
    window._lastHangKey = hsKey;

    // ---- Points card ----
    const pts = (data.rewards && data.rewards.points != null) ? data.rewards.points : (data.points ?? '--');
    if (typeof pts === 'number' || /^\d+$/.test(String(pts))) animateNumber('statPoints', pts);
    else setText('statPoints', pts);
    const redeemInfo = data.redeem_enabled ? (data.redeem_name || '已配置') : '未启用';
    setText('statPointsMeta', data.redeem_enabled ? `兑换已启用 · ${redeemInfo}` : '兑换未启用');
    // 积分刷新频率 + 趋势
    const prHours = data.points_refresh_hours || 8;
    setText('statPointsRefresh', `每 ${prHours} 小时自动刷新`);
    renderPointsTrend(data.points_history);

    // 下次任务倒计时
    const nextAi = data.next_ai_run || '';
    const nextHang = data.next_hang_run || '';
    setText('statNextAi', nextAi || '—');
    setText('statNextHang', nextHang || '—');

    // ---- Keepalive card ----
    // 保活为「周期性重连」模式：连接 300s（保持会话）→ 断开 keepalive_seconds → 重连。
    // 修复文案误导：原「周期约 X 分」让用户以为每 X 秒发一次心跳请求，
    // 实际断开窗口内零请求（更不易触发风控）。改为明示两段构成。
    // 补全「启用保活心跳」开关联动：关闭时明确显示已停用（而非误导性的运行描述）。
    if (data.keepalive_enabled === false) {
        setText('statKeepAlive', '已停用');
        setText('statKeepAliveMeta', '在自动化任务中开启后恢复');
    } else if (data.keepalive_seconds) {
        const kaMin = data.keepalive_seconds / 60;
        const kaTxt = Number.isInteger(kaMin) ? kaMin : kaMin.toFixed(1);
        setText('statKeepAlive', `断开 ${kaTxt} 分 → 连接 5 分`);
        setText('statKeepAliveMeta', data.keepalive_effective === false ? '启动中/待生效' : '运行中');
    } else {
        setText('statKeepAlive', '未配置');
        setText('statKeepAliveMeta', data.scheduler_running ? '调度器运行中' : '调度器未运行');
    }

    // ---- Detail status grid ----
    // 合并展示：容器/CrYun/Web 面板三个技术性条目合并为一条「运行环境」，
    // 减少零散 chip 与重复的「运行中」文案；异常时拆开明细展示，正常时不噪音。
    const envOk = running && data.scheduler_running !== false;
    const envDetail = [];
    if (!running) envDetail.push('容器已停止');
    // 补全开关联动：保活已停用时签到程序不运行属预期行为，不再误报
    if (data.ctyun_running === false && data.keepalive_enabled !== false) envDetail.push('签到程序未运行');
    setText('statusEnv', envDetail.length ? envDetail.join(' · ') : '容器 / 签到 / 面板 正常');
    const envChip = document.getElementById('statusEnv');
    if (envChip && envChip.closest('.chip')) envChip.closest('.chip').classList.toggle('bad', !envOk);
    setText('statusCron', data.scheduler_running ? '运行中' : '未运行');
    const cronChip = document.getElementById('statusCron');
    if (cronChip && cronChip.closest('.chip')) cronChip.closest('.chip').classList.toggle('ok', !!data.scheduler_running);
    setText('statusLastLogin', data.last_login || '从未登录');
    const authOk = !!(data.cookie_exists && data.auth_data_exists);
    setText('statusAuth', authOk ? '已保存' : (data.cookie_exists ? 'Cookie 已保存' : '无'));
    const authChip = document.getElementById('statusAuth');
    if (authChip && authChip.closest('.chip')) authChip.closest('.chip').classList.toggle('ok', authOk);
}

// Render hang progress in dashboard stat card (progress bar + sub text)
function renderHangProgress(hang, cfg) {
    const textEl = document.getElementById('hangProgressText');
    const pctEl = document.getElementById('hangProgressPct');
    const fillEl = document.getElementById('hangProgressBar');
    const metaEl = document.getElementById('statHangMeta');
    const cardEl = document.getElementById('hangCard');

    // 历史失败状态兜底：若「挂机失败」的 updated 距今已超过 10 分钟，
    // 视为上一轮遗留（新一轮挂机还没开始），按未挂机展示，避免看板一直显示旧的失败。
    const _staleFail = (hang && hang.status === '挂机失败') ? (() => {
        if (!hang.updated) return true;
        const t = new Date(hang.updated.replace(' ', 'T'));
        if (isNaN(t.getTime())) return true;
        return (Date.now() - t.getTime()) > 10 * 60 * 1000;
    })() : false;
    // 历史"挂机完成"残留兜底：completed_at/updated 距今超过挂机配置时长 + 5 分钟缓冲，
    // 视为上一轮遗留，按未挂机展示，避免没挂机却一直显示"挂机完成 60 分钟"。
    // 修复：原缓冲太短（总时长+5min），挂机刚完成的展示期内（用户看面板的黄金时段）
    // 就被误判为"陈旧"，明明刚拿到奖励却显示「未挂机」。
    // 改为：完成状态在当天 20:00 前保留至 12 小时、之后保留到次日 12:00（跨天兜底），
    // 且新任务开始时状态文件会被重置，不存在"永远显示完成"问题。
    const _staleDone = (hang && hang.status === '挂机完成') ? (() => {
        if (!hang.updated) return true;
        const t = new Date(hang.updated.replace(' ', 'T'));
        if (isNaN(t.getTime())) return true;
        // 完成态保留 12 小时（一个自然日内的展示窗口足够，且新挂机启动会覆盖状态）
        return (Date.now() - t.getTime()) > 12 * 60 * 60 * 1000;
    })() : false;
    const failed = hang && hang.status === '挂机失败' && !_staleFail;
    if (cardEl) cardEl.classList.toggle('hang-failed', !!failed);

    // 进度计算：优先「墙钟已过时长」（真实可信），云端 current_progress 仅作参考。
    // 修复：云端任务"使用1小时"上限 3600 秒（60 分钟），配置 > 60 分钟时若优先取云端，
    // 进度条会永久卡在 60/N%，必须以墙钟为准。
    function calcProgress(h) {
        const total = h.total_minutes || 0;
        let earned = 0;
        if (h.elapsed_minutes && h.elapsed_minutes > 0) {
            earned = h.elapsed_minutes;
        } else if (h.current_progress && h.current_progress > 0) {
            earned = Math.floor(h.current_progress / 60);
        }
        earned = Math.min(earned, total);
        const pct = total > 0 ? Math.min(100, Math.round((earned / total) * 100)) : 0;
        const remain = Math.max(0, total - earned);
        return { total, earned, pct, remain };
    }

    if (!hang || !hang.running) {
        // 陈旧失败 / 陈旧完成（上一轮遗留）按未挂机展示，不再显示旧的"挂机完成/挂机失败"
        const rawStatus = (hang && hang.status) ? hang.status : '未挂机';
        const status = ((_staleFail && rawStatus === '挂机失败') || (_staleDone && rawStatus === '挂机完成')) ? '未挂机' : rawStatus;
        if (textEl) textEl.textContent = status;
        if (pctEl) pctEl.textContent = (status === '挂机完成') ? '100%' : '0%';
        if (fillEl) { fillEl.style.width = (status === '挂机完成') ? '100%' : '0%'; fillEl.classList.remove('animated'); }
        if (metaEl) {
            if (failed) {
                metaEl.textContent = (hang.message || '挂机任务失败');
                metaEl.className = 'stat-sub hang-failed-text';
            } else if (status === '挂机完成' && !_staleDone) {
                // 完成时间取 updated（收尾写入时刻），有 elapsed 显示实际挂机时长
                const doneMin = hang.elapsed_minutes || hang.total_minutes || 0;
                metaEl.textContent = `本次挂机已完成（共 ${doneMin} 分钟），奖励已到账`;
                metaEl.className = 'stat-sub';
            } else {
                // 未挂机时显示用户真实配置时长与今日计划，避免显示错误的固定值
                const cfgMin = (cfg && cfg.hang_minutes) ? cfg.hang_minutes : 0;
                const cfgTime = (cfg && cfg.pc_hang_time && cfg.pc_hang_time.length) ? cfg.pc_hang_time.join('、') : '';
                const nextRun = (cfg && cfg.next_hang_run) ? cfg.next_hang_run : '';
                if (cfgMin > 0) {
                    metaEl.textContent = `未挂机 · 配置 ${cfgMin} 分钟（${cfgTime || '未设时间'}${nextRun ? ' · 下次 ' + nextRun : ''}）`;
                } else {
                    metaEl.textContent = '当前无挂机任务';
                }
                metaEl.className = 'stat-sub';
            }
        }
        return;
    }

    const p = calcProgress(hang);
    const status = hang.status || '挂机中';
    if (textEl) textEl.textContent = `${status}：${p.earned} / ${p.total} 分钟`;
    if (pctEl) pctEl.textContent = p.pct + '%';
    if (fillEl) { fillEl.style.width = p.pct + '%'; fillEl.classList.toggle('animated', p.pct < 100); }
    if (metaEl) {
        metaEl.textContent = p.remain > 0 ? `挂机进度 ${p.pct}% · 剩余约 ${p.remain} 分钟` : '挂机即将完成';
        metaEl.className = 'stat-sub';
    }
}

// ============================================
// 积分趋势（统计摘要 + 交互 SVG 折线 + 变动明细）
// ============================================
let _ptsRange = 'all';   // all / 7d / 30d / 90d
let _ptsRaw = [];        // 原始 history（来自后端）

function renderPointsTrend(history) {
    _ptsRaw = Array.isArray(history) ? history : [];
    drawPointsTrend();
}

function _ptsDeltaTxt(v) {
    if (v > 0) return '+' + v;
    if (v < 0) return String(v);
    return '0';
}
function _ptsNum(v) {
    if (v == null || isNaN(v)) return '--';
    return (Math.round(v * 10) / 10).toLocaleString('zh-CN', { maximumFractionDigits: 1 });
}
function _ptsPad(n) { return String(n).padStart(2, '0'); }
function _ptsDay(t) {
    const now = new Date();
    if (t.getFullYear() === now.getFullYear()) return `${_ptsPad(t.getMonth() + 1)}-${_ptsPad(t.getDate())}`;
    return `${String(t.getFullYear()).slice(2)}-${_ptsPad(t.getMonth() + 1)}-${_ptsPad(t.getDate())}`;
}
function _ptsFull(t) {
    return `${t.getFullYear()}-${_ptsPad(t.getMonth() + 1)}-${_ptsPad(t.getDate())} ${_ptsPad(t.getHours())}:${_ptsPad(t.getMinutes())}`;
}
function _ptsSpan(ms) {
    const mins = Math.round(ms / 60000);
    if (mins < 60) return mins + ' 分钟';
    const hours = Math.round((mins / 60) * 10) / 10;
    if (hours < 48) return hours + ' 小时';
    const days = Math.floor(hours / 24);
    const rem = Math.round(hours % 24);
    return days + ' 天' + (rem ? rem + ' 小时' : '');
}

function drawPointsTrend() {
    const box = document.getElementById('pointsTrend');
    const detailBox = document.getElementById('pointsDetail');
    const summaryBox = document.getElementById('trendSummary');
    if (!box) return;

    const empty = () => {
        box.innerHTML = '<div class="trend-empty">暂无足够积分记录生成趋势（自动刷新或挂机后会自动累积）</div>';
        if (summaryBox) summaryBox.innerHTML = '';
        if (detailBox) detailBox.innerHTML = '';
    };

    // 1. 归一化 + 排序 + 去重
    let data = _ptsRaw
        .filter(h => h && h.points != null && !isNaN(parseInt(h.points, 10)))
        .map(h => {
            const t = h.ts ? new Date(h.ts) : null;
            return {
                ts: h.ts || '',
                time: t,
                points: Math.max(0, parseInt(h.points, 10) || 0),
                delta: parseInt(h.delta, 10) || 0
            };
        })
        .filter(d => d.time && !isNaN(d.time.getTime()))
        .sort((a, b) => a.time - b.time);
    const seen = new Set();
    data = data.filter(d => {
        const k = d.ts + '|' + d.points;
        if (seen.has(k)) return false;
        seen.add(k);
        return true;
    });
    // 旧数据无 delta 则补算
    for (let i = 1; i < data.length; i++) {
        if (!data[i].delta) data[i].delta = data[i].points - data[i - 1].points;
    }

    // 2. 时间范围过滤
    let rows = data;
    if (_ptsRange !== 'all') {
        const days = { '7d': 7, '30d': 30, '90d': 90 }[_ptsRange] || 30;
        const cutoff = Date.now() - days * 86400000;
        rows = data.filter(d => d.time.getTime() >= cutoff);
    }
    if (rows.length < 2) { empty(); return; }

    // 3. 统计摘要
    const first = rows[0], last = rows[rows.length - 1];
    const vals = rows.map(d => d.points);
    const minV = Math.min(...vals), maxV = Math.max(...vals);
    const avgV = vals.reduce((s, v) => s + v, 0) / vals.length;
    const deltaV = last.points - first.points;
    const daily = deltaV / Math.max(1, (last.time - first.time) / 86400000);
    const spanTxt = _ptsSpan(last.time - first.time);
    if (summaryBox) {
        summaryBox.innerHTML =
            `<div class="trend-sum-item"><span class="k">当前积分</span><span class="v accent">${last.points.toLocaleString()}</span></div>` +
            `<div class="trend-sum-item"><span class="k">区间变化</span><span class="v ${deltaV > 0 ? 'up' : (deltaV < 0 ? 'down' : '')}">${_ptsDeltaTxt(deltaV)}</span></div>` +
            `<div class="trend-sum-item"><span class="k">日均增长</span><span class="v ${daily > 0 ? 'up' : (daily < 0 ? 'down' : '')}">${_ptsNum(daily)}</span></div>` +
            `<div class="trend-sum-item"><span class="k">最高 / 最低</span><span class="v">${maxV.toLocaleString()} / ${minV.toLocaleString()}</span></div>` +
            `<div class="trend-sum-item"><span class="k">平均积分</span><span class="v">${_ptsNum(avgV)}</span></div>` +
            `<div class="trend-sum-item"><span class="k">记录时长</span><span class="v">${spanTxt}</span></div>`;
    }

    // 4. SVG（viewBox 宽度取容器实际宽度，避免 preserveAspectRatio 拉伸文字）
    const cw = box.clientWidth || 720;
    const W = Math.max(320, cw), H = 250;
    const padL = 52, padR = 16, padT = 18, padB = 30;
    const plotW = W - padL - padR, plotH = H - padT - padB;
    const vSpan = Math.max(1, maxV - minV);
    const X = i => padL + (i / (rows.length - 1)) * plotW;
    const Y = v => padT + ((maxV - v) / vSpan) * plotH;

    const line = rows.map((d, i) => (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(d.points).toFixed(1)).join(' ');
    const area = `M${X(0).toFixed(1)} ${(padT + plotH).toFixed(1)} ` +
        rows.map((d, i) => 'L' + X(i).toFixed(1) + ' ' + Y(d.points).toFixed(1)).join(' ') +
        ` L${X(rows.length - 1).toFixed(1)} ${(padT + plotH).toFixed(1)} Z`;

    // 水平网格 + Y 刻度（5 档）
    let gridSvg = '';
    for (let g = 0; g <= 4; g++) {
        const gv = minV + (vSpan * g) / 4;
        const gy = Y(gv);
        gridSvg += `<line x1="${padL}" y1="${gy.toFixed(1)}" x2="${W - padR}" y2="${gy.toFixed(1)}" stroke="rgba(148,163,184,.28)" stroke-width="1" stroke-dasharray="4 4"/>`;
        gridSvg += `<text x="${padL - 6}" y="${(gy + 3.5).toFixed(1)}" text-anchor="end" font-size="10" fill="#94a3b8">${_ptsNum(gv)}</text>`;
    }
    // X 轴时间刻度（最多 5 个）
    const tickN = Math.min(5, rows.length);
    let axisSvg = '';
    for (let t = 0; t < tickN; t++) {
        const i = Math.round((t * (rows.length - 1)) / (tickN - 1));
        axisSvg += `<text x="${X(i).toFixed(1)}" y="${H - 9}" text-anchor="middle" font-size="10" fill="#94a3b8">${_ptsDay(rows[i].time)}</text>`;
    }
    // 平均线
    const ay = Y(avgV);
    const avgSvg = `<line x1="${padL}" y1="${ay.toFixed(1)}" x2="${W - padR}" y2="${ay.toFixed(1)}" stroke="rgba(249,115,22,.5)" stroke-width="1" stroke-dasharray="6 4"/>` +
        `<text x="${W - padR - 2}" y="${(ay - 5).toFixed(1)}" text-anchor="end" font-size="9" fill="#f97316">平均 ${_ptsNum(avgV)}</text>`;

    // 数据点（点少才逐个画圆点，避免过密）
    let dotsSvg = '';
    if (rows.length <= 40) {
        dotsSvg = rows.map((d, i) => `<circle cx="${X(i).toFixed(1)}" cy="${Y(d.points).toFixed(1)}" r="2.4" fill="#3b82f6"/>`).join('');
    }
    const lx = X(rows.length - 1), ly = Y(last.points);
    dotsSvg += `<circle cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="4.5" fill="#3b82f6" stroke="#fff" stroke-width="1.5"/>`;
    dotsSvg += `<text x="${lx.toFixed(1)}" y="${(ly - 10).toFixed(1)}" text-anchor="middle" font-size="11" font-weight="700" fill="#1e293b">${last.points.toLocaleString()}</text>`;

    box.innerHTML = `<div class="trend-plot">
        <svg viewBox="0 0 ${W} ${H}" class="trend-svg" style="height:${H}px">
            <defs><linearGradient id="ptsGrad2" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#3b82f6" stop-opacity="0.32"/>
                <stop offset="100%" stop-color="#3b82f6" stop-opacity="0.02"/>
            </linearGradient></defs>
            ${gridSvg}
            <path d="${area}" fill="url(#ptsGrad2)"/>
            <path d="${line}" fill="none" stroke="#3b82f6" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>
            ${avgSvg}
            ${dotsSvg}
            ${axisSvg}
        </svg>
        <div class="trend-tooltip" id="trendTooltip" style="display:none;"></div>
    </div>`;

    // 5. hover 提示（悬停最近数据点显示时间/积分/变化）
    const plot = box.querySelector('.trend-plot');
    const svg = plot.querySelector('svg');
    const tooltip = plot.querySelector('#trendTooltip');
    svg.addEventListener('mousemove', e => {
        const rect = svg.getBoundingClientRect();
        const mx = ((e.clientX - rect.left) / rect.width) * W;
        let bi = 0, bd = Infinity;
        rows.forEach((d, i) => {
            const dx = Math.abs(X(i) - mx);
            if (dx < bd) { bd = dx; bi = i; }
        });
        const d = rows[bi];
        tooltip.innerHTML =
            `<div class="tt-title">${_ptsFull(d.time)}</div>` +
            `<div>积分 ${d.points.toLocaleString()}</div>` +
            `<div class="tt-delta ${d.delta > 0 ? 'up' : (d.delta < 0 ? 'down' : 'flat')}">${_ptsDeltaTxt(d.delta)}</div>`;
        const tx = Math.max(56, Math.min(W - 56, X(bi)));
        const ty = Math.max(34, Y(d.points) - 12);
        tooltip.style.left = ((tx / W) * 100) + '%';
        tooltip.style.top = ty + 'px';
        tooltip.style.display = 'block';
    });
    svg.addEventListener('mouseleave', () => { tooltip.style.display = 'none'; });

    // 6. 最近积分变动明细（最新在上）
    if (detailBox) {
        const recent = rows.slice(-8).reverse();
        detailBox.innerHTML = recent.map(d => {
            const cls = d.delta > 0 ? 'up' : (d.delta < 0 ? 'down' : 'flat');
            return `<div class="trend-item">` +
                `<span class="t">${_ptsFull(d.time)}</span>` +
                `<span class="p">${d.points.toLocaleString()}</span>` +
                `<span class="d ${cls}">${_ptsDeltaTxt(d.delta)}</span>` +
                `</div>`;
        }).join('');
    }
}

// 下载日志
// 修复（2026-09-06）：启用面板密码后直接 <a href> 下载不带 token 会被 401 拦截，
// 改为 fetch 带 token 取回文本后本地保存（沿用后端给的文件名）。
async function downloadLogs() {
    try {
        const tok = getPanelToken();
        const headers = tok ? { 'X-Auth-Token': tok } : {};
        const resp = await fetch(API.logsDownload, { headers });
        if (!resp.ok) throw new Error(`下载失败 (${resp.status})`);
        const blob = await resp.blob();
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'ctyun_logs.txt';
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 5000);
    } catch (e) {
        showToast(e.message || '日志下载失败', 'error');
    }
}

// 重启容器（Web 服务）
async function restartContainer() {
    if (!(await showConfirm('确认要重启 Web 服务吗？重启后约需几秒恢复。', { title: '重启容器', confirmText: '重启', danger: true }))) return;
    try {
        await apiRequest(API.restart, 'POST', {});
        showToast('Web 服务正在重启…', 'info');
        setTimeout(refreshStatus, 4000);
    } catch (e) {
        showToast(e.message || '重启失败', 'error');
    }
}

function highlightKeepalive(btn) {
    const val = btn.dataset.val || btn.dataset.value;
    const inp = document.getElementById('keepaliveSeconds');
    if (inp && val) inp.value = val;
    btn.closest('.cron-presets').querySelectorAll('.cron-preset-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
}

// ============================================
// Account Settings
// ============================================
async function loadAccountSettings() {
    suppressAutoSave = true;
    try {
        const data = await apiRequest(API.getSettings);
        document.getElementById('username').value = data.username || '';
        document.getElementById('password').value = data.password || '';

        // Status info
        document.getElementById('cookieStatus').textContent = data.cookie_exists ? '已保存' : '无';
        document.getElementById('authStatus').textContent = data.auth_data_exists ? '已保存' : '无';
        document.getElementById('lastLoginTime').textContent = data.last_login || '从未登录';

        // Preset messages
        if (data.preset_messages && data.preset_messages.length > 0) {
            document.getElementById('presetMessages').value = data.preset_messages.join('\n');
        }

        // Cookie info (readonly display)
        const cookieEl = document.getElementById('accountCookie');
        if (cookieEl) {
            if (data.cookie_exists && data.username) {
                cookieEl.value = `已保存: ctyun_cookies_${data.username}_.json`;
            } else {
                cookieEl.value = '暂无 Cookie，保存账号并测试登录后自动生成';
            }
        }
    } catch (error) {
        showToast(error.message, 'error');
    }
    suppressAutoSave = false;
}

// 自动保存：修改后直接保存并生效，无需再点“保存”按钮。
// suppressAutoSave 用于在 loadXxx 回填数据时临时屏蔽自动保存，避免误写。
// _autoSaveBusy 用于在请求进行中屏蔽重复提交（避免用户连点保存导致重复请求）。
let suppressAutoSave = false;
let _autoSaveTimers = {};
let _autoSaveBusy = {};
// 修复：busy 期间的修改直接被丢弃。新增 pending 标志：保存进行中又有新
// 输入时标记 pending，当前保存结束后补一次保存，保证最后一次输入不丢失。
let _autoSavePending = {};
function autoSave(key, fn, delay) {
    if (suppressAutoSave) return;
    if (_autoSaveBusy[key]) { _autoSavePending[key] = true; return; }  // 保存中先记 pending，结束后补保存
    if (_autoSaveTimers[key]) clearTimeout(_autoSaveTimers[key]);
    _autoSaveTimers[key] = setTimeout(async () => {
        _autoSaveTimers[key] = null;
        _autoSaveBusy[key] = true;
        try { await fn(); }
        finally {
            _autoSaveBusy[key] = false;
            // 保存期间又有新输入：补一次保存（读到的已是最新表单值）
            if (_autoSavePending[key]) {
                _autoSavePending[key] = false;
                autoSave(key, fn, delay);
            }
        }
    }, delay || 600);
}

async function saveAccountSettings(isAuto) {
    const username = document.getElementById('username').value.trim();
    const password = document.getElementById('password').value;
    const presetMessages = document.getElementById('presetMessages').value;

    if (!username || !password) {
        // 自动保存时若密码为空（例如从后端回填后用户还没填），静默跳过，避免清空凭据
        if (isAuto) return;
        showToast('请填写账号和密码', 'warning');
        return;
    }

    try {
        const settings = {
            username: username,
            password: password,
            preset_messages: presetMessages ? presetMessages.split('\n').filter(s => s.trim()) : []
        };
        await apiRequest(API.saveSettings, 'POST', settings);
        if (isAuto) showToast('已自动保存', 'success');
        else {
            showToast('账号设置已保存', 'success');
            showAlert('settingsAlert', '账号设置已保存成功', 'success');
        }
        refreshStatus();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// 随机生成设备代码（调用后端 /api/device-code/regenerate）
async function generateDeviceCode() {
    const input = document.getElementById('accountDeviceCode');
    if (!input) return;
    try {
        const data = await apiRequest(API.regenerateDeviceCode, 'POST', {});
        const code = data.device_code || '';
        if (!code) {
            showToast('生成失败：未返回设备代码', 'error');
            return;
        }
        input.value = code;
        showToast('已生成设备代码，自动保存并生效', 'success');
        saveAccountDeviceCode(true);
    } catch (error) {
        showToast('生成失败: ' + error.message, 'error');
    }
}

// 保存账号页设备代码并生效（写入 web_settings.json + .devicecode_{username} 文件）
async function saveAccountDeviceCode(isAuto) {
    const input = document.getElementById('accountDeviceCode');
    if (!input) return;
    const code = input.value.trim();
    if (!code) {
        if (!isAuto) showToast('请先输入或随机生成设备代码', 'warning');
        return;
    }
    try {
        await apiRequest(API.saveDeviceCode, 'POST', { device_code: code });
        if (!isAuto) {
            showToast('设备代码已保存并生效', 'success');
            showAlert('settingsAlert', '设备代码已保存并生效', 'success');
        }
        refreshStatus();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function testLogin() {
    const username = document.getElementById('username').value.trim();
    const password = document.getElementById('password').value;
    if (!username || !password) {
        showToast('请先填写账号和密码', 'warning');
        return;
    }

    const btn = document.getElementById('btnTestLogin');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> 登录中...';

    try {
        const result = await apiRequest(API.testLogin, 'POST', { username, password });
        if (!result.success) {
            showToast('登录触发失败: ' + (result.error || '未知错误'), 'error');
            return;
        }
        showToast('已触发登录，正在验证账号…', 'info');
        // 轮询日志，等待登录完成或需要短信验证码
        await pollLoginProgress(btn);
    } catch (error) {
        showToast('登录失败: ' + error.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="mdi mdi-account-check-outline"></i> 测试登录';
        if (window.renderIcons) window.renderIcons(btn);
    }
}

// 轮询登录进度：检测日志中的“短信验证码”提示并弹窗输入
let _loginPollTimer = null;
async function pollLoginProgress(btn) {
    if (_loginPollTimer) clearInterval(_loginPollTimer);
    let smsPrompted = false;
    let done = false;
    let attempts = 0;
    _loginPollTimer = setInterval(async () => {
        if (done || attempts >= 90) { clearInterval(_loginPollTimer); return; }
        attempts++;
        try {
            // 修复（2026-09-06）：链路断裂根因 —— 这里轮询 type=all 只读 Web 面板日志
            // （web_panel.log），而短信验证码提示由登录任务进程打印到独立任务日志
            // login_task.log，永远不会出现在 all 里，导致弹窗逻辑形同虚设、
            // 用户被迫去终端执行 docker exec 命令。
            // 现改为同时拉取 login 任务日志（专查验证码提示）与 all（判登录成败）。
            const [loginLogs, allLogs] = await Promise.all([
                apiRequest(API.getLogs + '?type=login&limit=40'),
                apiRequest(API.getLogs + '?type=all&limit=30')
            ]);
            const loginText = (loginLogs.logs || []).join('\n');
            const allText = (allLogs.logs || []).map(l => (typeof l === 'string' ? l : (l.message || ''))).join('\n');
            const text = loginText + '\n' + allText;
            // 需要短信验证码（登录任务会在日志中打印提示并等待验证码文件）
            if (!smsPrompted && /短信验证|请输入.{0,12}验证码|等待短信验证码输入/.test(loginText)) {
                smsPrompted = true;
                // 替代原生 prompt：自定义模态弹窗输入验证码
                const code = await promptSmsCode();
                if (code) {
                    try {
                        await apiRequest(API.smsCode, 'POST', { code });
                        showToast('验证码已提交，登录继续中…', 'info');
                    } catch (e) {
                        showToast('验证码提交失败：' + e.message, 'error');
                        smsPrompted = false;  // 提交失败允许再次输入
                    }
                } else {
                    // 用户取消：允许后续轮询重新触发弹窗
                    smsPrompted = false;
                }
            }
            // 登录完成（积分抓取完成 / 登录成功账号）
            if (/奖励信息抓取完成|登录成功账号|登录成功，但未能读取/.test(text)) {
                done = true;
                clearInterval(_loginPollTimer);
                showToast('登录成功，积分与可兑换物品已刷新', 'success');
                if (window.refreshStatus) refreshStatus();
                if (window.loadCachedRewards) loadCachedRewards();
            }
            // 登录失败
            if (/执行异常|重新登录失败|缺少账号或密码|缺少环境变量|未获取到短信验证码/.test(text)) {
                done = true;
                clearInterval(_loginPollTimer);
                showToast('登录失败，请查看日志', 'error');
            }
        } catch (e) { /* 忽略轮询错误 */ }
    }, 5000);
}

// 短信验证码输入弹窗（2026-09-06 新增）：替代浏览器原生 prompt，
// 样式与面板模态一致（毛玻璃遮罩+卡片），支持回车提交
function promptSmsCode() {
    return new Promise((resolve) => {
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        const box = document.createElement('div');
        box.className = 'modal-box';
        box.innerHTML = `
            <div class="modal-header"><i class="mdi"><svg viewBox="0 0 24 24"><path fill="currentColor" d="M20 2H4a2 2 0 00-2 2v18l4-4h14a2 2 0 002-2V4a2 2 0 00-2-2zm0 14H5.17L4 17.17V4h16v12zM7 9h2v2H7V9zm4 0h2v2h-2V9zm4 0h2v2h-2V9z"/></svg></i><span>需要短信验证码</span></div>
            <div class="modal-body">
                <div>检测到天翼云登录需要短信验证，请输入手机收到的验证码：</div>
                <input type="text" class="form-control sms-modal-code" id="smsModalInput" maxlength="6" inputmode="numeric" placeholder="—— ——" style="margin-top:12px;text-align:center">
                <div class="sms-modal-hint">登录任务正在等待验证码（约 5 分钟内有效），提交后登录将继续。</div>
            </div>
            <div class="modal-actions">
                <button class="btn btn-ghost modal-cancel">取消</button>
                <button class="btn btn-primary modal-confirm">提交验证码</button>
            </div>`;
        overlay.appendChild(box);
        document.body.appendChild(overlay);
        requestAnimationFrame(() => overlay.classList.add('show'));
        const input = box.querySelector('#smsModalInput');
        setTimeout(() => input.focus(), 250);

        const close = (result) => {
            overlay.classList.remove('show');
            setTimeout(() => overlay.remove(), 200);
            document.removeEventListener('keydown', onKey);
            resolve(result);
        };
        const submit = () => {
            const v = (input.value || '').trim();
            if (v) close(v);
        };
        box.querySelector('.modal-cancel').addEventListener('click', () => close(null));
        box.querySelector('.modal-confirm').addEventListener('click', submit);
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') submit(); });
        const onKey = (e) => { if (e.key === 'Escape') close(null); };
        document.addEventListener('keydown', onKey);
    });
}

function updateLoginBadge(data) {
    const badge = document.getElementById('loginStateBadge');
    if (!badge) return;
    let label = '未登录', cls = 'state-off';
    if (data.cookie_exists || data.auth_data_exists) {
        label = '已登录';
        cls = 'state-on';
        if (data.last_login) label = '已登录 · ' + data.last_login;
    } else if (data.account_configured) {
        label = '账号已配置 · 未登录';
        cls = 'state-warn';
    }
    badge.textContent = label;
    badge.className = 'login-state-badge ' + cls;
}

async function clearSession() {
    if (!(await showConfirm('确定要清除所有登录状态吗？这会导致需要重新登录。', { title: '清除登录会话', confirmText: '清除', danger: true }))) return;
    try {
        const result = await apiRequest(API.clearSession, 'POST');
        showToast(result.message || '登录状态已清除', 'success');
        refreshStatus();
        loadAccountSettings();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function savePresetMessages(isAuto) {
    const presetMessages = document.getElementById('presetMessages').value;
    const messages = presetMessages ? presetMessages.split('\n').filter(s => s.trim()) : [];
    if (messages.length === 0) {
        if (isAuto) return;  // 自动保存时允许清空，静默跳过
        showToast('请至少填写一条预设消息', 'warning');
        return;
    }
    try {
        await apiRequest(API.savePresets, 'POST', { messages });
        if (isAuto) showToast('已自动保存', 'success');
        else showToast('预设消息已保存', 'success');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// ============================================
// Device Config
// ============================================
async function loadDeviceConfig() {
    try {
        const data = await apiRequest(API.getDeviceCode);
        const code = data.device_code || '未生成';
        const display = document.getElementById('deviceCodeDisplay');
        if (display) display.textContent = code;
        const statusData = await apiRequest(API.getStatus);
        const setText = (id, txt) => { const el = document.getElementById(id); if (el) el.textContent = txt; };
        setText('statusDeviceContainer', statusData.container_running ? '运行中' : '已停止');
        // 补全开关联动：保活已停用时显示「已停用」而非「未运行」（避免误解为故障）
        setText('statusDeviceCrYun', statusData.ctyun_running ? '运行中' : (statusData.keepalive_enabled === false ? '已停用' : '未运行'));
        setText('lastLoginTime', statusData.last_login || '从未登录');
        setText('statusDeviceCookie', statusData.cookie_exists ? '已缓存' : '无');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function saveDeviceCode(isAuto) {
    const code = document.getElementById('deviceCodeInput').value.trim();
    if (!code) { if (!isAuto) showToast('请先粘贴设备代码', 'warning'); return; }
    try {
        await apiRequest(API.saveDeviceCode, 'POST', { device_code: code });
        if (!isAuto) {
            showToast('设备代码已保存', 'success');
            showAlert('deviceAlert', '设备代码已保存', 'success');
            document.getElementById('deviceCodeInput').value = '';
        }
        loadDeviceConfig();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function copyText(id) {
    const el = document.getElementById(id);
    if (!el) return;
    const txt = el.textContent.trim();
    if (!txt || txt === '加载中...' || txt === '未生成') { showToast('无可复制内容', 'warning'); return; }
    navigator.clipboard.writeText(txt).then(() => showToast('已复制到剪贴板', 'success'))
        .catch(() => { const ta = document.createElement('textarea'); ta.value = txt; document.body.appendChild(ta); ta.select(); document.execCommand('copy'); ta.remove(); showToast('已复制', 'success'); });
}

// ============================================
// Cron Settings
// ============================================
async function loadCronSettings() {
    suppressAutoSave = true;
    try {
        const data = await apiRequest(API.getCron);
        // AI 对话：开关 + 时间点
        const aiEnabled = document.getElementById('aiChatEnabled');
        if (aiEnabled) aiEnabled.checked = data.ai_chat_enabled !== false;
        renderTimeChips('aiChatTimes', data.ai_chat_time || ["03:00", "20:00"]);
        // 云电脑挂机：开关 + 时间点 + 时长
        const pcEnabled = document.getElementById('pcHangEnabled');
        if (pcEnabled) pcEnabled.checked = data.pc_hang_enabled !== false;
        renderTimeChips('pcHangTimes', data.pc_hang_time || ["04:00", "06:00"]);
        const hmEl = document.getElementById('hangMinutes');
        if (hmEl) hmEl.value = data.hang_minutes || 80;
        // 保活
        const kaSec = document.getElementById('keepaliveSeconds');
        if (kaSec) kaSec.value = data.keepalive_seconds || 900;
        const kaEnabled = document.getElementById('keepaliveEnabled');
        if (kaEnabled) kaEnabled.checked = data.keepalive_enabled !== false;
        // 修复死选择器：实际容器 id 是 sub-automation（不是 sub-cron）。
        // 旧选择器永远匹配不到任何按钮，导致保活/积分刷新预设高亮全部失效。
        // 修复：限定 keepalive-group 容器。原 #sub-automation 范围内所有
        // cron-preset-btn（含挂机时长/积分刷新预设）都会被误亮；且积分刷新
        // 高亮循环应先清掉旧 active（避免旧值按钮残留高亮）。
        document.querySelectorAll('#sub-automation .keepalive-group .cron-preset-btn').forEach(btn => {
            btn.classList.toggle('active', parseInt(btn.dataset.val, 10) === parseInt(data.keepalive_seconds, 10));
        });
        // 积分刷新间隔
        const prEl = document.getElementById('pointsRefreshHours');
        if (prEl) prEl.value = data.points_refresh_hours || 8;
        document.querySelectorAll('#sub-automation .cron-preset-btn').forEach(btn => {
            if (!btn.closest('.keepalive-group')) btn.classList.remove('active');
            if (parseInt(btn.dataset.val, 10) === parseInt(data.points_refresh_hours, 10) && !btn.closest('.keepalive-group')) btn.classList.add('active');
        });
    } catch (error) {
        showToast(error.message, 'error');
    }
    suppressAutoSave = false;
}

// 渲染时间点多选芯片（容器 id + 已选列表）。
// 以「已选列表」为基准保证自定义时间（如 07:30）也能显示并选中，再补充 0-23 整点。
function renderTimeChips(containerId, selectedList) {
    const box = document.getElementById(containerId);
    if (!box) return;
    const selected = new Set((selectedList || []).map(s => String(s)));
    // 基础整点 0-23
    const hourSet = new Set();
    for (let h = 0; h < 24; h++) hourSet.add(`${String(h).padStart(2, '0')}:00`);
    // 合并：已选时间 + 整点，去重并按时间排序
    const all = new Set([...selected, ...hourSet]);
    const sorted = Array.from(all).sort();
    box.innerHTML = sorted.map(t => {
        const on = selected.has(t) ? ' active' : '';
        return `<button type="button" class="time-chip${on}" data-time="${t}">${t}</button>`;
    }).join('');
}

// 点击挂机时长预设按钮：填入 + 高亮互斥
function highlightHangMinutes(btn) {
    const input = document.getElementById('hangMinutes');
    if (input) input.value = btn.dataset.val;
    const group = btn.closest('.cron-presets-inline');
    if (group) group.querySelectorAll('.cron-preset-btn').forEach(b => b.classList.toggle('active', b === btn));
}

// 折叠/展开 Cron 说明
function toggleCronHelp() {
    const body = document.getElementById('cronHelpBody');
    const caret = document.getElementById('cronHelpCaret');
    if (!body) return;
    const hidden = body.classList.toggle('hidden');
    if (caret) caret.classList.toggle('up', !hidden);
}
(function bindCronHelpToggle() {
    const t = document.getElementById('cronHelpToggle');
    if (!t) return;
    t.addEventListener('click', toggleCronHelp);
    t.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleCronHelp(); } });
})();

// 点击 cron 预设按钮：把表达式填入对应输入框并高亮
function applyCronPreset(btn) {
    const targetId = btn.dataset.target;
    const val = btn.dataset.val;
    const el = document.getElementById(targetId);
    if (el) {
        el.value = val;
        el.focus();
    }
    // 同组预设按钮互斥高亮
    const group = btn.closest('.cron-presets-inline');
    if (group) group.querySelectorAll('.cron-preset-btn').forEach(b => b.classList.toggle('active', b === btn));
}


async function saveCronSettings(isAuto) {
    const aiEnabledEl = document.getElementById('aiChatEnabled');
    const pcEnabledEl = document.getElementById('pcHangEnabled');
    const kaSecEl = document.getElementById('keepaliveSeconds');
    const kaEnabledEl = document.getElementById('keepaliveEnabled');
    const silentEl = document.getElementById('silentMode');
    const browserEl = document.getElementById('browserWatch');
    const hmEl = document.getElementById('hangMinutes');
    const prEl = document.getElementById('pointsRefreshHours');

    const aiEnabled = aiEnabledEl ? aiEnabledEl.checked : true;
    const pcEnabled = pcEnabledEl ? pcEnabledEl.checked : true;
    const aiTimes = getSelectedTimes('aiChatTimes');
    const pcTimes = getSelectedTimes('pcHangTimes');
    const keepaliveEnabled = kaEnabledEl ? kaEnabledEl.checked : true;
    const silentMode = silentEl ? silentEl.checked : false;
    const browserWatch = browserEl ? browserEl.checked : false;

    // 修复：超范围值从"拒绝保存"改为"钳制到合法区间后保存"，
    // 避免用户输错后点保存毫无反应、输入框显示与实际配置不一致。
    // （原 const 改 let 以支持钳制回写；AI/挂机时间点非空校验保留）
    let keepaliveSec = kaSecEl ? (parseInt(kaSecEl.value, 10) || 900) : 900;
    let hangMinutes = hmEl ? (parseInt(hmEl.value, 10) || 80) : 80;
    let pointsRefreshHours = prEl ? (parseInt(prEl.value, 10) || 8) : 8;

    if (aiEnabled && aiTimes.length === 0) {
        // 修复：自动保存时空选静默返回，界面已清空但服务器仍保留旧时间点，
        // 用户误以为已生效。补提示（手动保存 error，自动保存 warning）。
        showToast('请为 AI 对话选择至少一个执行时间点', isAuto ? 'warning' : 'error');
        return;
    }
    if (pcEnabled && pcTimes.length === 0) {
        // 修复：同上，自动保存空选静默返回无任何提示，界面与服务器配置脱节
        showToast('请为云电脑挂机选择至少一个执行时间点', isAuto ? 'warning' : 'error');
        return;
    }
    if (keepaliveSec < 10 || keepaliveSec > 21600) {
        keepaliveSec = (keepaliveSec < 10) ? 10 : 21600;
        if (kaSecEl) kaSecEl.value = keepaliveSec;
        showToast('保活间隔超出范围，已调整为 ' + keepaliveSec + ' 秒', 'warning');
    }
    if (hangMinutes < 1 || hangMinutes > 720) {
        hangMinutes = (hangMinutes < 1) ? 1 : 720;
        if (hmEl) hmEl.value = hangMinutes;
        showToast('挂机时长超出范围，已调整为 ' + hangMinutes + ' 分钟', 'warning');
    }
    if (pointsRefreshHours < 1 || pointsRefreshHours > 168) {
        pointsRefreshHours = (pointsRefreshHours < 1) ? 1 : 168;
        if (prEl) prEl.value = pointsRefreshHours;
        showToast('积分刷新间隔超出范围，已调整为 ' + pointsRefreshHours + ' 小时', 'warning');
    }

    try {
        await apiRequest(API.saveCron, 'POST', {
            ai_chat_enabled: aiEnabled,
            ai_chat_time: aiTimes,
            pc_hang_enabled: pcEnabled,
            pc_hang_time: pcTimes,
            hang_minutes: hangMinutes,
            keepalive_seconds: keepaliveSec,
            keepalive_enabled: keepaliveEnabled,
            silent_mode: silentMode,
            browser_watch: browserWatch,
            points_refresh_hours: pointsRefreshHours
        });
        if (isAuto) showToast('已自动保存', 'success');
        else {
            showToast('定时任务配置已保存', 'success');
            showAlert('cronAlert', `配置已保存（保活间隔 ${keepaliveSec}秒，积分每 ${pointsRefreshHours} 小时刷新）`, 'success');
        }
        loadCronSettings();
        refreshStatus();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// 读取时间点芯片选中状态
function getSelectedTimes(containerId) {
    const box = document.getElementById(containerId);
    if (!box) return [];
    return Array.from(box.querySelectorAll('.time-chip.active')).map(b => b.dataset.time).sort();
}

// 时间点芯片点击切换
function toggleTimeChip(btn) {
    if (!btn.classList.contains('time-chip')) return;
    btn.classList.toggle('active');
}

// 自定义时间添加：校验 HH:MM 后插入芯片
function addCustomTime(containerId, raw) {
    const t = (raw || '').trim();
    if (!/^\d{1,2}:\d{2}$/.test(t)) { showToast('时间格式应为 HH:MM，如 07:30', 'error'); return; }
    const [hh, mm] = t.split(':');
    if (!(0 <= parseInt(hh, 10) && parseInt(hh, 10) <= 23 && 0 <= parseInt(mm, 10) && parseInt(mm, 10) <= 59)) {
        showToast('时间超出范围', 'error'); return;
    }
    const norm = `${String(parseInt(hh, 10)).padStart(2, '0')}:${mm}`;
    const box = document.getElementById(containerId);
    if (!box) return;
    if (box.querySelector(`.time-chip[data-time="${norm}"]`)) { showToast('该时间已存在', 'info'); return; }
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'time-chip active';
    btn.dataset.time = norm;
    btn.textContent = norm;
    box.appendChild(btn);
    // 修复：回车添加自定义时间后芯片已选中，但 value 由 onkeydown 内联清空
    // 不触发 change 事件，自动保存从未执行，刷新后新时间点丢失。
    // 成功插入芯片后显式触发一次 cron 自动保存（与点击芯片行为对齐）。
    autoSave('cron', () => saveCronSettings(true));
}

// 委托：时间点芯片点击
document.addEventListener('click', function (e) {
    const chip = e.target.closest && e.target.closest('.time-chip');
    if (chip) toggleTimeChip(chip);
});

function highlightPointsRefresh(btn) {
    const val = btn.dataset.val;
    const el = document.getElementById('pointsRefreshHours');
    if (el) el.value = val;
    const group = btn.closest('.cron-presets-inline');
    if (group) group.querySelectorAll('.cron-preset-btn').forEach(b => b.classList.toggle('active', b === btn));
}

// ============================================
// Redeem Settings (item-list based)
// ============================================
// 当前选中的物品与桌面
let selectedReward = null;   // {prodId, prodName, prodType, costPoints, description}
let rewardsData = null;      // /api/rewards 返回的数据
let pendingDesktopId = '';   // 待回填的云电脑 ID（下拉框异步填充后写入）

// 加载服务器上已缓存的 rewards.json（如果有），填充物品列表
async function loadCachedRewards() {
    try {
        const data = await apiRequest(API.getRewards);
        if (data.exists && data.timestamp) {
            rewardsData = data;
            renderRewards(data);
        } else {
            // 无缓存时自动触发一次后台抓取，让商品默认显示出来
            fetchRewards();
        }
    } catch (e) {
        // 忽略，首次进入时无缓存
    }
}

async function loadRedeemConfig() {
    suppressAutoSave = true;
    try {
        const data = await apiRequest(API.getRedeem);
        const enEl = document.getElementById('redeemEnabled');
        if (enEl) enEl.checked = data.enabled || false;
        const stEl = document.getElementById('redeemScheduleType');
        if (stEl) stEl.value = data.schedule_type || 'daily';
        updateRedeemSchedule();
        const ivEl = document.getElementById('intervalDays');
        if (ivEl && data.schedule_type === 'interval') ivEl.value = data.interval_days || 1;
        const mvEl = document.getElementById('monthlyDays');
        if (mvEl && data.schedule_type === 'monthly') mvEl.value = (data.monthly_days || []).join(',');
        const mtEl = document.getElementById('redeemMaxTimes');
        if (mtEl) mtEl.value = data.max_redeem_times ?? 0;
        // 保存待回填的 desktopId：下拉框选项由 renderRewards 异步填充，
        // 待其填充完成后再回写，避免竞态导致刷新后选不中。
        pendingDesktopId = data.desktop_id ? String(data.desktop_id) : '';
        if (rewardsData && rewardsData.exists) {
            if (data.prod_id) {
                const reward = (rewardsData.rewards || []).find(r => String(r.prodId) === String(data.prod_id));
                if (reward) { selectedReward = reward; renderRewards(rewardsData); }
            }
            renderSelectedReward();
        }
    } catch (error) {
        showToast(error.message, 'error');
    }
    suppressAutoSave = false;
}

function updateRedeemSchedule() {
    const type = document.getElementById('redeemScheduleType').value;
    const ig = document.getElementById('intervalDaysGroup');
    const mg = document.getElementById('monthlyDaysGroup');
    if (ig) ig.style.display = (type === 'interval') ? 'block' : 'none';
    if (mg) mg.style.display = (type === 'monthly') ? 'block' : 'none';
}

// 触发后台抓取可兑换物品
async function fetchRewards() {
    const listEl = document.getElementById('rewardList');
    if (listEl) listEl.innerHTML = '<div class="empty-state"><span class="spinner"></span> 正在后台抓取，需登录云电脑，约 1-2 分钟…</div>';
    try {
        await apiRequest(API.fetchRewards, 'POST');
        showAlert('redeemAlert', '正在后台抓取可兑换物品，约 1-2 分钟，请稍候…', 'warning');
        let attempts = 0;
        while (attempts < 36) {
            await new Promise(r => setTimeout(r, 5000));
            try {
                const data = await apiRequest(API.getRewards);
                if (data.exists && data.timestamp) {
                    rewardsData = data;
                    renderRewards(data);
                    showAlert('redeemAlert', '可兑换物品已加载完成', 'success');
                    return;
                }
            } catch (e) { /* 尚未生成 */ }
            attempts++;
        }
        showAlert('redeemAlert', '抓取超时，请稍后重试', 'danger');
    } catch (error) {
        showToast(error.message, 'error');
    }
}

function renderRewards(data) {
    rewardsData = data;
    const listEl = document.getElementById('rewardList');
    const rewards = data.rewards || [];
    const desktops = data.desktops || [];
    // 更新剩余积分显示
    const ptsEl = document.getElementById('rewardPointsVal');
    if (ptsEl) {
        const pts = (data.points != null) ? data.points : '--';
        ptsEl.textContent = (typeof pts === 'number') ? pts.toLocaleString() : pts;
    }
    if (rewards.length === 0) {
        listEl.innerHTML = '<div class="empty-state">未获取到可兑换物品。可能登录已过期，请重新登录。</div>';
    } else {
        listEl.innerHTML = rewards.map(r => {
            const isSel = selectedReward && String(selectedReward.prodId) === String(r.prodId);
            return `
                <div class="reward-item ${isSel ? 'selected' : ''}" data-prodid="${r.prodId}" onclick="selectReward(${r.prodId})">
                    <div class="reward-item-main">
                        <div class="reward-item-name">${escapeHtml(r.prodName || '未知物品')}</div>
                        <div class="reward-item-meta">产品ID: ${r.prodId}${r.prodType ? ' · ' + escapeHtml(r.prodType) : ''}</div>
                    </div>
                    <div class="reward-item-cost">${r.costPoints}</div>
                </div>`;
        }).join('');
        // 进入兑换页默认选中（若用户尚未选择且未从配置回填），避免“不知道怎么选”的困惑
        if (!selectedReward) {
            // 修复：默认选中不再取列表第一个，而是优先“1G 数据盘永久扩容”(17024101)。
            // 原因：该物品是用户日常默认兑换目标，列表顺序可能变化（如上新、售磨），
            // 按第一个选容易漂移到别的物品，存在误兑换风险。优先精确匹配 prodId，
            // 其次按 prodType 匹配（不同期 prodId 可能变但类型稳定），最后才退回列表第一个。
            const DEFAULT_REDEEM_PROD_ID = '17024101';   // 1G 数据盘永久扩容
            const DEFAULT_REDEEM_PROD_TYPE = 'pointsdiskupgrade';
            const defaultReward = rewards.find(r => String(r.prodId) === DEFAULT_REDEEM_PROD_ID)
                || rewards.find(r => (r.prodType || '') === DEFAULT_REDEEM_PROD_TYPE)
                || rewards[0];
            selectReward(defaultReward.prodId, true);
        }
    }
    const sel = document.getElementById('redeemDesktopId');
    if (sel) {
        if (desktops.length === 0) {
            sel.innerHTML = '<option value="">未获取到云电脑</option>';
        } else {
            sel.innerHTML = '<option value="">请选择要兑换的云电脑</option>' +
                desktops.map(d => `<option value="${d.desktopId}">${escapeHtml(d.objName || '云电脑')}（ID: ${d.desktopId}）</option>`).join('');
        }
        // 回填保存的云电脑选择（来自后端配置，等选项填充完再设置）
        if (pendingDesktopId) {
            const matched = Array.from(sel.options).find(o => o.value === pendingDesktopId);
            if (matched) sel.value = matched.value;
            pendingDesktopId = '';
        }
    }
}

// 选中某个物品（silent=true 时不弹 toast，用于进入页面时的默认选中）
function selectReward(prodId, silent = false) {
    if (!rewardsData) return;
    const reward = (rewardsData.rewards || []).find(r => String(r.prodId) === String(prodId));
    if (!reward) return;
    selectedReward = reward;
    // 高亮
    document.querySelectorAll('.reward-item').forEach(el => {
        el.classList.toggle('selected', String(el.dataset.prodid) === String(prodId));
    });
    renderSelectedReward();
    if (!silent) showToast(`已选择：${reward.prodName}（${reward.costPoints} 积分）`, 'success');
}

// 显示自动带出的物品信息
function renderSelectedReward() {
    const timesHint = document.getElementById('redeemTimesHint');
    if (!selectedReward) {
        if (timesHint) timesHint.textContent = '请在左侧列表中选择要兑换的物品';
        return;
    }
    if (rewardsData && rewardsData.points > 0 && selectedReward.costPoints > 0) {
        const maxTimes = Math.floor(rewardsData.points / selectedReward.costPoints);
        timesHint.textContent = `当前积分 ${rewardsData.points}，此物品最多可兑换 ${maxTimes} 次（填 0 即全部按此兑换）`;
        if (maxTimes <= 0) {
            timesHint.textContent = `当前积分 ${rewardsData.points} 不足以兑换此物品（需 ${selectedReward.costPoints} 积分）`;
        }
    } else {
        timesHint.textContent = '已选物品，可设置兑换次数（0=按积分尽量兑）';
    }
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str == null ? '' : String(str);
    return div.innerHTML;
}

async function saveRedeemSettings(isAuto) {
    const desktopId = document.getElementById('redeemDesktopId').value.trim();
    const enabled = document.getElementById('redeemEnabled').checked;

    if (!selectedReward) {
        if (!isAuto) showToast('请先在列表中选择要兑换的物品', 'warning');
        return;
    }
    if (!desktopId) {
        if (!isAuto) showToast('请选择要兑换到哪个云电脑', 'warning');
        return;
    }

    const config = {
        enabled: enabled,
        schedule_type: document.getElementById('redeemScheduleType').value,
        interval_days: parseInt(document.getElementById('intervalDays').value) || 1,
        monthly_days: document.getElementById('monthlyDays').value
            ? document.getElementById('monthlyDays').value.split(',').map(d => parseInt(d.trim())).filter(n => !isNaN(n))
            : [],
        prod_id: selectedReward.prodId,
        prod_name: selectedReward.prodName,
        prod_type: selectedReward.prodType || '',
        cost_points: selectedReward.costPoints,
        max_redeem_times: parseInt(document.getElementById('redeemMaxTimes').value) || 0,
        desktop_id: desktopId
    };

    try {
        await apiRequest(API.saveRedeem, 'POST', config);
        if (isAuto) showToast('已自动保存', 'success');
        else {
            showToast('兑换配置已保存', 'success');
            showAlert('redeemAlert', '积分兑换配置已保存成功', 'success');
        }
        loadRedeemConfig();
        refreshStatus();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function disableRedeem() {
    if (!(await showConfirm('确定要禁用自动兑换功能吗？', { title: '禁用自动兑换', confirmText: '禁用', danger: true }))) return;
    try {
        await apiRequest(API.disableRedeem, 'POST');
        showToast('自动兑换已禁用', 'success');
        loadRedeemConfig();
        refreshStatus();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

async function manualRedeem() {
    const maxTimes = parseInt(document.getElementById('redeemMaxTimes').value) || 0;
    const cost = selectedReward ? selectedReward.costPoints : 0;
    const points = rewardsData ? rewardsData.points : 0;
    const timesDesc = maxTimes === 0
        ? (cost > 0 && points > 0 ? `按当前积分（${points}）最多可兑 ${Math.floor(points / cost)} 次` : '按当前积分尽量兑')
        : `指定兑换 ${maxTimes} 次`;

    if (!(await showConfirm(`确认要立即兑换「${selectedReward ? selectedReward.prodName : '所选物品'}」吗？<br>${timesDesc}。<br><br>点击确定将真实下单消耗积分。`, { title: '确认兑换', confirmText: '确认兑换', danger: true }))) return;

    const btn = document.getElementById('btnManualRedeem');
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> 兑换中...';
    try {
        await apiRequest(API.executeTask, 'POST', { task: 'redeem' });
        showToast('兑换任务已触发，正在后台执行', 'success');
        // 打开实时进度弹窗并轮询兑换日志
        openRedeemProgress();
    } catch (error) {
        showToast(error.message, 'error');
    } finally {
        btn.disabled = false;
        btn.innerHTML = '<i class="mdi mdi-gift-outline"></i> 手动触发兑换';
        if (window.renderIcons) window.renderIcons(btn);
    }
}

// ============================================
// Redeem Progress Modal (实时显示兑换执行日志)
// ============================================
let redeemPollTimer = null;
function openRedeemProgress() {
    const modal = document.getElementById('redeemProgressModal');
    const logEl = document.getElementById('redeemProgressLog');
    const statusEl = document.getElementById('redeemProgressStatus');
    logEl.textContent = '正在连接后台，获取兑换执行日志…';
    statusEl.textContent = '正在执行兑换，请稍候…';
    statusEl.className = 'redeem-progress-status running';
    modal.style.display = 'flex';
    // 每 2 秒轮询兑换日志
    redeemPollTimer = setInterval(pollRedeemProgress, 2000);
    pollRedeemProgress();
}

async function pollRedeemProgress() {
    const logEl = document.getElementById('redeemProgressLog');
    const statusEl = document.getElementById('redeemProgressStatus');
    try {
        const data = await apiRequest(`${API.getLogs}?type=redeem_log`);
        const lines = (data.logs || []).slice();
        // 只显示本次兑换相关日志（从最近一次"手动兑换模式"开始）
        const startIdx = findLastRedeemStart(lines);
        const shown = startIdx >= 0 ? lines.slice(startIdx) : lines.slice(-40);
        logEl.textContent = shown.join('\n') || '暂无日志';
        logEl.scrollTop = logEl.scrollHeight;
        // 检测是否完成
        if (hasRedeemFinished(shown)) {
            const isFail = shown.some(l => l.includes('未成功') || l.includes('失败'));
            statusEl.textContent = isFail ? '兑换未成功，请查看上方日志' : '兑换流程已结束，正在刷新积分...';
            statusEl.className = 'redeem-progress-status ' + (isFail ? 'failed' : 'success');
            stopRedeemPoll();
            if (!isFail) {
                // 积分有变动 → 自动刷新可兑换物品与积分
                showToast('积分发生变化，正在重新抓取最新数据...', 'info');
                setTimeout(fetchRewards, 1500);
            }
        }
    } catch (e) {
        logEl.textContent = '读取兑换日志失败: ' + e.message;
    }
}

function findLastRedeemStart(lines) {
    for (let i = lines.length - 1; i >= 0; i--) {
        if (lines[i].includes('手动兑换模式') || lines[i].includes('开始进行云电脑挂机')) {
            return i;
        }
    }
    return -1;
}

function hasRedeemFinished(lines) {
    return lines.some(l =>
        l.includes('兑换流程结束') ||
        l.includes('手动兑换未成功') ||
        l.includes('手动兑换失败') ||
        l.includes('兑换任务已提前退出') ||
        l.includes('强制退出')
    );
}

function stopRedeemPoll() {
    if (redeemPollTimer) {
        clearInterval(redeemPollTimer);
        redeemPollTimer = null;
    }
}

function closeRedeemProgress() {
    stopRedeemPoll();
    document.getElementById('redeemProgressModal').style.display = 'none';
}

// ============================================
// Task Execution
// ============================================
async function executeTask(task) {
    // 保活日志按钮 - 跳转到日志页面
    if (task === 'keepalive_log') {
        navigateTo('logs');
        const keepBtn = document.querySelector('#logTypeButtons .cron-preset-btn:nth-child(2)');
        setLogType('keepalive_log', keepBtn);
        return;
    }

    const buttonMap = {
        'ai_chat': null,
        'pc_hang': 'btnPcHang',
        'redeem': null
    };
    const btnId = buttonMap[task];
    const btn = btnId ? document.getElementById(btnId) : null;
    const originalHTML = btn ? btn.innerHTML : '';

    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> 执行中...';
    }

    try {
        const result = await apiRequest(API.executeTask, 'POST', { task });
        showToast(result.message || '任务已触发', 'success');
        setTimeout(() => refreshStatus(), 2000);
    } catch (error) {
        showToast(error.message, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalHTML;
            if (window.renderIcons) window.renderIcons(btn);
        }
    }
}

// ============================================
// Logs
// ============================================
function setLogType(type, btn) {
    currentLogType = type;
    document.querySelectorAll('#logTypeButtons .cron-preset-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    loadLogs();
}

async function loadLogs() {
    try {
        const data = await apiRequest(`${API.getLogs}?type=${currentLogType}`);
        const viewer = document.getElementById('logViewer');
        if (data.logs && data.logs.length > 0) {
            viewer.textContent = data.logs.join('\n');
        } else {
            viewer.textContent = '暂无日志';
        }
        viewer.scrollTop = viewer.scrollHeight;
    } catch (error) {
        document.getElementById('logViewer').textContent = '加载日志失败: ' + error.message;
    }
}

async function clearLogs() {
    if (!(await showConfirm('确定要清空所有运行日志吗？此操作不可恢复。', { title: '清空日志', confirmText: '清空', danger: true }))) return;
    try {
        await apiRequest(API.clearLogs, 'POST');
        showToast('日志已清空', 'success');
        loadLogs();
    } catch (error) {
        showToast(error.message, 'error');
    }
}

// ============================================
// Init
// ============================================
document.addEventListener('DOMContentLoaded', function() {
    initTheme();
    loadNotifySettings();
    restoreCardCollapse();
    refreshStatus();

    // Staggered entrance for the initial dashboard stat cards
    document.querySelectorAll('#page-dashboard .stat-card').forEach((card, i) => {
        setTimeout(() => card.classList.add('in'), 80 + i * 70);
    });

    // Account settings page buttons
    loadAccountSettings();
    // 修复：三处手动保存按钮此前直接把函数名传给 addEventListener，
    // 事件对象 MouseEvent 被当成 isAuto 参数传入（truthy），导致：
    // ① 空表单保存不弹提示直接静默返回；② 保存成功只弹"已自动保存"。
    // 改为包箭头函数显式传 false（手动保存）。
    const btnSaveSettings = document.getElementById('btnSaveSettings');
    if (btnSaveSettings) btnSaveSettings.addEventListener('click', () => saveAccountSettings(false));
    const btnTestLogin = document.getElementById('btnTestLogin');
    if (btnTestLogin) btnTestLogin.addEventListener('click', testLogin);
    const btnClearSession = document.getElementById('btnClearSession');
    if (btnClearSession) btnClearSession.addEventListener('click', clearSession);
    const btnGenDeviceCode = document.getElementById('btnGenDeviceCode');
    if (btnGenDeviceCode) btnGenDeviceCode.addEventListener('click', generateDeviceCode);
    const btnSaveDeviceCode = document.getElementById('btnSaveDeviceCode');
    if (btnSaveDeviceCode) btnSaveDeviceCode.addEventListener('click', () => saveAccountDeviceCode(false));
    const btnSavePresets = document.getElementById('btnSavePresets');
    if (btnSavePresets) btnSavePresets.addEventListener('click', () => savePresetMessages(false));

    // 面板安全卡（2026-09-06 新增）：开关/改密码/退出登录
    const swPanelAuth = document.getElementById('panelAuthEnabled');
    if (swPanelAuth) swPanelAuth.addEventListener('change', togglePanelAuth);
    const btnChangePwd = document.getElementById('btnPanelAuthChangePwd');
    if (btnChangePwd) btnChangePwd.addEventListener('click', openPanelAuthModal);
    const btnPanelLogout = document.getElementById('btnPanelAuthLogout');
    if (btnPanelLogout) btnPanelLogout.addEventListener('click', panelAuthLogout);

    // 修改面板账号弹窗（2026-09-07 交互升级）：取消/保存/密码眼睛/回车提交/Esc 关闭
    const modalCancel = document.getElementById('panelAuthModalCancel');
    if (modalCancel) modalCancel.addEventListener('click', closePanelAuthModal);
    const modalSave = document.getElementById('panelAuthModalSave');
    if (modalSave) modalSave.addEventListener('click', submitPanelAuthModal);
    bindPwdEye('modalNewPwdEye', 'modalNewPwd');
    bindPwdEye('modalNewPwdEye2', 'modalNewPwd2');
    const modal = document.getElementById('panelAuthModal');
    if (modal) {
        modal.addEventListener('click', (e) => { if (e.target === modal) closePanelAuthModal(); });
        modal.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && e.target.tagName === 'INPUT') { e.preventDefault(); submitPanelAuthModal(); }
            if (e.key === 'Escape') closePanelAuthModal();
        });
    }

    // 面板登录遮罩表单（2026-09-07 默认账号模式：仅登录表单，无 setup）
    const authLoginForm = document.getElementById('panelAuthLoginForm');
    if (authLoginForm) authLoginForm.addEventListener('submit', handlePanelAuthLogin);

    // 积分趋势：范围切换 + 手动刷新
    const trendRange = document.getElementById('trendRange');
    if (trendRange) trendRange.addEventListener('click', e => {
        const btn = e.target.closest('.trend-range-btn');
        if (!btn) return;
        _ptsRange = btn.dataset.range;
        trendRange.querySelectorAll('.trend-range-btn').forEach(b => b.classList.toggle('active', b === btn));
        drawPointsTrend();
    });
    const btnPointsRefresh = document.getElementById('btnPointsRefresh');
    if (btnPointsRefresh) btnPointsRefresh.addEventListener('click', async () => {
        try {
            const d = await apiRequest(API.pointsHistory);
            renderPointsTrend(d.history || []);
            showToast('积分数据已刷新', 'success');
        } catch (error) {
            showToast('积分数据刷新失败：' + error.message, 'error');
        }
    });

    // 自动保存：设置项改动后立即保存并生效，无需再点“保存”按钮（防抖触发）
    // 账号子页：账号/密码/预设消息输入、账号侧设备代码
    const subAccount = document.getElementById('sub-account');
    if (subAccount) {
        subAccount.addEventListener('input', (e) => {
            const id = e.target.id;
            if (id === 'username' || id === 'password') {
                autoSave('account', () => saveAccountSettings(true));
            } else if (id === 'presetMessages') {
                autoSave('presets', () => savePresetMessages(true));
            } else if (id === 'accountDeviceCode') {
                autoSave('accountDeviceCode', () => saveAccountDeviceCode(true));
            }
        });
    }
    // 手动粘贴设备代码：失焦即保存
    const deviceCodeInput = document.getElementById('deviceCodeInput');
    if (deviceCodeInput) {
        deviceCodeInput.addEventListener('blur', () => saveDeviceCode(true));
    }

    // 自动化子页（任务调度）：开关/数字 change + 芯片/预设按钮 click 都自动保存
    const subAuto = document.getElementById('sub-automation');
    if (subAuto) {
        subAuto.addEventListener('change', () => autoSave('cron', () => saveCronSettings(true)));
        subAuto.addEventListener('click', (e) => {
            // 时间芯片点击、时长/保活/积分预设按钮点击都触发保存
            if (e.target.closest('.time-chip') || e.target.closest('.cron-preset-btn')) {
                autoSave('cron', () => saveCronSettings(true));
            }
        });
        // 自定义时间输入框（回车添加芯片后）也触发保存
        const aiCustom = document.getElementById('aiChatCustom');
        if (aiCustom) aiCustom.addEventListener('change', () => autoSave('cron', () => saveCronSettings(true)));
        const pcCustom = document.getElementById('pcHangCustom');
        if (pcCustom) pcCustom.addEventListener('change', () => autoSave('cron', () => saveCronSettings(true)));
    }

    // 兑换页：开关/下拉/数字 change + 物品选择 click 都自动保存
    // 修复：实际渲染的物品元素类名是 .reward-item（旧代码监听 .reward-card 永远匹配不到，
    // 点击物品从不触发自动保存）
    const pageRedeem = document.getElementById('page-redeem');
    if (pageRedeem) {
        pageRedeem.addEventListener('change', () => autoSave('redeem', () => saveRedeemSettings(true)));
        pageRedeem.addEventListener('click', (e) => {
            if (e.target.closest('.reward-item')) {
                autoSave('redeem', () => saveRedeemSettings(true));
            }
        });
    }

    // Auto refresh status every 30 seconds
    setInterval(refreshStatus, 30000);

    // 面板鉴权检查（2026-09-06 新增）：放到初始化最后。
    // 需要登录时展示全屏遮罩拦截操作；无需登录（桌面版/未启用）时什么都不做，
    // 上面已有的数据加载逻辑正常执行。
    initPanelAuth();
});