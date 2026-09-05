// 内联 SVG 图标库（替代 mdi 外部字体，零外网依赖）
// 在 DOMContentLoaded 后调用 renderIcons() 把 <i class="mdi mdi-xxx"> 渲染为 SVG
(function () {
  const P = {
    'view-dashboard': 'M13 3v8h8V3h-8zm0 10h8v8h-8v-8zM3 13h8V3H3v10zm0 10h8V13H3v10z',
    'account-circle': 'M12 2a10 10 0 100 20 10 10 0 000-20zm0 4a4 4 0 110 8 4 4 0 010-8zm0 14a8 8 0 01-6.3-3.1c.9-2 3-3.4 6.3-3.4s5.4 1.4 6.3 3.4A8 8 0 0112 20z',
    // 设置页齿轮（保留原版，但新增自动化页用的调度图标已含时钟）
    'cog': 'M12 8a4 4 0 100 8 4 4 0 000-8zm9.4 4c0-.6-.1-1.2-.2-1.8l2-1.6-2-3.4-2.4 1a7.5 7.5 0 00-3-1.7L15.4 1h-3.8l-.6 2.9c-1.1.3-2.1.9-3 1.7l-2.4-1-2 3.4 2 1.6c-.1.6-.2 1.2-.2 1.8s.1 1.2.2 1.8l-2 1.6 2 3.4 2.4-1c.9.8 1.9 1.4 3 1.7l.6 2.9h3.8l.6-2.9c1.1-.3 2.1-.9 3-1.7l2.4 1 2-3.4-2-1.6c.1-.6.2-1.2.2-1.8z',
    'clock-outline': 'M12 2a10 10 0 100 20 10 10 0 000-20zm0 18a8 8 0 110-16 8 8 0 010 16zm1-13h-2v6l5 3 .9-1.6L13 11V7z',
    // 下次挂机：时钟 + 播放角标（体现"定时启动挂机"属性）
    'clock-play': 'M12 2a10 10 0 100 20 10 10 0 000-20zm0 18a8 8 0 110-16 8 8 0 010 16zm-2-12.5l6 4.5-6 4.5v-9z',
    'gift-outline': 'M20 7h-2.2A3 3 0 0012 3.8 3 3 0 006.2 7H4a1 1 0 00-1 1v3a1 1 0 001 1h1v8a1 1 0 001 1h12a1 1 0 001-1v-8h1a1 1 0 001-1V8a1 1 0 00-1-1zm-6 0h-4a1 1 0 010-2 1 1 0 011-1 1 1 0 011 1h.5V7zm-2 12h-3v-7h3v7zm5 0h-3v-7h3v7z',
    'text-box-outline': 'M3 5a2 2 0 012-2h14a2 2 0 012 2v14a2 2 0 01-2 2H5a2 2 0 01-2-2V5zm2 0v14h14V5H5zm2 3h10v2H7V8zm0 4h10v2H7v-2zm0 4h7v2H7v-2z',
    'refresh': 'M17.65 6.35A8 8 0 105.5 16h2.4A6 6 0 1116 7.7L13 11h7V4l-2.35 2.35z',
    'palette-cog': 'M12 2a10 10 0 00-3 19.6v1.4a1 1 0 001 1c.6 0 1-.4 1-1v-.5c.6.3 1.3.5 2 .5h1a1 1 0 100-2H13a4 4 0 01-4-4c0-2.2 1.8-4 4-4s4 1.8 4 4a3 3 0 103 3 1 1 0 102 0 5 5 0 00-5-5 6 6 0 00-6-6 6 6 0 00-6 6c0 .2 0 .4.1.6C5.3 9.1 6.5 8 8 8c1.7 0 3 1.3 3 3s-1.3 3-3 3c-.5 0-.9-.1-1.3-.3A3 3 0 007 16a1 1 0 01-.8-.4 5 5 0 01-1.5-2.1 1 1 0 01-.7.5v-1.7a2 2 0 00-1-1.7 1 1 0 01-.5-.8V9.1c0-.5.4-.9.9-1A10 10 0 1112 2zm1 18a3 3 0 006 0 3 3 0 00-6 0z',
    'message-outline': 'M20 4H4a2 2 0 00-2 2v12a2 2 0 002 2h14l4 4V6a2 2 0 00-2-2zm-2 12H6v-2h12v2zm0-3H6v-2h12v2zm0-3H6V8h12v2z',
    'laptop': 'M4 5h16a1 1 0 011 1v9h2v2H1v-2h2V6a1 1 0 011-1zm2 2v7h12V7H6z',
    // 云电脑挂机：云（上）+显示器（下）组合（体现"云端桌面"属性，替代原普通 laptop）
    'cloud-laptop': 'M6.5 9a3.5 3.5 0 01.6-6.9A4 4 0 0114.9 3 3.3 3.3 0 0116 9H6.5zM4 11h16a1 1 0 011 1v6a1 1 0 01-1 1H4a1 1 0 01-1-1v-6a1 1 0 011-1zm2 2v4h12v-4H6zM2 21h20v1.5H2V21z',
    'gift': 'M20 7h-2.2A3 3 0 0012 3.8 3 3 0 006.2 7H4a1 1 0 00-1 1v3a1 1 0 001 1h1v8a1 1 0 001 1h12a1 1 0 001-1v-8h1a1 1 0 001-1V8a1 1 0 00-1-1zm-6 0h-4a1 1 0 010-2 1 1 0 011-1 1 1 0 011 1h.5V7zm-2 12h-3v-7h3v7zm5 0h-3v-7h3v7z',
    'logout': 'M16 13v-2H7V6H5v12h2v-5h9v-2l4 3-4 3zM11 3H4a2 2 0 00-2 2v14a2 2 0 002 2h7v-2H4V5h7V3z',
    'information-outline': 'M11 9h2V7h-2v2zm1 13A10 10 0 1112 2a10 10 0 010 20zm0-18a8 8 0 100 16 8 8 0 000-16zm-1 13h2v-6h-2v6z',
    'content-save': 'M17 3H5a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2V7l-4-4zm-5 16a3 3 0 113-3 3 3 0 01-3 3zm3-9a1 1 0 110-2 1 1 0 010 2z',
    'content-copy': 'M19 3h-3V1H8a2 2 0 00-2 2v2H3a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-3h2a2 2 0 002-2V5a2 2 0 00-2-2zM8 3h8v2H8V3zm12 16H6V6h14v13zm2-12h-2v12H5V5h2V3h13a2 2 0 012 2v2z',
    'auto-fix': 'M3 17l3-3 4 4-3 3H4a1 1 0 01-1-1v-3zm14.7-9.7a1 1 0 010 1.4l-1.4 1.4-1.9-1.9-1 1 1.9 1.9-1.4 1.4-1.9-1.9-1 1 1.9 1.9-1.4 1.4-1.9-1.9-1 1L9 17.6l-1.4 1.4-1.9-1.9-1 1 1.9 1.9-1.4 1.4-1.9-1.9-1 1 3 3 13-13a1 1 0 010-1.4l-2.6-2.6z',
    'calendar-clock': 'M19 4h-1V2h-2v2H8V2H6v2H5a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2V6a2 2 0 00-2-2zm0 16H5V10h14v10zm0-12H5V6h14v2zM12 12l4 2.5-1.5 2.5L11 15v4h-2v-6l3-1z',
    'tune-variant': 'M4 3v3h3V3H4zm0 6v3h3V9H4zm0 6v3h3v-3H4zm6-12v3h3V3h-3zm0 6v3h3V9h-3zm0 6v3h3v-3h-3zm6 0v3h3v-3h-3zm0-6v3h3V9h-3zm0-6v3h3V3h-3z',
    'stop': 'M12 2a10 10 0 100 20 10 10 0 000-20zm0 14a4 4 0 110-8 4 4 0 010 8z',
    'calendar': 'M19 4h-1V2h-2v2H8V2H6v2H5a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2V6a2 2 0 00-2-2zm0 16H5V10h14v10zm0-12H5V6h14v2z',
    'delete-sweep': 'M15 2H6a2 2 0 00-2 2v3h2V4h9v3h2V4a2 2 0 00-2-2zM4 8h16l-1.5 14H5.5L4 8zm6 3v8h2v-8H10zm4 0v8h2v-8h-2z',
    // 兑换进度：循环交换双箭头（比圆圈箭头更明确"积分↔商品"）
    'swap-horizontal-circle-outline': 'M12 2a10 10 0 100 20 10 10 0 000-20zm0 2a8 8 0 110 16 8 8 0 010-16zM7 9h8V7l3.5 3L15 13v-2H8V9zm2 8h8l1.5-2 1 1.5L17 17v2H9v-2zm-2-6h3l1 2H7l-1-2z',
    'close': 'M18.3 5.7L12 12l6.3 6.3-1.4 1.4L10.6 13.4 4.3 19.7 2.9 18.3 9.2 12 2.9 5.7 4.3 4.3l6.3 6.3 6.3-6.3 1.4 1.4z',
    'account-check-outline': 'M10 13a5 5 0 015-5h5a5 5 0 015 5v1h-2v-1a3 3 0 00-3-3h-5a3 3 0 00-3 3v1H10v-1zm-5 9a8 8 0 018-8v2a6 6 0 00-6 6H5zm14.5-9.5l1.5 1.5 3-3-1.4-1.4-1.6 1.6-0.6-0.6L17 9.9l1.5 1.6z',
    'palette-swatch': 'M16 6a2 2 0 11-2-2 2 2 0 012 2zm-4 7a2 2 0 11-2-2 2 2 0 012 2zm7-4a2 2 0 11-2-2 2 2 0 012 2zm-6-6a5 5 0 00-5 5c0 .3.1.5.2.7l-3 3.5a2 2 0 00.3 2.8 2 2 0 002.8-.3l.9-1a4.9 4.9 0 003.8 1.3V15a2 2 0 010 4H6a2 2 0 01-2-2v-8a6 6 0 116 6v-5a2 2 0 00-2-2zm8-2a2 2 0 11-2-2 2 2 0 012 2zm-1 7a2 2 0 11-2-2 2 2 0 012 2zm-2 7a2 2 0 11-2-2 2 2 0 012 2z',
    'restore': 'M13 3a9 9 0 00-9 9H1l4 4 4-4H6a7 7 0 117 7 7 7 0 01-6-3.2l-1.7 1A9 9 0 1022 12 9 9 0 0013 3zm-1 5v5l4 2 .8-1.4-3.3-1.6V8h-1.5z',
    'bell-ring-outline': 'M10 21a2 2 0 004 0h-4zm-6-4v-1l2-2v-4a6 6 0 013-5.2V4a3 3 0 016 0v.8A6 6 0 0118 10v4l2 2v1H4zm14.2-6c0-1-.2-1.9-.6-2.8.6-.5 1-1.3 1-2.2a3.7 3.7 0 00-3.6-3.6c-.2 0-.4 0-.6.1.1-.4.2-.9.2-1.3a4 4 0 00-8 0c0 .4.1.9.2 1.3-.2 0-.4-.1-.6-.1a3.7 3.7 0 00-3.6 3.6c0 .9.4 1.7 1 2.2-.4.9-.6 1.8-.6 2.8V16l-2 2v1h18v-1l-2-2v-5z',
    // 积分趋势：折线图 + 趋势箭头（比纯折线更"活"）
    'chart-line': 'M3 3h2v16h16v2H3V3zm13.2 3.2L12.7 9.8l-2.4-2.4L5.6 12l1.4 1.4 3.2-3.2 2.4 2.4 5.2-5.6 1.5 1.4-2.3-3.3-1 1.1z',
    // 保活：Wi-Fi 信号弧（体现"周期重连维持会话"属性，替代原 EKG 心电线）
    'heart-pulse': 'M12 4C7.9 4 4.1 5.5 1.3 8l1.4 1.4C5.1 7.2 8.4 5.9 12 5.9s6.9 1.3 9.3 3.5L22.7 8C19.9 5.5 16.1 4 12 4zm0 4.5c-2.9 0-5.6 1.1-7.6 2.9l1.4 1.4c1.7-1.5 3.9-2.4 6.2-2.4s4.5.9 6.2 2.4l1.4-1.4c-2-1.8-4.7-2.9-7.6-2.9zm0 4.4c-1.7 0-3.3.7-4.5 1.8L9 16c.8-.7 1.8-1.1 3-1.1s2.2.4 3 1.1l1.5-1.3c-1.2-1.1-2.8-1.8-4.5-1.8zm0 4.2a1.6 1.6 0 110 3.2 1.6 1.6 0 010-3.2z',
    'check-circle': 'M12 2a10 10 0 100 20 10 10 0 000-20zm-2 15l-5-5 1.4-1.4L10 14.2l7.6-7.6L19 8l-9 9z',
    'alert-circle': 'M12 2a10 10 0 100 20 10 10 0 000-20zm-1 5h2v8h-2V7zm0 10h2v2h-2v-2z',
    'chevron-down': 'M7.4 8.6L12 13.2l4.6-4.6L18 10l-6 6-6-6 1.4-1.4z',
    'dice-5': 'M5 3a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2V5a2 2 0 00-2-2H5zm3 5a1 1 0 110 2 1 1 0 010-2zm4 0a1 1 0 110 2 1 1 0 010-2zm4 0a1 1 0 110 2 1 1 0 010-2zM8 16a1 1 0 110-2 1 1 0 010 2zm4 0a1 1 0 110-2 1 1 0 010 2zm4 0a1 1 0 110-2 1 1 0 010 2zm-8-4a1 1 0 110-2 1 1 0 010 2zm8 0a1 1 0 110-2 1 1 0 010 2z',
    'devices': 'M3 6a2 2 0 012-2h12a2 2 0 012 2v4h2a2 2 0 012 2v6a2 2 0 01-2 2H9a2 2 0 01-2-2v-2H3a2 2 0 01-2-2V6zm2 0v6h6V6H5zm8 0v4h6V6h-6zm-2 8v4h9v-4h-9zm-4 1v1h2v-1h-2zm-1 0H2v1h2v-1z',
    'file-eye-outline': 'M6 2h9l5 5v3h-2V8h-4V4H6v16h5v2H6a2 2 0 01-2-2V4a2 2 0 012-2zm15 15a2 2 0 110 4 2 2 0 010-4zm-2.5 2.5c-.5 0-.8.1-1.1.2 1.1.6 1.8 1.5 2.1 2.5h2c-.3-1.6-1.4-2.7-3-2.7zm-3.6-.2l.9.6c.5-.5 1.2-.8 1.9-.9l-.7-1c-1-.2-1.9 0-2.1 1.3zm.6.5c.4.9.9 1.7 1.6 2.4l.9-.6c-.9-1-1.4-2-1.6-3l-.9 1.2zm4.3-2.4l1 .2c.1-.7.1-1.4 0-2.1l-1 .2c.1.6.1 1.2 0 1.7z',
    // 运行日志：终端提示符（描边风格，方框 + > + 短横线，体现"命令行输出"属性）
    'console-log': { s: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l4 3-4 3"/><path d="M13 15h4"/>' },
    'format-list-bulleted': 'M4 5a1 1 0 110 2 1 1 0 010-2zm0 6a1 1 0 110 2 1 1 0 010-2zm0 6a1 1 0 110 2 1 1 0 010-2zm4-11h12v2H8V6zm0 6h12v2H8v-2zm0 6h12v2H8v-2z',
    'information': 'M12 2a10 10 0 100 20 10 10 0 000-20zm-1 5h2v2h-2V7zm0 4h2v6h-2v-6z',
    // AI 对话：对话气泡 + 星芒（体现"AI"属性，替代原机器人头）
    'robot': 'M4 4h13a2 2 0 012 2v7a2 2 0 01-2 2H9.5L6 18.5V15H4a2 2 0 01-2-2V6a2 2 0 012-2zm10.45 2.1l-1.2 2.55-2.85.4 2.1 2-.5 2.85 2.5-1.35 2.5 1.35-.5-2.85 2.1-2-2.85-.4-1.2-2.55zM4 7v1.5h6V7H4z', /* 修改：星芒坐标放大 ~10%，更醒目（评审建议） */
    'send-check': 'M2 3l20 9-8 4-1 7-4-3-2 2-2-5-5-1L2 3zm4 9l4 1 7-5-11 4zm3 5l1 4 2-3-3-1z',
    'star-circle': 'M12 2a10 10 0 100 20 10 10 0 000-20zm0 2a8 8 0 110 16 8 8 0 010-16zm0 3l1.8 3.6 4 .6-2.9 2.8.7 4-3.6-1.9-3.6 1.9.7-4L6.2 11.2l4-.6L12 7z'
  };

  // 图标支持三种值：
  //  - string        → 单 path（fill 规则，与旧版兼容）
  //  - {f: string}   → 单 path 且使用 fill-rule="evenodd"（挖空类图标）
  //  - {s: string}   → stroke 风格原始 SVG 内部标记（描边类图标）
  function svgFor(name) {
    const v = P[name];
    if (!v) return '';
    if (typeof v === 'object' && v.s) {
      return '<svg viewBox="0 0 24 24" width="1em" height="1em" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + v.s + '</svg>';
    }
    const d = typeof v === 'object' ? v.f : v;
    const rule = (typeof v === 'object' && v.f) ? ' fill-rule="evenodd"' : '';
    return '<svg viewBox="0 0 24 24" width="1em" height="1em" fill="currentColor"' + rule + ' aria-hidden="true">' +
      '<path d="' + d + '"/></svg>';
  }

  window.icons = { svgFor: svgFor };

  // 把 <i class="mdi mdi-xxx"></i> 渲染为内联 SVG
  window.renderIcons = function renderIcons(root) {
    const scope = root || document;
    const nodes = scope.querySelectorAll('i.mdi');
    nodes.forEach(function (el) {
      let name = '';
      el.classList.forEach(function (c) {
        if (c.indexOf('mdi-') === 0 && c !== 'mdi') name = c.slice(4);
      });
      if (name && P[name]) {
        el.innerHTML = svgFor(name);
        el.classList.add('ico-rendered');
      }
    });
  };

  // 自动渲染：DOMContentLoaded 时 + 资源加载完成后（兜底）
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { window.renderIcons(); });
  } else {
    window.renderIcons();
  }
  window.addEventListener('load', function () { window.renderIcons(); });
})();
