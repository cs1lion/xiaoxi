/* 舞台氛围层（Canvas 2D，无新依赖）
 *
 * 两件事：
 *   1. 图版窗内的空气层——光柱 / 光池 / 慢扫描 / 颗粒（DOM + CSS，见 stage-fx.css）。
 *      结构只建一次；强弱交给 CSS 按 data-interaction 分档，不由 JS 逐帧改。
 *   2. 纸面上的浮尘——一张 2D canvas，慢速上升的极淡金色尘点。人物那边已经有
 *      真 3D 尘埃，这一层只负责让纸面不至于死板。
 *
 * 状态源全部是真实运行状态，不新增业务状态：
 *   - #avatarStage[data-interaction]：app.js reflectInteractionState 每帧写入
 *   - #meterFill 的宽度：app.js 用真实麦克 RMS 写入（光层用它调音量响应）
 *   - #holoDeck 内 .aqi-lane[data-status="running"]：任务真实执行态（executive 积木）
 *   - #holoDeck 子节点新增：卡片送达 = 一次完成反馈
 *
 * 渲染约束：唯一一条 rAF；页面隐藏即暂停；prefers-reduced-motion 不启动画布
 * （CSS 静态光场兜底）；DPR 上限 1.5；纸面粒子 ≤8。
 *
 * 2026-10-18：人物搬进暗色图版窗之后，原来画在 canvas 上的"立绘光晕"和
 * 完成星屑都被图版挡住了（canvas 在最底层，图版是不透明窗口）。这两样改成
 * 图版内的 CSS 层：光晕交给 .plate-atmos 的分档变量，星屑变成一次径向闪光。
 */
(() => {
  const stage = document.querySelector('.stage');
  const avatarStage = document.getElementById('avatarStage');
  const holoDeck = document.getElementById('holoDeck');
  const meterFill = document.getElementById('meterFill');
  if (!stage || !avatarStage) return;

  /* 图版窗内的空气层：两道光柱 + 光池 + 慢扫描 + 颗粒。纯装饰，不进无障碍树。 */
  const plateInner = document.querySelector('.plate-inner');
  let plateAtmos = null;
  if (plateInner && !plateInner.querySelector('.plate-atmos')) {
    plateAtmos = document.createElement('div');
    plateAtmos.className = 'plate-atmos';
    plateAtmos.setAttribute('aria-hidden', 'true');
    for (const name of ['pa-shaft', 'pa-shaft pa-shaft-b', 'pa-pool', 'pa-scan', 'pa-grain']) {
      const layer = document.createElement('span');
      layer.className = name;
      plateAtmos.appendChild(layer);
    }
    plateInner.prepend(plateAtmos);
  } else {
    plateAtmos = plateInner?.querySelector('.plate-atmos') || null;
  }

  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const canvas = document.createElement('canvas');
  canvas.id = 'stageFx';
  canvas.setAttribute('aria-hidden', 'true');
  stage.prepend(canvas);
  const ctx = canvas.getContext('2d');
  if (!ctx) { canvas.remove(); return; }

  let W = 0, H = 0, DPR = 1;
  let particles = [];
  let running = false;    // rAF 是否在跑
  let raf = 0;
  let last = 0;
  let clock = 0;
  let interaction = 'idle';
  let taskRunning = false;
  let amp = 0;            // 平滑后的真实音量 0..1
  let burstTimer = 0;

  const NARROW = () => W < 760;
  const COUNT = () => (NARROW() ? 4 : 8);

  function resize() {
    const r = stage.getBoundingClientRect();
    DPR = Math.min(globalThis.devicePixelRatio || 1, 1.5);
    W = Math.max(1, Math.round(r.width));
    H = Math.max(1, Math.round(r.height));
    canvas.width = Math.round(W * DPR);
    canvas.height = Math.round(H * DPR);
    canvas.style.width = `${W}px`;
    canvas.style.height = `${H}px`;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    seed();
  }

  function seed() {
    particles = [];
    const n = COUNT();
    for (let i = 0; i < n; i++) particles.push(spawn(i / n));
  }

  function spawn(t = Math.random()) {
    // 纸面上的尘点：极淡的金与墨，慢速上升；左侧文字区更少更暗。
    const right = Math.random() < 0.62;
    return {
      x: right ? W * (0.52 + Math.random() * 0.44) : W * Math.random() * 0.45,
      y: H * Math.random(),
      r: 0.9 + Math.random() * 1.5,
      vx: (Math.random() - 0.5) * 2.2,
      vy: -1.6 - Math.random() * 3,
      o: (right ? 0.16 : 0.09) + Math.random() * 0.08,
      gold: Math.random() < 0.55,
      phase: Math.random() * Math.PI * 2,
      t,
    };
  }

  function setInteraction(id) {
    if (id === interaction) return;
    interaction = id || 'idle';
  }

  /* 卡片送达：一次极短的径向闪光，落在图版内部，不播报警式循环。 */
  function burst() {
    if (!plateAtmos) return;
    plateAtmos.classList.remove('is-burst');
    // 强制重排，让同一个动画能被连续触发
    void plateAtmos.offsetWidth;
    plateAtmos.classList.add('is-burst');
    clearTimeout(burstTimer);
    burstTimer = setTimeout(() => plateAtmos?.classList.remove('is-burst'), 900);
  }

  function readHolo() {
    if (!holoDeck) { taskRunning = false; return; }
    taskRunning = !!holoDeck.querySelector('.aqi-lane[data-status="running"]');
  }

  function step(dt) {
    clock += dt;

    // 音量响应只来自真实 RMS（meterFill 的宽度由 app.js 的麦克包络写入）
    const targetAmp = interaction === 'listening' && meterFill
      ? Math.min(1, parseFloat(meterFill.style.width || '0') / 100) : 0;
    amp += (targetAmp - amp) * Math.min(1, dt * 10);

    const thinking = interaction === 'thinking' || interaction === 'preparing';
    const quiet = interaction === 'unavailable' || interaction === 'yielding';

    ctx.clearRect(0, 0, W, H);

    // 任务执行：一条克制的流光沿舞台右缘缓慢下行，只在有单在跑时出现
    if (taskRunning && !NARROW()) {
      const t = (clock % 6) / 6;
      const x = W - 3;
      const y = H * (0.12 + t * 0.76);
      const trail = 90;
      const g = ctx.createLinearGradient(x, y - trail, x, y);
      g.addColorStop(0, 'rgba(155,122,62,0)');
      g.addColorStop(1, 'rgba(169,58,42,.42)');
      ctx.strokeStyle = g; ctx.lineWidth = 1.4;
      ctx.beginPath(); ctx.moveTo(x, y - trail); ctx.lineTo(x, y); ctx.stroke();
      ctx.fillStyle = 'rgba(169,58,42,.7)';
      ctx.beginPath(); ctx.arc(x, y, 1.8, 0, Math.PI * 2); ctx.fill();
    }

    // 浮尘：慢漂浮，粗细与亮度各带一点随机，落笔感来自呼吸式明暗
    const speedScale = quiet ? 0.45 : 1;
    for (const p of particles) {
      p.vx += ((Math.sin(clock * 0.26 + p.phase) * 1.6) - p.vx) * dt * 0.22;
      p.vy += ((-2.4) - p.vy) * dt * 0.22;
      p.x += p.vx * dt * speedScale;
      p.y += p.vy * dt * speedScale;
      if (p.y < -6 || p.x < -6 || p.x > W + 6 || p.y > H + 6) Object.assign(p, spawn(), { y: H + 4 });
      const breathe = 0.7 + 0.3 * Math.sin(clock * 0.7 + p.phase);
      const alpha = p.o * breathe * (quiet ? 0.5 : 1) * (thinking ? 1.2 : 1) * (1 + amp * 0.5);
      ctx.fillStyle = p.gold ? `rgba(155,122,62,${alpha})` : `rgba(33,28,22,${alpha * 0.55})`;
      ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fill();
    }
  }

  function loop(now) {
    if (!running) return;
    const dt = Math.min(0.05, Math.max(0, (now - last) / 1000));
    last = now;
    step(dt);
    raf = requestAnimationFrame(loop);
  }

  function start() {
    if (running || reduceMotion.matches || document.hidden) return;
    running = true; last = performance.now();
    raf = requestAnimationFrame(loop);
  }
  function stop() {
    running = false; cancelAnimationFrame(raf);
    ctx.clearRect(0, 0, W, H);
  }

  // —— 真实状态订阅 ——
  setInteraction(avatarStage.dataset.interaction || 'idle');
  new MutationObserver(() => setInteraction(avatarStage.dataset.interaction || 'idle'))
    .observe(avatarStage, { attributes: true, attributeFilter: ['data-interaction'] });

  if (holoDeck) {
    readHolo();
    new MutationObserver(mutations => {
      readHolo();
      for (const m of mutations) {
        for (const node of m.addedNodes) {
          // 卡片送达 = 完成反馈，只播一次，不播报警式循环
          if (node.nodeType === 1 && node.classList?.contains('aqi-block')) burst();
        }
      }
    }).observe(holoDeck, { childList: true, subtree: true });
  }

  document.addEventListener('visibilitychange', () => (document.hidden ? stop() : start()));
  reduceMotion.addEventListener?.('change', () => (reduceMotion.matches ? stop() : start()));
  new ResizeObserver(resize).observe(stage);
  resize();
  start();
})();
