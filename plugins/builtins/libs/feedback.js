// libs/feedback.js
// @name: feedback
// @desc: Loading(msg) — 加载提示; ErrorBox(msg) — 错误提示; SuccessBox(msg) — 成功提示
// @example: container.appendChild(Loading('正在加载...')); container.appendChild(ErrorBox('出错了'));


function Loading(msg) { var d = document.createElement('div'); d.style.cssText = 'text-align:center;padding:40px;color:#a0a0a0;'; d.textContent = '⏳ ' + (msg || '加载中...'); return d; }
function ErrorBox(msg) { var d = document.createElement('div'); d.style.cssText = 'color:#e94560;text-align:center;padding:20px;background:rgba(233,69,96,0.1);border-radius:8px;'; d.textContent = '❌ ' + msg; return d; }
function SuccessBox(msg) { var d = document.createElement('div'); d.style.cssText = 'color:#4CAF50;text-align:center;padding:20px;font-weight:bold;background:rgba(76,175,80,0.1);border-radius:8px;'; d.textContent = '✅ ' + msg; return d; }