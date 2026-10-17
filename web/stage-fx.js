/* Midnight Living Stage · 舞台氛围层（Canvas 2D，无新依赖）
 *
 * 状态源全部是真实运行状态，不新增业务状态：
 *   - #avatarStage[data-interaction]：app.js reflectInteractionState 每帧写入
 *     idle / monitoring / listening / speaking / preparing / thinking / yielding / unavailable
 *   - #meterFill 的宽度：app.js 用真实麦克 RMS 写入，仅倾听时读取作音量响应
 *   - #holoDeck 内 .aqi-lane[data-status="running"]：任务真实执行态（executive 积木）
 *   - #holoDeck 子节点新增：卡片送达 = 一次完成反馈
 *
 * 渲染约束：唯一一条 rAF；页面隐藏即暂停；prefers-reduced-motion 不启动画布
 * （CSS 静态光场兜底，见 stage-fx.css）；DPR 上限 1.5；粒子 ≤12。
 */
(() => {
  const stage = document.querySelector('.stage');
  const avatarStage = document.getElementById('avatarStage');
  const holoDeck = document.getElementById('holoDeck');
  const meterFill = document.getElementById('meterFill');
  if (!stage || !avatarStage) return;

  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const canvas = document.createElement('canvas');
  canvas.id = 'stageFx';
  canvas.setAttribute('aria-hidden', 'true');
  stage.prepend(canvas);
  const ctx = canvas.getContext('2d');
  if (!ctx) { canvas.remove(); return; }

  let W = 0, H = 0, DPR = 1;
  let particles = [];
  let sparks = [];        // 完成反馈的一次性星屑
  let running = false;    // rAF 是否在跑
  let raf = 0;
  let last = 0;
  let clock = 0;
  let interaction = 'idle';
  let taskRunning = false;
  let amp = 0;            // 平滑后的真实音量 0..1
  let avatarRect = null;  // 立绘在画布坐标系里的位置缓存
  let frame = 0;

  const NARROW = () => W < 760;
  const COUNT = () => (NARROW() ? 6 : 12);

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
    avatarRect = null;
    seed();
  }

  function measureAvatar() {
    const s = stage.getBoundingClientRect();
    const a = avatarStage.getBoundingClientRect();
    if (a.width < 2 || a.height < 2) return null;
    return { x: a.left - s.left + a.width / 2, y: a.top - s.top + a.height * 0.42, r: Math.max(a.width, a.height) * 0.5, footY: a.top - s.top + a.height * 0.94 };
  }

  function seed() {
    particles = [];
    const n = COUNT();
    for (let i = 0; i < n; i++) particles.push(spawn(i / n));
  }

  function spawn(t = Math.random()) {
    // 低密度、慢速、偏向舞台右半（立绘区）；左侧文本区只放更暗的少数
    const right = Math.random() < 0.7;
    return {
      x: right ? W * (0.55 + Math.random() * 0.42) : W * Math.random() * 0.5,
      y: H * Math.random(),
      r: 1.2 + Math.random() * 1.6,
      vx: (Math.random() - 0.5) * 3.2,          // px/s，很慢
      vy: -2 - Math.random() * 3.5,
      o: (right ? 0.32 : 0.16) + Math.random() * 0.18,
      warm: Math.random() < 0.28,               // 少量暖黄，其余月白
      phase: Math.random() * Math.PI * 2,
      t,
    };
  }

  function setInteraction(id) {
    if (id === interaction) return;
    interaction = id || 'idle';
  }

  function burst() {
    const a = avatarRect || measureAvatar();
    if (!a) return;
    for (let i = 0; i < 10; i++) {
      const ang = Math.random() * Math.PI * 2;
      const sp = 26 + Math.random() * 46;
      sparks.push({ x: a.x, y: a.y - a.r * 0.2, vx: Math.cos(ang) * sp, vy: Math.sin(ang) * sp - 18, life: 1, r: 1.4 + Math.random() * 1.4 });
    }
  }

  function readHolo() {
    if (!holoDeck) { taskRunning = false; return; }
    taskRunning = !!holoDeck.querySelector('.aqi-lane[data-status="running"]');
  }

  function step(dt) {
    clock += dt;
    frame++;
    if (frame % 30 === 0 || !avatarRect) avatarRect = measureAvatar();

    // 音量响应只来自真实 RMS（meterFill 的宽度由 app.js 的麦克包络写入）
    const targetAmp = interaction === 'listening' && meterFill
      ? Math.min(1, parseFloat(meterFill.style.width || '0') / 100) : 0;
    amp += (targetAmp - amp) * Math.min(1, dt * 10);

    const thinking = interaction === 'thinking' || interaction === 'preparing';
    const quiet = interaction === 'unavailable' || interaction === 'yielding';
    const converge = thinking && avatarRect ? avatarRect : null;

    ctx.clearRect(0, 0, W, H);

    // 倾听光环：局部柔和光晕，半径随真实音量呼吸
    if ((interaction === 'listening' || interaction === 'speaking') && avatarRect && !quiet) {
      const a = avatarRect;
      const glowR = a.r * (1.15 + amp * 0.5 + Math.sin(clock * 2.2) * 0.04);
      const g = ctx.createRadialGradient(a.x, a.y, a.r * 0.3, a.x, a.y, glowR);
      const warm = interaction === 'listening';
      g.addColorStop(0, warm ? `rgba(201,162,39,${0.10 + amp * 0.12})` : 'rgba(125,150,210,0.10)');
      g.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(a.x, a.y, glowR, 0, Math.PI * 2); ctx.fill();
    }

    // 任务执行：一条克制的流动轨迹沿舞台右侧缓慢行进
    if (taskRunning && !NARROW()) {
      const t = (clock % 6) / 6;                    // 6s 一轮
      const x = W * 0.985;
      const y = H * (0.12 + t * 0.76);
      const trail = 90;
      const g = ctx.createLinearGradient(x, y - trail, x, y);
      g.addColorStop(0, 'rgba(201,162,39,0)');
      g.addColorStop(1, 'rgba(232,194,106,0.5)');
      ctx.strokeStyle = g; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.moveTo(x, y - trail); ctx.lineTo(x, y); ctx.stroke();
      ctx.fillStyle = 'rgba(232,194,106,0.85)';
      ctx.beginPath(); ctx.arc(x, y, 2, 0, Math.PI * 2); ctx.fill();
    }

    // 粒子：慢漂浮；思考时向立绘轻微汇聚
    const speedScale = quiet ? 0.4 : 1;
    for (const p of particles) {
      if (converge) {
        p.vx += ((converge.x - p.x) * 0.006 - p.vx) * dt * 0.9;
        p.vy += ((converge.y - p.y) * 0.006 - p.vy) * dt * 0.9;
      } else {
        p.vx += ((Math.sin(clock * 0.3 + p.phase) * 2.4) - p.vx) * dt * 0.25;
        p.vy += ((-3.2) - p.vy) * dt * 0.25;
      }
      p.x += p.vx * dt * speedScale;
      p.y += p.vy * dt * speedScale;
      if (p.y < -6 || p.x < -6 || p.x > W + 6 || p.y > H + 6) Object.assign(p, spawn(), { y: H + 4 });
      const breathe = 0.75 + 0.25 * Math.sin(clock * 0.8 + p.phase);
      const alpha = p.o * breathe * (quiet ? 0.5 : 1) * (thinking ? 1.25 : 1);
      ctx.fillStyle = p.warm ? `rgba(232,194,106,${alpha})` : `rgba(232,228,217,${alpha * 0.8})`;
      ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fill();
    }

    // 完成反馈：一次性星屑，800ms 内散完
    for (let i = sparks.length - 1; i >= 0; i--) {
      const s = sparks[i];
      s.life -= dt / 0.8;
      if (s.life <= 0) { sparks.splice(i, 1); continue; }
      s.x += s.vx * dt; s.y += s.vy * dt; s.vy += 30 * dt;
      ctx.fillStyle = `rgba(232,194,106,${s.life * 0.9})`;
      ctx.beginPath(); ctx.arc(s.x, s.y, s.r * s.life + 0.4, 0, Math.PI * 2); ctx.fill();
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
