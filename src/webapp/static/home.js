/* 智学引擎 · 首页: 仪表盘数据 + 大脑光效 + 趋势图 */
'use strict';

const $ = id => document.getElementById(id);

function toast(msg) {
  const t = $('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._tm);
  t._tm = setTimeout(() => t.classList.remove('show'), 2200);
}

/* ---------- 大脑主视觉: 全息神经球 (assets/brain.png 存在时改用图片) ---------- */
function brainCanvas() {
  const cv = $('brainCv'), ctx = cv.getContext('2d');
  const W = cv.width, H = cv.height, CX = W / 2, CY = H / 2 - 18;
  const rand = (n => () => (n = (n * 9301 + 49297) % 233280) / 233280)(20260108);
  // 神经节点: 椭球体内
  const nodes = Array.from({length: 72}, () => {
    const a = rand() * Math.PI * 2, r = Math.pow(rand(), 0.55) * 175;
    return {x: Math.cos(a) * r * 1.12, y: Math.sin(a) * r * 0.94, s: 0.6 + rand() * 1.9,
            p: rand() * Math.PI * 2, links: []};
  });
  nodes.forEach((n, i) => nodes.forEach((m, j) => {
    if (j <= i) return;
    if (Math.hypot(n.x - m.x, n.y - m.y) < 58) { n.links.push(j); m.links.push(j); }
  }));
  // 上升微粒
  const dust = Array.from({length: 26}, () => ({
    x: CX + (rand() - 0.5) * 380, y: CY + (rand() - 0.5) * 300,
    v: 0.15 + rand() * 0.4, s: 0.8 + rand() * 1.6, a: 0.12 + rand() * 0.3}));
  let t = 0, alive = true;
  function frame() {
    if (!alive) return;
    t += 0.016;
    ctx.clearRect(0, 0, W, H);
    // 底部台座光晕 (双椭圆)
    for (const [ry, alpha] of [[26, 0.30], [15, 0.5]]) {
      const g = ctx.createRadialGradient(CX, CY + 168, 2, CX, CY + 168, 190);
      g.addColorStop(0, `rgba(59,180,246,${alpha * 0.55})`);
      g.addColorStop(1, 'rgba(59,130,246,0)');
      ctx.save();
      ctx.translate(CX, CY + 168);
      ctx.scale(1, ry / 60);
      ctx.translate(-CX, -(CY + 168));
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, W, H);
      ctx.restore();
    }
    // 中心亮核
    const pulse = 0.5 + 0.5 * Math.sin(t * 1.1);
    const core = ctx.createRadialGradient(CX, CY, 6, CX, CY, 185 + 12 * pulse);
    core.addColorStop(0, `rgba(140,240,255,${0.42 + 0.10 * pulse})`);
    core.addColorStop(0.28, 'rgba(59,160,246,0.20)');
    core.addColorStop(0.65, 'rgba(59,130,246,0.07)');
    core.addColorStop(1, 'rgba(59,130,246,0)');
    ctx.fillStyle = core;
    ctx.fillRect(0, 0, W, H);
    // 轨道环 (倾斜 + 亮珠)
    for (const [rx, ry, sp, ph] of [[228, 74, 0.7, 0], [252, 108, -0.45, 2.1], [206, 148, 0.3, 4.4]]) {
      ctx.save();
      ctx.translate(CX, CY);
      ctx.rotate(Math.sin(t * sp * 0.3 + ph) * 0.10);
      ctx.beginPath();
      ctx.ellipse(0, 0, rx, ry, 0, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(56,160,248,${0.20 + 0.07 * Math.sin(t + ph)})`;
      ctx.lineWidth = 1.3;
      ctx.stroke();
      for (const off of [0, 2.6]) {                 // 每环两颗亮珠
        const ang = t * sp + ph + off;
        ctx.beginPath();
        ctx.arc(Math.cos(ang) * rx, Math.sin(ang) * ry, 3, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(140,240,255,.95)';
        ctx.shadowColor = '#67e8f9';
        ctx.shadowBlur = 14;
        ctx.fill();
        ctx.shadowBlur = 0;
      }
      ctx.restore();
    }
    // 连线
    ctx.lineWidth = 0.8;
    for (const n of nodes) for (const j of n.links) {
      const m = nodes[j];
      const p = 0.5 + 0.5 * Math.sin(t * 1.6 + (n.x + m.y) * 0.02);
      ctx.strokeStyle = `rgba(80,190,250,${0.12 + 0.22 * p})`;
      ctx.beginPath();
      ctx.moveTo(CX + n.x, CY + n.y);
      ctx.lineTo(CX + m.x, CY + m.y);
      ctx.stroke();
    }
    // 节点
    for (const n of nodes) {
      const p = 0.6 + 0.4 * Math.sin(t * 2 + n.p);
      ctx.beginPath();
      ctx.arc(CX + n.x, CY + n.y, n.s * (1 + 0.35 * p), 0, Math.PI * 2);
      ctx.fillStyle = `rgba(150,236,255,${0.40 + 0.55 * p})`;
      ctx.shadowColor = '#67e8f9';
      ctx.shadowBlur = 8 * p;
      ctx.fill();
      ctx.shadowBlur = 0;
    }
    // 上升微粒
    for (const d of dust) {
      d.y -= d.v;
      if (d.y < CY - 200) { d.y = CY + 190; d.x = CX + (Math.random() - 0.5) * 380; }
      ctx.beginPath();
      ctx.arc(d.x, d.y, d.s, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(120,220,250,${d.a})`;
      ctx.fill();
    }
    requestAnimationFrame(frame);
  }
  frame();
  // 图片存在则切换 (用户可放 assets/brain.png 替换生成式光效); HEAD 探测避免 404 噪音
  fetch('/static/assets/brain.png', {method: 'HEAD'})
    .then(r => {
      if (!r.ok) return;
      alive = false;
      cv.classList.add('hidden');
      const im = $('brainImg');
      im.src = '/static/assets/brain.png';
      im.classList.remove('hidden');
    })
    .catch(() => {});
}

/* ---------- 趋势折线图 (单线: 每日复习次数) ---------- */
function drawTrend(data, days) {
  const svg = $('trendSvg');
  const W = 560, H = 190, PL = 34, PR = 10, PT = 16, PB = 26;
  const iw = W - PL - PR, ih = H - PT - PB;
  const max = Math.max(3, ...data.map(d => d.count));
  const x = i => PL + (iw * i) / Math.max(1, data.length - 1);
  const y = v => PT + ih - (ih * v) / max;
  const pts = data.map((d, i) => `${x(i)},${y(d.count).toFixed(1)}`).join(' ');
  const line = `<polyline points="${pts}" fill="none" stroke="#22d3ee" stroke-width="2"
      stroke-linejoin="round" style="filter:drop-shadow(0 0 5px rgba(34,211,238,.6))"/>` +
    data.map((d, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(d.count).toFixed(1)}"
        r="2.4" fill="#22d3ee"/>`).join('');
  const area = (() => {
    const p = data.map((d, i) => `${x(i).toFixed(1)},${y(d.count).toFixed(1)}`).join(' ');
    const base = `${x(data.length - 1).toFixed(1)},${PT + ih} ${PL},${PT + ih}`;
    return `<polygon points="${p} ${base}" fill="url(#tGrad)" opacity=".16"/>`;
  })();
  // x 轴标签: 均匀取 6 个左右
  const step = Math.max(1, Math.round(data.length / 6));
  const labels = data.map((d, i) =>
    (i % step === 0 || i === data.length - 1)
      ? `<text x="${x(i).toFixed(1)}" y="${H - 8}" fill="#61789a" font-size="10"
           text-anchor="middle">${d.date.slice(5)}</text>` : '').join('');
  const grid = [0.25, 0.5, 0.75, 1].map(f => {
    const gy = (PT + ih * (1 - f)).toFixed(1);
    return `<line x1="${PL}" x2="${W - PR}" y1="${gy}" y2="${gy}"
      stroke="rgba(56,150,248,.10)" stroke-width="1"/>
      <text x="${PL - 6}" y="${+gy + 3}" fill="#61789a" font-size="9"
        text-anchor="end">${Math.round(max * f)}</text>`;
  }).join('');
  svg.innerHTML = `<defs>
      <linearGradient id="tGrad" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#3b82f6"/>
        <stop offset="100%" stop-color="#3b82f6" stop-opacity="0"/>
      </linearGradient></defs>
    ${grid}${area}${line}${labels}`;
}

/* ---------- 数据加载 ---------- */
let TREND = {7: [], 30: []};
let trendDays = 7;                 // 当前选中窗口: 30s 轮询重绘时保持用户选择

async function loadStats() {
  let s;
  try {
    const r = await fetch('/api/stats');
    s = await r.json();
  } catch (e) { return; }
  const st = s.stats;
  $('streakBadge').textContent = `🔥 连续学习 ${st.streak} 天`;
  $('hsToday').textContent = st.today.reviews;
  $('hsTotal').textContent = st.total;
  $('ringPct').textContent = st.progress + '%';
  const C = 2 * Math.PI * 50;
  $('ringFg').style.strokeDashoffset = C * (1 - st.progress / 100);
  $('stMastered').textContent = st.mastered;
  $('stFresh').textContent = st.fresh;
  $('stDue').textContent = st.due;
  TREND = {7: s.trend7, 30: s.trend30};
  drawTrend(TREND[trendDays] || TREND[7]);
  // 列表
  const learn = $('learnList'), review = $('reviewList');
  if (!s.pending.learn.length) {
    learn.innerHTML = '<div class="l-empty">暂无新知识点</div>';
  } else {
    learn.innerHTML = s.pending.learn.map(a => `
      <div class="l-item"><div class="nm">📖 ${esc(a.name)}</div>
        <span class="badge">${a.count} 新</span>
        <button class="btn-go" onclick="location.href='/review'">学习</button></div>`).join('');
  }
  if (!s.pending.review.length) {
    review.innerHTML = '<div class="l-empty">暂无到期复习</div>';
  } else {
    review.innerHTML = s.pending.review.map(a => `
      <div class="l-item"><div class="nm">🔄 ${esc(a.name)}</div>
        <span class="badge p">${a.count} 题</span>
        <button class="btn-go" onclick="location.href='/review'">复习</button></div>`).join('');
  }
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, c =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
}

/* ---------- init ---------- */
$('trendTabs').addEventListener('click', e => {
  const b = e.target.closest('.tab');
  if (!b) return;
  document.querySelectorAll('#trendTabs .tab').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  trendDays = +b.dataset.days;
  drawTrend(TREND[trendDays] || []);
});
document.querySelectorAll('[data-todo]').forEach(el =>
  el.addEventListener('click', () => toast('该模块建设中, 敬请期待')));

brainCanvas();
loadStats();
setInterval(loadStats, 30000);
