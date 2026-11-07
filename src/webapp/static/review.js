/* 复习考试: 准备(claude 出题) → 就绪 → 单题录音作答+现场评分 → 结果(AI 统一阅卷) */
'use strict';

const $ = id => document.getElementById(id);
const VOICE = 'en-US-EmmaNeural';
const REC_MAX_MS = 60000;
const RATE_META = [
  {r: 1, name: 'Again', zh: '忘记'}, {r: 2, name: 'Hard', zh: '困难'},
  {r: 3, name: 'Good', zh: '良好'}, {r: 4, name: 'Easy', zh: '轻松'},
];

const st = {
  phase: 'prepare', session: null, questions: [], idx: 0,
  recorder: null, chunks: [], levelRaf: 0, audioCtx: null, startedAt: 0,
  timerTm: 0, results: null, pollTm: 0, judging: false,
  submitting: false, rating: false, micLock: false, lastSig: '',
};

function show(sec) {
  for (const s of ['stPrepare', 'stReady', 'stExam', 'stResult'])
    $(s).classList.add('hidden');
  $(sec).classList.remove('hidden');
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"]/g, c =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
}

/* ================= 1. 准备: 触发 design-exam 并轮询 ================= */
async function bootPrepare() {
  show('stPrepare');
  try { await fetch('/api/review/prepare', {method: 'POST'}); } catch (e) {}
  pollPrepare(0);
}

function pollPrepare(n) {
  clearTimeout(st.pollTm);
  fetch('/api/review/prepare/status')
    .then(r => r.json())
    .then(p => {
      $('prepMsg').textContent = p.message || '…';
      if (p.phase === 'ready') return goReady();
      if (p.phase === 'error') {
        $('prepTitle').textContent = '文章分析未完成';
        $('prepActions').classList.remove('hidden');
        return;
      }
      if (n > 720) {                    // 24 分钟兜底
        $('prepTitle').textContent = '分析耗时过长';
        $('prepActions').classList.remove('hidden');
        return;
      }
      st.pollTm = setTimeout(() => pollPrepare(n + 1), 2000);
    })
    .catch(() => { st.pollTm = setTimeout(() => pollPrepare(n + 1), 2500); });
}

$('btnForceStart').addEventListener('click', goReady);

/* ================= 2. 就绪 ================= */
async function goReady() {
  show('stReady');
  clearTimeout(st.pollTm);
  let q;
  try {
    q = await (await fetch('/api/review/queue')).json();
  } catch (e) { $('dueCount').textContent = '?'; return; }
  $('dueCount').textContent = q.due;
  const c = q.params.config || {};
  $('readyMeta').textContent =
    `FSRS-6 · 期望保持率 ${Math.round(q.params.desired_retention * 100)}% · ` +
    `学习步长 ${(c.learning_steps || []).join('/')} 分钟 · 单场最多 ${q.due} 题`;
  $('fsrChip').textContent = `FSRS R=${Math.round(q.params.desired_retention * 100)}%`;
}

$('btnStart').addEventListener('click', async () => {
  $('btnStart').disabled = true;
  try {
    const s = await (await fetch('/api/review/session', {method: 'POST'})).json();
    if (s.detail) { alert(s.detail); return; }
    st.session = s.session_id;
    st.questions = s.questions;
    st.idx = 0;
    st.phase = 'exam';
    show('stExam');
    renderQuestion();
  } catch (e) {
    alert('创建考试失败: ' + e.message);
  } finally {
    $('btnStart').disabled = false;
  }
});

/* ================= 3. 考试: 单题 + 实时录音 + 现场评分 ================= */
function renderQuestion() {
  const q = st.questions[st.idx];
  const n = st.questions.length;
  $('qIdx').textContent = `${st.idx + 1} / ${n}`;
  $('qBar').style.width = (st.idx / n * 100) + '%';
  $('qText').textContent = q.question;
  $('btnQSay').disabled = false;
  $('btnSkip').classList.remove('hidden');
  $('ratePanel').classList.add('hidden');
  $('micBtn').classList.remove('rec');
  $('micBtn').disabled = false;
  $('micLabel').textContent = '点击开始作答';
  $('recTime').classList.add('hidden');
  stopMeter();
}

$('btnQSay').addEventListener('click', () => {
  const q = st.questions[st.idx];
  playUrl(`/api/say_stream?text=${encodeURIComponent(q.question)}&voice=${VOICE}`, $('btnQSay'));
});

/* --- 共享播放器: 全页同一时刻只播一个音频, 点新的自动停旧的 --- */
const player = new Audio();

function playUrl(url, btn) {
  player.pause();
  player.src = url;
  if (btn) { btn.disabled = true; player._btn = btn; }
  player.play().catch(() => { if (btn) btn.disabled = false; });
}
player.onended = player.onerror = () => {
  if (player._btn) { player._btn.disabled = false; player._btn = null; }
};

/* --- 录音 --- */
const meterCtx = $('meterCv').getContext('2d');

async function startRec() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio: true});
    st.chunks = [];
    st.recorder = new MediaRecorder(stream, {mimeType: pickMime()});
    st.recorder.ondataavailable = e => { if (e.data.size) st.chunks.push(e.data); };
    st.recorder.onstop = () => {
      stream.getTracks().forEach(t => t.stop());
      submitAnswer(new Blob(st.chunks, {type: st.recorder.mimeType || 'audio/webm'}));
    };
    st.recorder.start(250);
    st.startedAt = Date.now();
    startMeter(stream);
    $('micBtn').classList.add('rec');
    $('micLabel').textContent = '停止提交';
    $('recTime').classList.remove('hidden');
    tickTimer();
    st.timerTm = setTimeout(stopRec, REC_MAX_MS);
  } catch (e) {
    alert('无法访问麦克风: ' + e.message);
  }
}

function pickMime() {
  for (const m of ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'])
    if (MediaRecorder.isTypeSupported(m)) return m;
  return '';
}

function stopRec() {
  clearTimeout(st.timerTm);
  if (st.recorder && st.recorder.state === 'recording') st.recorder.stop();
}

function tickTimer() {
  const s = Math.floor((Date.now() - st.startedAt) / 1000);
  $('recTime').textContent =
    String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
  if (st.recorder && st.recorder.state === 'recording')
    requestAnimationFrame(tickTimer);
}

$('micBtn').addEventListener('click', () => {
  if (st.micLock || st.submitting) return;
  if (st.recorder && st.recorder.state === 'recording') {
    st.micLock = true;
    $('micBtn').disabled = true;
    stopRec();
    setTimeout(() => { st.micLock = false; $('micBtn').disabled = false; }, 700);
  } else startRec();
});

/* 音量电平表 */
function startMeter(stream) {
  try {
    st.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const src = st.audioCtx.createMediaStreamSource(stream);
    const an = st.audioCtx.createAnalyser();
    an.fftSize = 256;
    src.connect(an);
    const buf = new Uint8Array(an.frequencyBinCount || 64);
    (function draw() {
      if (!st.recorder || st.recorder.state !== 'recording') { stopMeter(); return; }
      st.levelRaf = requestAnimationFrame(draw);
      an.getByteFrequencyData(buf);
      const W = $('meterCv').width, H = $('meterCv').height, n = 40;
      meterCtx.clearRect(0, 0, W, H);
      const bw = W / n;
      for (let i = 0; i < n; i++) {
        const v = (buf.length ? buf[Math.floor(i * buf.length / n)] : 0) / 255;
        const h = Math.max(2, (isFinite(v) ? v : 0) * H * 0.92);
        const g = meterCtx.createLinearGradient(0, H - h, 0, H);
        g.addColorStop(0, v > 0.75 ? '#f87171' : '#67e8f9');
        g.addColorStop(1, 'rgba(34,211,238,.15)');
        meterCtx.fillStyle = g;
        meterCtx.fillRect(i * bw + 1, H - h, bw - 2, h);
      }
    })();
  } catch (e) {                     // 电平表属增强体验, 不允许它弄挂录音主流程
    console.warn('meter unavailable:', e.message);
  }
}

function stopMeter() {
  cancelAnimationFrame(st.levelRaf);
  meterCtx.clearRect(0, 0, $('meterCv').width, $('meterCv').height);
  if (st.audioCtx) { st.audioCtx.close(); st.audioCtx = null; }
}

/* --- 提交: 成功后出评分面板 (评分后进下一题) --- */
$('btnSubmit').addEventListener('click', stopRec);

$('btnSkip').addEventListener('click', () => submitAnswer(null));

function cancelRecording() {
  clearTimeout(st.timerTm);
  const rec = st.recorder;
  st.recorder = null;
  st.chunks = [];
  if (rec) {
    rec.onstop = rec.ondataavailable = null;
    try { if (rec.state !== 'inactive') rec.stop(); } catch (e) {}
  }
  stopMeter();
  $('recTime').classList.add('hidden');
}

async function submitAnswer(blob) {
  if (st.submitting) return;
  st.submitting = true;
  cancelRecording();
  const q = st.questions[st.idx];
  const fd = new FormData();
  fd.append('session_id', st.session);
  fd.append('card_id', q.card_id);
  if (blob && blob.size > 800) fd.append('audio', blob, 'answer.webm');
  $('micBtn').disabled = true;
  $('btnSkip').disabled = true;
  $('micLabel').textContent = blob ? '转写中…' : '已跳过';
  let ok = false;
  try {
    const r = await fetch('/api/exam/answer', {method: 'POST', body: fd});
    if (r.status === 409) { ok = true; }
    else if (!r.ok) alert('提交失败: ' + (await r.json()).detail);
    else ok = true;
  } catch (e) {
    alert('提交失败: ' + e.message);
  }
  st.submitting = false;
  if (!ok) { renderQuestion(); return; }
  if (blob) showRatePanel();        // 作答了 → 现场选回忆表现; 跳过 → 直接下一题
  else advance();
}

function showRatePanel() {
  const q = st.questions[st.idx];
  $('micLabel').textContent = '作答完成 · 请选择回忆表现';
  $('rateBtns').innerHTML = RATE_META.map(m => `
    <button class="rate-btn r${m.r}" data-rate="${m.r}">
      <b>${m.name} · ${m.zh}</b><span>${(q.preview && q.preview[m.r]) || ''}</span>
    </button>`).join('');
  $('ratePanel').classList.remove('hidden');
}

$('rateBtns').addEventListener('click', e => {
  const b = e.target.closest('[data-rate]');
  if (b && !st.rating) chooseRating(+b.dataset.rate);
});

async function chooseRating(rating) {
  st.rating = true;
  const q = st.questions[st.idx];
  try {
    const r = await fetch('/api/exam/rate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: st.session, card_id: q.card_id, rating}),
    });
    if (!r.ok) throw new Error((await r.json()).detail);
  } catch (e) {
    alert('评分失败: ' + e.message);
  }
  st.rating = false;
  advance();
}

function advance() {
  $('ratePanel').classList.add('hidden');
  $('btnSkip').disabled = false;
  st.idx += 1;
  if (st.idx >= st.questions.length) {
    $('qBar').style.width = '100%';
    st.phase = 'result';
    try {
      fetch(`/api/exam/finish?session_id=${st.session}`, {method: 'POST'})
        .catch(() => {});
    } catch (e) {}
    setTimeout(openResults, 300);     // 等 finish 响应落地再进结果页
  } else {
    renderQuestion();
  }
}

/* ================= 4. 结果: 统一阅卷进度 + 逐题详情 ================= */
async function openResults() {
  show('stResult');
  stopMeter();
  st.judging = true;
  st.lastSig = '';
  try {
    await loadResults();
  } catch (e) {
    $('resultSummary').textContent = '加载失败, 正在重试…';
  }
  pollJudge();
}

/* 数据签名: 只在 verdict/rated/fsrs 变化时重渲染列表, 轮询期间不闪烁 */
function resultSig(d) {
  return JSON.stringify(d.items.map(i =>
    [i.card_id, i.verdict ? (i.verdict.error ? 'e' : (i.verdict.correct ? 1 : 0)) : null,
     i.rated, i.fsrs_rating, i.fsrs_applied]));
}

async function loadResults() {
  const d = await (await fetch(`/api/exam/results?session_id=${st.session}`)).json();
  st.results = d;
  const done = d.items.filter(i => i.verdict).length;
  const answered = d.items.filter(i => i.answered).length;
  $('judgeBar').style.width = (done / d.total * 100) + '%';
  $('resultSummary').textContent =
    `共 ${d.total} 题 · 已作答 ${answered} · AI 已判分 ${done}` +
    (done < answered ? ' · 统一阅卷约需 1~3 分钟' : '');
  const sig = resultSig(d);
  if (sig === st.lastSig) return;                 // 数据没变: 不重绘列表 (防闪烁)
  st.lastSig = sig;
  const list = $('resultList');
  list.innerHTML = d.items.map((it, ix) => {
    const v = it.verdict;
    let chip;
    if (!it.answered) chip = `<span class="r-verdict none">⭘ 未作答</span>`;
    else if (!v) chip = `<span class="r-verdict wait">⏳ 统一阅卷中…</span>`;
    else if (v.error) chip = `<span class="r-verdict bad">⚠ ${esc(v.error)}</span>`;
    else chip = `<span class="r-verdict ${v.correct ? 'ok' : 'bad'}">${v.correct ? '✓ 回答正确' : '✗ 待加强'}</span>`;
    const rateName = {1: 'Again', 2: 'Hard', 3: 'Good', 4: 'Easy'};
    const rateInfo = it.answered
      ? (it.fsrs_applied
          ? `<span class="r-verdict fsr">📅 已计入 FSRS: ${rateName[it.fsrs_rating] || '-'}`
            + (it.verdict && !it.verdict.error && !it.verdict.correct ? ' (判错强制 Again)' : '') + '</span>'
          : `<span class="r-verdict none">📅 你的选择: ${it.rated ? rateName[it.rated] : '未选'} · 待计入</span>`)
      : '';
    const reply = it.answered
      ? `<div class="r-row"><span class="r-label">你的回答</span>
           <div class="r-reply ${it.reply ? '' : 'empty'}">${esc(it.reply || '(未识别到语音)')}</div>
           ${it.audio ? `<button class="abtn" data-play="${ix}">▶ 我的录音</button>` : ''}</div>`
      : '';
    const comment = v && v.comment
      ? `<div class="r-comment">💬 ${esc(v.comment)}</div>` : '';
    return `<div class="r-item">
      <div class="r-q">Q${ix + 1} · ${esc(it.question)}</div>
      ${chip}${rateInfo}${comment}
      <div class="r-row"><span class="r-label">正确答案</span>
        <div class="r-ans">${esc(it.answer)}</div>
        <button class="abtn" data-tts="${ix}">🔊 播放答案</button></div>
      ${reply}
    </div>`;
  }).join('');
  $('btnDone').textContent = '完成复习, 回首页';
}

/* 结果页委托: 播放录音 / TTS (共享播放器, 不叠加) */
$('resultList').addEventListener('click', e => {
  const play = e.target.closest('[data-play]');
  if (play) {
    const it = st.results.items[+play.dataset.play];
    playUrl(it.audio, play);
    return;
  }
  const tts = e.target.closest('[data-tts]');
  if (tts) {
    const it = st.results.items[+tts.dataset.tts];
    playUrl(`/api/say_stream?text=${encodeURIComponent(it.answer)}&voice=${VOICE}`, tts);
  }
});

/* 判分进度轮询: 全部有结论后停; 400 次上限防死等 (约 20 分钟) */
function pollJudge(tries = 0) {
  clearTimeout(st.pollTm);
  if (!st.judging) return;
  if (tries > 400) {
    st.judging = false;
    $('resultSummary').textContent = '判分耗时过长, 稍后手动刷新查看';
    return;
  }
  fetch(`/api/exam/state?session_id=${st.session}`)
    .then(r => r.json())
    .then(s => {
      const need = s.items.filter(i => i.answered && !i.judged).length;
      if (need === 0) {
        st.judging = false;
        loadResults().catch(() => {});
        return;
      }
      loadResults().catch(() => {});
      st.pollTm = setTimeout(() => pollJudge(tries + 1), 3000);
    })
    .catch(() => { st.pollTm = setTimeout(() => pollJudge(tries + 1), 4000); });
}

$('btnDone').addEventListener('click', () => { location.href = '/'; });

/* boot */
bootPrepare();
