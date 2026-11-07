/* 智学引擎 · 研读台主脚本 (study.html): 文章加载渲染 + 划词翻译/笔记 + 播放器
   (单词/划句/全文音频流式, 词边界对齐高亮当前段落) + 音色与语速选择 */
"use strict";
/* ================= 工具 ================= */
const $ = s => document.querySelector(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const cleanTok = t => t.replace(/^[^\p{L}\p{N}']+|[^\p{L}\p{N}']+$/gu, '');
let toastTimer = null;
function toast(msg, err){
  const t = $('#toast');
  t.textContent = msg; t.className = 'show' + (err ? ' err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.className = '', 2600);
}
async function api(path, body){
  const r = await fetch(path, body ? {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)} : undefined);
  if(!r.ok){ const e = await r.json().catch(() => ({})); throw new Error(e.detail || ('HTTP ' + r.status)); }
  return r.json();
}

/* ================= 全局状态 ================= */
const FALLBACK_VOICES = ['en-US-EmmaNeural', 'en-US-AriaNeural', 'en-US-GuyNeural',
  'en-US-ChristopherNeural', 'en-GB-SoniaNeural', 'en-AU-NatashaNeural'];
/* localStorage 在"阻止 Cookie"等隐私配置下会抛 SecurityError, 统一走安全封装 */
const store = {
  get(k, d){ try{ return localStorage.getItem(k) ?? d; }catch(e){ return d; } },
  set(k, v){ try{ localStorage.setItem(k, v); }catch(e){} },
};
const state = {
  articles: [], articleId: null, article: null,
  voice: store.get('ep_voice', 'en-US-EmmaNeural'),
  speed: parseFloat(store.get('ep_speed', '1')) || 1,
  trans: store.get('ep_trans', '1') === '1',      // 划词划句翻译开关
  notes: [],
};
const player = $('#player');
let playSession = 0;          // 播放会话号: 新播放使旧播放流程作废
let playAllActive = false;    // 播放全文模式
let playFinish = null;        // 当前播放的结束回调(自然结束/手动停止/出错都触发)

/* ================= 音频播放核心 ================= */
function stopAudio(){
  if(playFinish){ const f = playFinish; f(); }     // 让在途的 playUrl promise 返回
  player.onended = null; player.onerror = null;
  player.pause();
}

/* 播放一个音频 url; promise 在播完/被停止/出错时 resolve */
function playUrl(url){
  const sess = ++playSession;
  return new Promise(resolve => {
    stopAudio();
    playFinish = () => { playFinish = null; markPara(null); if(playSession === sess) resolve(); };
    player.onended = () => { if(playFinish){ const f = playFinish; f(); } };
    player.onerror = () => { toast('音频加载失败, 请重试', true); if(playFinish){ const f = playFinish; f(); } };
    player.src = url;
    player.playbackRate = state.speed;
    player.play().catch(() => {});
  });
}
player.addEventListener('pause', () => {
  if(!playFinish) return;
  if(playAllActive){                    // 全文播放: 暂停保持会话, 可恢复
    playPaused = true;
    updatePlayAllBtn();
    return;
  }
  const f = playFinish; f();            // 其它播放(划词朗读等): 原样结算
});
player.addEventListener('play', () => { // 媒体键/系统控件恢复播放时同步回播放态
  if(playAllActive && playPaused){
    playPaused = false;
    updatePlayAllBtn();
  }
});

/* 播放全文时高亮当前段落 */
function markPara(p){
  document.querySelectorAll('.para.cur').forEach(el => el.classList.remove('cur'));
  if(p == null) return;
  const el = document.querySelector(`.para[data-idx="${p}"]`);
  if(el) el.classList.add('cur');
}

/* 划词/划句朗读: 服务端先查 resource/audio/words/ 缓存, 命中则直接返回, 不经过 edge-tts */
async function playSnippet(text){
  try{
    const r = await api('/api/say', {text, voice: state.voice});
    await playUrl(r.url);
  }catch(e){ toast('播放失败: ' + e.message, true); }
}

/* 播放全文: 整篇一次流式播放。服务端边合成边回传并落盘,
   二次播放直接读缓存文件; 依据各段起始秒高亮当前段落。
   三态控制: 播放 ⇄ 暂停(保持会话, 可恢复) / 停止(终止会话)。 */
let fullStarts = [];         // 全文音频内各段起始秒, 未对齐的段为 null
let fullStartsDone = false;
let pollSeq = 0;             // 轮询会话号: 新的全文播放使旧轮询作废
let playPaused = false;      // 暂停态: 会话仍存活, playFinish 未结算

/* 播放全文按钮: SVG 图标(播放/暂停), 尺寸大于旧文本字形 */
const ICON_PLAY = '<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>';
const ICON_PAUSE = '<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>';
const ICON_STOP = '<svg viewBox="0 0 24 24" width="15" height="15" fill="currentColor" aria-hidden="true"><path d="M6 6h12v12H6z"/></svg>';
const ICON_MARK = '<svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor" aria-hidden="true"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25zM20.71 7.04c.39-.39.39-1.02 0-1.41l-2.34-2.34a.9959.9959 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83z"/></svg>';

function updatePlayAllBtn(){
  const b = $('#playAllBtn');
  if(!b) return;
  b.innerHTML = (playAllActive && !playPaused) ? ICON_PAUSE : ICON_PLAY;
  b.classList.toggle('active', playAllActive);
  b.title = !playAllActive ? '播放全文' : (playPaused ? '继续播放' : '暂停');
  const s = $('#playStopBtn');
  if(s){ s.innerHTML = ICON_STOP; s.style.display = playAllActive ? 'inline-flex' : 'none'; }
  updateProgressBar();          // 停止时顺带隐藏进度条
}
updatePlayAllBtn();

/* 播放进度条: 时长已知用精确进度; 流式合成中 duration 常为 Infinity,
   用 各段起始秒 vs 段前累计词数 的线性拟合(语速近似恒定)估算总时长,
   保证进度实时平滑; 合成完成后 duration 变为有限值, 自动切回精确进度。 */
let progDragging = false;

function estDuration(){
  if(!state.article) return null;
  const words = state.article.paras.map(p => p.tokens.length);
  const totalW = words.reduce((a, b) => a + b, 0);
  if(!totalW) return null;
  const pts = [];
  for(let i = 0; i < fullStarts.length; i++)
    if(fullStarts[i] != null){
      const before = words.slice(0, i).reduce((a, b) => a + b, 0);
      pts.push([before, fullStarts[i]]);
    }
  let est;
  if(pts.length < 2) est = totalW / 2.6;         // 仅1个起始点: 用默认语速(~156词/分)先验
  else{
    const n = pts.length;
    const sx = pts.reduce((a, p) => a + p[0], 0), sy = pts.reduce((a, p) => a + p[1], 0);
    const sxx = pts.reduce((a, p) => a + p[0] * p[0], 0), sxy = pts.reduce((a, p) => a + p[0] * p[1], 0);
    const den = n * sxx - sx * sx;
    if(den <= 0) return null;
    const rate = (n * sxy - sx * sy) / den;      // 秒/词
    if(rate <= 0) return null;
    est = totalW / rate;
  }
  // 钳到合理语速区间, 防标题段等异常点把估算带飞 (bar 突跳到头/爬行)
  return Math.min(Math.max(est, totalW / 5), totalW / 1.5);
}

function updateProgressBar(){
  const bar = $('#playProg');
  if(!bar) return;
  if(!playAllActive){ bar.style.display = 'none'; return; }
  bar.style.display = 'block';
  if(progDragging) return;                        // 拖拽中视觉由指针事件驱动
  // 流式合成期间 Chrome 会按已收字节估一个≈已缓冲时长的假 duration, 不可信;
  // 合成完成(fullStartsDone)后才采信真实 duration。
  const streaming = !fullStartsDone;
  const d = player.duration;
  let dur = (!streaming && isFinite(d) && d > 0) ? d : estDuration();
  let frac = null;
  if(dur) frac = Math.min(1, player.currentTime / dur);
  else if(fullStarts.length){
    let cur = 0;
    for(let i = 0; i < fullStarts.length; i++)
      if(fullStarts[i] != null && fullStarts[i] <= player.currentTime) cur = i;
    frac = cur / fullStarts.length;
  }
  bar.firstElementChild.style.width = ((frac || 0) * 100).toFixed(2) + '%';
}

/* 点击/拖拽进度条快进: 按下与松开时 seek, 拖动中只刷视觉避免音频卡顿。
   流式期间 duration 是按字节估的假值, 换算一律走 estDuration (同进度条口径);
   Chrome 对该原始流的 seekable 为 [0,∞), 远跳由解码器吞流追赶。 */
function seekProg(frac){
  frac = Math.max(0, Math.min(1, frac));
  const p = player;
  const streaming = !fullStartsDone;
  if(!streaming && isFinite(p.duration) && p.duration > 0){
    p.currentTime = frac * p.duration;
    return;
  }
  const est = estDuration();
  if(est) p.currentTime = frac * est;
}
(function setupProgSeek(){
  const bar = $('#playProg');
  bar.addEventListener('pointerdown', ev => {
    if(!playAllActive) return;
    ev.preventDefault();
    progDragging = true;
    bar.classList.add('drag');
    try{ bar.setPointerCapture(ev.pointerId); }catch(e){}
    const fracOf = e => {
      const r = bar.getBoundingClientRect();
      return Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    };
    seekProg(fracOf(ev));
    const move = e => {
      bar.firstElementChild.style.width = (fracOf(e) * 100).toFixed(2) + '%';
    };
    const up = e => {
      progDragging = false;
      bar.classList.remove('drag');
      seekProg(fracOf(e));
      bar.removeEventListener('pointermove', move);
      bar.removeEventListener('pointerup', up);
      bar.removeEventListener('pointercancel', up);
    };
    bar.addEventListener('pointermove', move);
    bar.addEventListener('pointerup', up);
    bar.addEventListener('pointercancel', up);
  });
})();

function updateFullHighlight(){
  if(!playAllActive || !fullStarts.length) return;
  let cur = -1;
  for(let i = 0; i < fullStarts.length; i++)
    if(fullStarts[i] != null && fullStarts[i] <= player.currentTime) cur = i;
  if(cur >= 0) markPara(cur);
}
player.addEventListener('timeupdate', () => { updateFullHighlight(); updateProgressBar(); });

async function pollFullStarts(aid, voice, seq){
  while(playAllActive && !fullStartsDone && state.articleId === aid && seq === pollSeq){
    try{
      const m = await api(`/api/article_audio_starts?article_id=${encodeURIComponent(aid)}`
        + `&voice=${encodeURIComponent(voice)}`);
      if(state.articleId !== aid || seq !== pollSeq) return;
      fullStarts = m.starts || [];
      fullStartsDone = !!m.complete;
    }catch(e){ /* 服务暂不可达, 下一轮重试 */ }
    await new Promise(r => setTimeout(r, 800));
  }
}
async function playAll(){
  if(playAllActive){
    if(playPaused){                          // 暂停中 → 继续
      playPaused = false;
      player.playbackRate = state.speed;
      player.play().catch(() => {});
    }else{                                   // 播放中 → 暂停 (pause 事件里置态, 保持会话)
      playPaused = true;
      player.pause();
    }
    updatePlayAllBtn();
    return;
  }
  if(!state.article || !state.article.paras.length) return;
  const artId = state.articleId, voice = state.voice, seq = ++pollSeq;
  playAllActive = true; playPaused = false; updatePlayAllBtn();
  fullStarts = []; fullStartsDone = false;
  pollFullStarts(artId, voice, seq);
  const q = `article_id=${encodeURIComponent(artId)}&voice=${encodeURIComponent(voice)}`;
  try{
    await playUrl(`/api/article_audio?${q}`);
  }finally{
    playAllActive = false; playPaused = false; updatePlayAllBtn(); pollSeq++;
  }
}
$('#playAllBtn').onclick = playAll;

/* 停止: 终止会话 (stopAudio 结算 playFinish → playAll finally 复位) */
$('#playStopBtn').onclick = () => {
  if(!playAllActive) return;
  playPaused = false;
  stopAudio();
  updatePlayAllBtn();
};

/* ================= 顶栏音色 / 语速 ================= */
function renderVoiceOptions(sel, voices){
  if(!voices.includes(state.voice)) voices = [state.voice, ...voices];
  sel.innerHTML = voices.map(v =>
    `<option value="${esc(v)}"${v === state.voice ? ' selected' : ''}>${esc(v.replace('Neural', ''))}</option>`).join('');
  sel.value = state.voice;
  sel.onchange = () => {
    state.voice = sel.value;
    store.set('ep_voice', state.voice);
    playAllActive = false; updatePlayAllBtn(); stopAudio();
    if(state.articleId) loadArticle(state.articleId);
  };
}
async function initVoices(){
  const sel = $('#voiceSel');
  renderVoiceOptions(sel, FALLBACK_VOICES);          // 先渲染内置列表, 不阻塞页面
  try{
    const v = await (await fetch('/api/voices')).json();
    if(Array.isArray(v) && v.length) renderVoiceOptions(sel, v);
  }catch(e){ /* 保留内置列表 */ }
}
$('#speedSel').onchange = () => {
  state.speed = parseFloat($('#speedSel').value) || 1;
  store.set('ep_speed', state.speed);
  player.playbackRate = state.speed;
};

/* 划译开关: 关闭后点击/划选不再弹出翻译 (亮/暗两态) */
function renderTransBtn(){
  const b = $('#transBtn');
  b.innerHTML = ICON_MARK + '<span>划译</span>';
  b.classList.toggle('off', !state.trans);
}
$('#transBtn').onclick = () => {
  state.trans = !state.trans;
  store.set('ep_trans', state.trans ? '1' : '0');
  renderTransBtn();
  if(!state.trans) hidePopup();
};
renderTransBtn();

/* ================= 文章 ================= */
async function loadArticles(){
  const box = $('#articleList');
  try{
    state.articles = await (await fetch('/api/articles')).json();
  }catch(e){
    box.innerHTML = '<div class="h-empty">文章列表加载失败<br><button class="btn" id="artRetry" style="margin-top:8px">重试</button></div>';
    const btn = box.querySelector('#artRetry');
    if(btn) btn.onclick = loadArticles;
    return;
  }
  $('#artCount').textContent = state.articles.length;
  box.innerHTML = state.articles.map(a => `
    <div class="art-item${a.id === state.articleId ? ' active' : ''}" data-id="${esc(a.id)}">
      <div class="t" title="${esc(a.title)}">${esc(a.id)}</div>
    </div>`).join('');
  box.querySelectorAll('.art-item').forEach(el =>
    el.onclick = () => loadArticle(el.dataset.id));
}

async function loadArticle(id){
  playSession++;                       // 使在途播放作废
  playAllActive = false; updatePlayAllBtn();
  stopAudio(); hidePopup();
  try{
    const r = await fetch('/api/article/' + encodeURIComponent(id));
    if(!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    if(!data.paras) throw new Error('数据异常');
    state.articleId = id; state.article = data;
    renderArticle(); loadArticles();
    store.set('ep_last', id);
    return true;
  }catch(e){ toast('文章加载失败: ' + e.message, true); return false; }
}

function renderArticle(){
  const art = state.article;
  const view = $('#articleView');
  view.innerHTML = art.paras.map(p => {
    const tag = p.is_title ? 'h1' : 'p';
    const toks = p.tokens.map((t, i) =>
      `<span class="tok" data-i="${i}" data-p="${p.idx}">${esc(t)}</span>`).join(' ');
    return `<${tag} class="para${p.is_title ? ' title' : ''}" data-idx="${p.idx}">${toks}</${tag}>`;
  }).join('');
}

/* ================= 划词 / 划句 弹窗 ================= */
const popup = $('#popup');
let popupCtx = null;

function selectionInfo(){
  const sel = window.getSelection();
  if(!sel || sel.isCollapsed) return null;
  const text = sel.toString().replace(/\s+/g, ' ').trim();
  if(!text) return null;
  const nodeOf = n => (n.nodeType === 1 ? n : n.parentElement);
  const a = nodeOf(sel.anchorNode), f = nodeOf(sel.focusNode);
  const at = a && a.closest('.tok'), ft = f && f.closest('.tok');
  if(!at || !ft) return null;
  const pa = at.closest('.para'), pf = ft.closest('.para');
  if(!pa || !pf || pa.dataset.idx !== pf.dataset.idx){ toast('请选择同一段落内的文本'); return null; }
  const i0 = +at.dataset.i, i1 = +ft.dataset.i;
  return {para: +pa.dataset.idx, t0: Math.min(i0, i1), t1: Math.max(i0, i1), text};
}

function showPopup(mode, info, x, y){
  popupCtx = {mode, ...info, trans: '', phon: ''};
  popup.innerHTML = `
    <div class="pp-head"><span class="pp-text">${esc(info.text)}${mode === 'word'
      ? '<span class="pp-phon" id="ppPhon"></span>' : ''}</span>
      <button class="pp-close" title="关闭">✕</button></div>
    <div class="pp-trans" id="ppTrans"><span style="color:var(--dim)">⏳ 查询中…</span></div>
    <div class="pp-actions">
      <button class="btn" id="ppPlay">▶ 播放</button>
      <button class="btn primary" id="ppAdd" title="加入笔记(原文+翻译)">＋ 笔记</button>
      <button class="btn" id="ppRemark" title="写一条备注加入笔记">✎ 备注</button>
    </div>
    <div class="pp-note hidden" id="ppNoteBox">
      <textarea id="ppNoteText" placeholder="补充这个${mode === 'word' ? '词' : '句'}的备注…"></textarea>
      <button class="btn primary" id="ppSave">保存</button>
    </div>`;
  popup.classList.add('show');
  const place = () => {
    const r = popup.getBoundingClientRect();
    popup.style.left = Math.max(12, Math.min(x, innerWidth - r.width - 12)) + 'px';
    popup.style.top = Math.max(12, Math.min(y + 14, innerHeight - r.height - 12)) + 'px';
  };
  place();
  popupCtx.reposition = place;
  popup.querySelector('.pp-close').onclick = hidePopup;
  $('#ppPlay').onclick = () => {
    if(!popupCtx) return;
    const {text} = popupCtx;
    playAllActive = false; updatePlayAllBtn(); stopAudio();
    playSnippet(text);            // 弹窗保持常驻, 便于接着点笔记/备注
  };
  $('#ppAdd').onclick = addNote;
  $('#ppRemark').onclick = () => {
    $('#ppNoteBox').classList.toggle('hidden');
    $('#ppNoteText').focus();
    if(popupCtx && popupCtx.reposition) popupCtx.reposition();   // 展开后重新定位, 防止底部越界
  };
  $('#ppSave').onclick = saveNote;
  if(mode === 'word') dictFor(info);      // 单词: 词典(音标+多词义)
  else translateFor(info);                // 句子: 翻译
}

/* 单词词典查询: 音标 + 多条词义; 词义列表替代单条翻译 */
async function dictFor(info){
  try{
    const d = await api('/api/dict?text=' + encodeURIComponent(info.text));
    if(!popupCtx || popupCtx.text !== info.text) return;
    popupCtx.phon = d.phonetic || '';
    popupCtx.senses = d.senses || [];
    popupCtx.trans = popupCtx.senses.slice(0, 2).join(' / ');   // 入笔记的释义串
    const el = $('#ppPhon');
    if(el) el.textContent = popupCtx.phon;
    const box = $('#ppTrans');
    if(box) box.innerHTML = popupCtx.senses.length
      ? popupCtx.senses.map(s => `<div class="pp-sense">${esc(s)}</div>`).join('')
      : '<span style="color:var(--dim)">未查到词义</span>';
  }catch(e){
    if(!popupCtx || popupCtx.text !== info.text) return;
    const box = $('#ppTrans');
    if(box) box.innerHTML = `<span style="color:var(--err)">查询失败: ${esc(e.message)}</span>`;
  }
}

/* 划句翻译会话缓存: 键=text, 离开页面即释放 (设计: 服务端不缓存, 划词走 words_dict) */
const transCache = new Map();

async function translateFor(info){
  const render = trans => {
    if(!popupCtx || popupCtx.text !== info.text) return;   // 弹窗已切换/关闭
    popupCtx.trans = trans;
    const box = $('#ppTrans');
    if(box) box.textContent = trans;
  };
  if(transCache.has(info.text)){ render(transCache.get(info.text)); return; }
  try{
    const d = await api('/api/translate', {text: info.text});
    transCache.set(info.text, d.translation);
    render(d.translation);
  }catch(e){
    if(!popupCtx || popupCtx.text !== info.text) return;   // 弹窗已切换/关闭
    const box = $('#ppTrans');
    if(box) box.innerHTML = `<span style="color:var(--err)">翻译失败: ${esc(e.message)}</span>`;
  }
}

/* 组装加入笔记的请求体 (定位不落库: 前端按文本搜索) */
function notePayload(note){
  return {article_id: state.articleId, text: popupCtx.text, note,
    translation: popupCtx.trans || '', phonetic: popupCtx.phon || ''};
}

/* ＋ 笔记: 一键把原文+当前翻译收进笔记 (翻译未返回时先存原文, 之后可再＋补全) */
async function addNote(){
  const ctx = popupCtx;
  if(!ctx) return;
  try{
    await api('/api/notes', notePayload(''));
    if(popupCtx !== ctx) return;          // 弹窗已切换: 不动新弹窗的按钮
    const b = $('#ppAdd');
    if(b){ b.textContent = '✓ 已在笔记'; b.disabled = true; }
    toast('已加入笔记 ✓');
    loadNotes();
  }catch(e){ toast('加入笔记失败: ' + e.message, true); }
}

/* ✎ 备注: 带自定义备注加入/更新笔记 */
async function saveNote(){
  const note = $('#ppNoteText').value.trim();
  const ctx = popupCtx;
  if(!note){ toast('备注内容为空', true); return; }
  try{
    await api('/api/notes', notePayload(note));
    if(popupCtx !== ctx) return;
    toast('已加入笔记 ✓');
    $('#ppNoteBox').classList.add('hidden');
    $('#ppNoteText').value = '';
    loadNotes();
  }catch(e){ toast('保存失败: ' + e.message, true); }
}

function hidePopup(){ popup.classList.remove('show'); popupCtx = null; }

/* ================= 笔记 ================= */
async function loadNotes(){
  try{
    state.notes = await (await fetch('/api/notes')).json();
    renderNotes();
  }catch(e){ /* 忽略 */ }
}

function renderNotes(){
  const box = $('#noteList');
  const ns = state.notes;
  const cnt = $('#noteCount');
  if(cnt) cnt.textContent = ns.length;
  if(!ns.length){ box.innerHTML = '<div class="h-empty">暂无笔记</div>'; return; }
  box.innerHTML = ns.map(n => `
    <div class="h-item" data-id="${n.id}"
        data-art="${esc(n.article_id)}" data-text="${esc(n.text)}">
      <div class="h-top"><span class="h-src" title="${esc(n.text)}">${esc(n.text)}</span>
        <span class="h-time">${esc((n.created_at || '').slice(5, 16))}</span>
        <span class="h-act"><button class="play" title="定位并播放">▶</button><button class="del" title="删除">✕</button></span></div>
      ${n.phonetic ? `<div class="h-phon">${esc(n.phonetic)}</div>` : ''}
      ${n.translation ? `<div class="h-res">${esc(n.translation)}</div>` : ''}
      ${n.note ? `<div class="h-res note">${esc(n.note)}</div>` : ''}
    </div>`).join('');

  box.querySelectorAll('.h-item').forEach(el => {
    const info = {article_id: el.dataset.art, text: el.dataset.text, id: +el.dataset.id};
    el.onclick = e => {
      if(e.target.closest('button')) return;
      jumpTo(info, false);
    };
    el.querySelector('.play').onclick = () => {
      playAllActive = false;
      stopAudio();                        // 先真停全文音频, 防幽灵播放/失败路径失控
      updatePlayAllBtn();
      jumpTo(info, true);
    };
    el.querySelector('.del').onclick = async () => {
      try{
        const r = await fetch('/api/notes/' + info.id, {method: 'DELETE'});
        if(!r.ok) throw new Error('HTTP ' + r.status);
      }catch(e){ toast('删除失败: ' + e.message, true); }
      loadNotes();
    };
  });
}

/* 笔记定位: 在当前文章 tokens 里搜索笔记原文 (位置不落库, 前端文本搜索)。
   双侧归一到"仅字母/数字/'的词序列", 标点差异不影响匹配; 返回 {para, t0, t1} 或 null */
function locateText(text){
  const simp = s => (String(s).toLowerCase().match(/[\p{L}\p{N}']+/gu) || []).join(' ');
  const target = simp(text);
  if(!target) return null;
  for(const p of (state.article ? state.article.paras : [])){
    const joined = p.tokens.map(t => simp(cleanTok(t))).join(' ');
    const pos = joined.indexOf(target);
    if(pos < 0) continue;
    const t0 = joined.slice(0, pos).split(' ').length - 1;
    return {para: p.idx, t0, t1: t0 + target.split(' ').length - 1};
  }
  return null;
}

async function jumpTo(info, autoplay){
  if(info.article_id !== state.articleId){
    const ok = await loadArticle(info.article_id);
    if(!ok || state.articleId !== info.article_id) return;   // 加载失败或期间已切走
  }
  if(!state.article) return;
  const loc = locateText(info.text);
  if(!loc){
    $('#articleView').scrollTop = 0;
    if(autoplay) playSnippet(info.text);
    return;
  }
  const para = document.querySelector(`p.para[data-idx="${loc.para}"], h1.title[data-idx="${loc.para}"]`);
  if(para) para.scrollIntoView({block: 'center', behavior: 'smooth'});
  document.querySelectorAll('.tok.flash').forEach(el => el.classList.remove('flash'));
  for(let i = loc.t0; i <= Math.min(loc.t1, loc.t0 + 40); i++){
    const el = document.querySelector(`.tok[data-p="${loc.para}"][data-i="${i}"]`);
    if(el) el.classList.add('flash');
  }
  setTimeout(() => document.querySelectorAll('.tok.flash').forEach(el => el.classList.remove('flash')), 2600);
  if(autoplay) playSnippet(info.text);
}

/* ================= 交互事件 ================= */
const view = $('#articleView');

view.addEventListener('click', e => {
  const tok = e.target.closest('.tok');
  if(!tok || !state.article || !state.trans) return;
  if(!window.getSelection().isCollapsed) return;   // 拖选已在 mouseup 处理
  const word = cleanTok(tok.textContent);
  if(!word) return;
  showPopup('word', {text: word, para: +tok.dataset.p, t0: +tok.dataset.i, t1: +tok.dataset.i},
    e.clientX, e.clientY);
});

view.addEventListener('mouseup', e => {
  if(!state.article || !state.trans) return;
  const info = selectionInfo();
  if(!info) return;
  const sel = window.getSelection();
  const rect = sel.rangeCount ? sel.getRangeAt(0).getBoundingClientRect() : {left: e.clientX, top: e.clientY, bottom: e.clientY};
  // 双击/拖选单个 token 也按"词"处理; 单词需清洗标点, 与单击行为真正一致
  if(info.t0 === info.t1){
    const w = cleanTok(info.text);
    if(w) info.text = w;
  }
  showPopup(info.t0 === info.t1 ? 'word' : 'sent', info, rect.left, rect.bottom);
});

document.addEventListener('mousedown', e => {
  if(popup.classList.contains('show') && !popup.contains(e.target) && !e.target.closest('.tok'))
    hidePopup();
});
document.addEventListener('keydown', e => { if(e.key === 'Escape') hidePopup(); });
player.addEventListener('ended', () => {
  if(playFinish){ const f = playFinish; f(); }
});

/* ================= 可拖拽分栏 ================= */
/* 指针捕获实现: 拖到窗口外释放也不会残留监听; 左右联动约束, 中栏始终保留最小宽度 */
const cols = {left: 12.5, right: 25, minCenter: 22};
function setLeftWidth(pct){
  pct = Math.max(8, Math.min(40, pct));
  pct = Math.min(pct, 100 - cols.right - cols.minCenter);
  cols.left = pct;
  $('#leftPane').style.width = pct + '%';
}
function setRightWidth(pct){
  pct = Math.max(12, Math.min(45, pct));
  pct = Math.min(pct, 100 - cols.left - cols.minCenter);
  cols.right = pct;
  $('#rightPane').style.width = pct + '%';
}
function saveCols(){ store.set('ep_cols', JSON.stringify([cols.left, cols.right])); }
function setupSplitter(id, apply){
  const el = $(id);
  el.addEventListener('pointerdown', ev => {
    ev.preventDefault();
    try{ el.setPointerCapture(ev.pointerId); }catch(e){}
    const move = e => apply(e);
    const up = () => {
      el.removeEventListener('pointermove', move);
      el.removeEventListener('pointerup', up);
      el.removeEventListener('pointercancel', up);
      saveCols();
    };
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', up);
  });
}
setupSplitter('#splitL', e => {
  setLeftWidth((e.clientX - $('#main').getBoundingClientRect().left) / $('#main').clientWidth * 100);
});
setupSplitter('#splitR', e => {
  const rect = $('#main').getBoundingClientRect();
  setRightWidth((rect.right - e.clientX) / rect.width * 100);
});
(function restoreCols(){
  try{
    const [l, r] = JSON.parse(store.get('ep_cols', '[12.5,25]'));
    setRightWidth(Math.max(12, Math.min(45, r)));
    setLeftWidth(Math.max(8, Math.min(40, l)));
  }catch(e){}
})();

/* ================= 启动 ================= */
(async function init(){
  try{
    player.playbackRate = state.speed;
    $('#speedSel').value = String(state.speed);
    await loadArticles();
    initVoices();
    loadNotes();
    const last = store.get('ep_last', null);
    const first = state.articles.find(a => a.id === last) || state.articles[0];
    if(first) loadArticle(first.id);
  }catch(e){ toast('初始化失败: ' + e.message, true); }
})();
