/* 眼-头协调的性质测试。
 *
 * 这一层做的事情很容易被肉眼看漏：头转过去了没有？头和眼是同时动的吗？
 * 方向对不对？幅度够不够？眼睛有没有跟过头？
 * 所以全部写成可判定的性质，而不是"看起来自然"。
 */

import { IdleDriver } from "./drivers/idle.js";
import { Avatar } from "./index.js";

let failures = 0;
function check(name, ok, detail = "") {
  if (ok) console.log(`  ok   ${name}`);
  else { console.log(`  FAIL ${name}${detail ? "  -> " + detail : ""}`); failures++; }
}

const DT = 1 / 240;
const run = (idle, seconds, opts = {}) => {
  const n = Math.round(seconds / DT);
  const trace = [];
  for (let i = 0; i < n; i++) trace.push(idle.update(DT, opts));
  return trace;
};

/* 命名注视点只存在于 config/avatar.yaml，IdleDriver 的默认值里只有 viewer。
 * 不把这几个目标喂进来的话，setGaze("think") 会静默回退到 viewer，
 * 而 viewer 在原点——于是"头先动"这种断言会因为"根本没动"而恒真/恒假，
 * 测试看起来跑了，其实什么都没测到。
 * 这几个值必须和 config/avatar.yaml 的 gaze.targets 一致；
 * scripts/check_protocol.py 里有一条检查专门守这个同步。 */
const gazeTargets = {
  viewer: { x: 0.00, y: 0.00, drift: 0.10 },
  mic: { x: -0.34, y: 0.20, drift: 0.06 },
  think: { x: 0.62, y: -0.36, drift: 0.17 },
  down: { x: -0.18, y: 0.54, drift: 0.09 },
};
const makeIdle = (extra = {}) => new IdleDriver({ gaze: { targets: gazeTargets }, ...extra });

/* 通道启动时刻：按"走完自己最终位移的 20%"来判定。
 * 用固定阈值（头 >0.02、眼 >0.15）是不公平的——两条通道的量程差一个数量级，
 * 眼要到更高的绝对值才算"启动"，测出来自然是眼后启动，
 * 于是"眼先动"的断言会因为阈值选偏而翻车。 */
function onset(trace, pick, frac = 0.2) {
  const final = Math.hypot(...pick(trace[trace.length - 1]));
  if (!(final > 1e-6)) return -1;
  const thresh = final * frac;
  return trace.findIndex(o => Math.hypot(...pick(o)) > thresh);
}

console.log("小幅移视：只动眼睛，不动头");
{
  const idle = makeIdle();
  idle.setGaze("viewer", true);
  // 先跑到稳定
  run(idle, 0.5);
  idle.setGazeTarget(0.25, 0.1);   // 幅度 0.27 < recruit 0.42
  const tr = run(idle, 0.6);
  const headMag = Math.max(...tr.map(o => Math.hypot(o.headX, o.headY)));
  const eyeMag = Math.max(...tr.map(o => Math.hypot(o.gazeX, o.gazeY)));
  check("眼睛确实移动了", eyeMag > 0.2, `眼动幅度=${eyeMag.toFixed(3)}`);
  check("头基本没动", headMag < 0.02, `头动幅度=${headMag.toFixed(3)}`);
}

console.log("大幅移视：头被征用");
{
  const idle = makeIdle();
  idle.setGaze("viewer", true);
  run(idle, 0.5);
  idle.setGazeTarget(1.4, 0.0);    // 幅度 1.4 > recruit 0.42
  const tr = run(idle, 0.8);
  const headMag = Math.max(...tr.map(o => Math.abs(o.headX)));
  const eyeMag = tr[tr.length - 1].gazeX;
  check("头跟过来了", headMag > 0.3, `头动幅度=${headMag.toFixed(3)}`);

  /* 眼+头必须等于"当前实际下发的总目标"，而不是最初那个 1.4。
   * 落点之后还在做微扫视（drift），总目标一直在 1.4 附近小幅游走，
   * 拿 1.4 当基准会把正常的漂移误判成偏差。
   *
   * 这个不变量的意义在于抓"眼和头各自直奔完整目标"这个错误：
   * 那样稳态下和会是两倍目标，而且偏得越远偏得越离谱。
   * 驱动器自己知道当前下发的总目标是多少（gazeTo + headTo），
   * 拿它当基准，比硬编码数字严谨。 */
  const total = idle.gazeToX + idle.headToX;
  const sum = eyeMag + tr[tr.length - 1].headX;
  check("眼+头之和等于当前总目标（没有重复计入）",
    Math.abs(sum - total) < 0.02,
    `眼=${eyeMag.toFixed(3)} 头=${tr[tr.length - 1].headX.toFixed(3)} 和=${sum.toFixed(3)} 当前总目标=${total.toFixed(3)}`);

  // 单独验证头没有多算：头不应该超过它那份配额
  const cap = idle.cfg.gaze_coord;
  check("头没有超过 (总目标 - 征用门槛) 的配额",
    tr[tr.length - 1].headX <= Math.min(cap.head_capacity, Math.hypot(total, 0) - cap.recruit) + 1e-3,
    `头=${tr[tr.length - 1].headX.toFixed(3)} 配额=${Math.min(cap.head_capacity, Math.hypot(total, 0) - cap.recruit).toFixed(3)}`);
}

console.log("头有容量上限，不会把眼睛那份全抢走");
{
  const idle = makeIdle();
  idle.setGaze("viewer", true);
  run(idle, 0.5);
  idle.setGazeTarget(1.6, 0.0);   // 超出常规活动范围
  const tr = run(idle, 1.0);
  const last = tr[tr.length - 1];
  const cap = idle.cfg.gaze_coord.head_capacity;
  check("头转动不超过容量上限", Math.abs(last.headX) <= cap + 1e-6,
    `头=${last.headX.toFixed(3)} 上限=${cap}`);
  check("眼睛保底有自己的份额（不变成 0）", Math.abs(last.gazeX) > 0.05,
    `眼=${last.gazeX.toFixed(3)}`);
}

console.log("眼-头时序：可预期注视头先动");
{
  // 真人对"自己决定要看的地方"是头先动，比眼早约 100ms。
  // 这条如果不成立，"她在看那"这个动作会读成"眼在追着头"，
  // 也就是一个人偶被拖着脖子看。
  const idle = makeIdle();
  idle.setGaze("viewer", true);
  run(idle, 0.5);
  idle.reactive = false;
  idle.setGaze("think", false);    // 我们自己选的注视点
  const tr = run(idle, 0.5);
  const headOn = onset(tr, o => [o.headX, o.headY]);
  const eyeOn = onset(tr, o => [o.gazeX, o.gazeY]);
  check("头比眼先动", headOn >= 0 && eyeOn >= 0 && headOn < eyeOn,
    `头启动帧=${headOn} 眼启动帧=${eyeOn}`);
}

console.log("眼-头时序：反应性注视眼先动");
{
  // 鼠标是外来的刺激。这时是眼先看到、头后跟上，
  // 和上面刻意选注视点的时序正好相反。
  const idle = makeIdle();
  idle.setGaze("viewer", true);
  run(idle, 0.5);
  idle.setMousePos(-1.4, 0);
  idle.setGazeTarget(-1.4, 0);     // 反应性
  const tr = run(idle, 0.5);
  const headOn = onset(tr, o => [o.headX, o.headY]);
  const eyeOn = onset(tr, o => [o.gazeX, o.gazeY]);
  check("眼比头先动", eyeOn >= 0 && headOn >= 0 && eyeOn < headOn,
    `眼启动帧=${eyeOn} 头启动帧=${headOn}`);
}

console.log("眼跳不应期");
{
  // 两次眼跳之间的最小间隔约 150ms。早先的实现没有这个限制，
  // 密集微扫视会连成一条抖动的线。
  const idle = makeIdle();
  idle.setGaze("viewer", true);
  run(idle, 0.5);
  let accepted = 0;
  for (let k = 0; k < 20; k++) {
    if (idle._moveTo(0.3 + k * 0.02, 0.1, 0.05)) accepted++;
    idle.t += 0.01;                 // 每次只推进 10ms，必然落在不应期内
  }
  check("密集请求被不应期挡下", accepted <= 3, `20 次请求里只接受 ${accepted} 次`);

  const idle2 = makeIdle();
  idle2.setGaze("viewer", true);
  run(idle2, 0.5);
  idle2.t += 1.0;                    // 间隔足够长
  check("间隔够长时正常接受", idle2._moveTo(0.9, 0.2, 0.1) === true);
}

console.log("大幅移视伴随眨眼");
{
  // 大于约 30 度的移视常和眨眼同时发生，用来遮挡角膜上扫的盲区。
  const idle = makeIdle();
  idle.setGaze("viewer", true);
  run(idle, 0.5);
  idle.blinkPhase = -1; idle.nextBlink = 99;   // 屏蔽随机眨眼
  idle.setGazeTarget(1.3, 0);
  check("大幅移视请求了眨眼", idle.pendingShiftBlink === true);

  const idle2 = makeIdle();
  idle2.setGaze("viewer", true);
  run(idle2, 0.5);
  idle2.nextBlink = 99;
  idle2.t += 1.0;
  idle2.setGazeTarget(0.2, 0.05);   // 小幅
  check("小幅移视不触发眨眼", idle2.pendingShiftBlink === false);
}

console.log("时序不变量");
{
  // 头部朝向只应由眼-头协调 + 噪声决定，两者都必须有界。
  // 一旦 headX 跑飞（比如延迟线查询越界），姿态会整块歪掉，
  // 而这种错误在浏览器里表现为"脸慢慢转走不回来"。
  let bad = null;
  const idle = makeIdle();
  idle.setGaze("viewer", true);
  idle.setMouseFollow(true);
  idle.setMousePos(0, 0);
  let phase = 0;
  for (let i = 0; i < 240 * 30; i++) {
    // 鼠标绕一大圈，制造连续的移视请求
    phase += 0.05;
    idle.setMousePos(Math.cos(phase) * 1.5, Math.sin(phase) * 1.2);
    idle.setGazeTarget(Math.cos(phase) * 1.5, Math.sin(phase) * 1.2);
    const o = idle.update(DT, {});
    if (!Number.isFinite(o.headX) || !Number.isFinite(o.headY)) { bad = `第 ${i} 帧 head 非有限值`; break; }
    if (Math.hypot(o.headX, o.headY) > 1.6) { bad = `第 ${i} 帧 head 越界 ${Math.hypot(o.headX, o.headY)}`; break; }
    if (!Number.isFinite(o.gazeX)) { bad = `第 ${i} 帧 gaze 非有限值`; break; }
  }
  check("持续移视 30 秒，输出始终有限且有界", !bad, bad || "");
}

console.log("接管视线时不产生跳变");
{
  // emotion/gesture 会临时抢走视线控制权，这时 idle 必须把游走和
  // 眼跳都停住而不是继续往某个方向跑，否则松手的一瞬间会有一跳。
  const idle = makeIdle();
  idle.setGaze("viewer", true);
  run(idle, 0.4);
  idle.claimGaze(true);
  const before = idle.update(DT, {});
  const tr = [];
  for (let i = 0; i < 60; i++) tr.push(idle.update(DT, {}));
  const drift = Math.hypot(tr[tr.length - 1].gazeX - before.gazeX,
                           tr[tr.length - 1].gazeY - before.gazeY);
  check("被接管时注视基本冻结", drift < 0.01, `漂移=${drift.toFixed(4)}`);
}

console.log("显式换落点后微扫视围绕新目标");
{
  const idle = makeIdle();
  idle.nextBlink = 99;
  idle.nextMicro = 99;
  idle.setMotion({ gazeDrift: 0 });
  idle.setGazeTarget(-0.30, 0.05);
  run(idle, 0.3);
  idle.setGazeTarget(0.30, 0.10);
  run(idle, 0.3);
  // 强制发生一次实际微扫视；零漂移幅度应保持最新落点。
  idle.nextMicro = 0;
  const last = run(idle, 0.3).at(-1);
  check("微扫视不会回到第一个 custom 目标",
    Math.abs(last.gazeX - 0.30) < 1e-6 && Math.abs(last.gazeY - 0.10) < 1e-6,
    `眼位=(${last.gazeX.toFixed(3)}, ${last.gazeY.toFixed(3)})`);
}

console.log("指针离开后归还 viewer 注视点");
{
  const avatar = new Avatar().configure({
    gaze: { targets: gazeTargets },
    emotions: { base: { shift: {} } },
  });
  avatar.idle.nextBlink = 99;
  avatar.idle.nextMicro = 99;
  const advance = seconds => {
    for (let i = 0; i < Math.round(seconds / DT); i++) avatar._frame(DT);
  };
  avatar.setGazeMode("follow");
  avatar.onUserInteraction("mousemove", 0.8, -0.1);
  advance(1);
  check("重置前实际眼位跟随指针", avatar.eyeFrame.params.ParamEyeBallX > 0.3);
  avatar.resetGaze();
  advance(1.5);
  check("重置后稳定看回 viewer 而非残留指针",
    avatar.idle.targetName === "viewer"
      && Math.abs(avatar.eyeFrame.params.ParamEyeBallX) < 1e-4
      && Math.abs(avatar.eyeFrame.params.ParamEyeBallY) < 1e-4,
    `目标=${avatar.idle.targetName} 眼位=(${avatar.eyeFrame.params.ParamEyeBallX.toFixed(4)}, ${avatar.eyeFrame.params.ParamEyeBallY.toFixed(4)})`);
  avatar.onUserInteraction("mousemove", -0.8, 0);
  advance(1);
  check("新的指针输入仍能重新接管", avatar.eyeFrame.params.ParamEyeBallX < -0.3);
}

console.log("自动眨眼在低帧率仍完整闭合，并恢复困眼");
{
  for (const fps of [20, 30, 60]) {
    const avatar = new Avatar().configure({
      gaze: { targets: gazeTargets },
      emotions: { base: { shift: {} } },
      response: { ParamEyeLOpen: 0.028, ParamEyeROpen: 0.028 },
    });
    avatar.idle.nextBlink = 99;
    avatar.idle.cfg.energy.drain_per_sec = 0;
    avatar.setEyeExpression("sleepy");
    const dt = 1 / fps;
    for (let i = 0; i < fps; i++) avatar._frame(dt);
    const before = avatar.eyeFrame.params;
    // 到期调度自然眨眼；屏蔽随机双眨，使一轮结束可判定。
    avatar.idle.nextBlink = 0;
    avatar.idle.cfg.blink.double_prob = 0;
    avatar.idle.cfg.blink.min_interval = 99;
    const trace = [];
    for (let i = 0; i < fps; i++) {
      avatar._frame(dt);
      trace.push(avatar.eyeFrame);
    }
    check(`${fps} FPS 自动眨眼确实完整闭合双眼`,
      trace.some(frame => frame.params.ParamEyeLOpen === 0 && frame.params.ParamEyeROpen === 0));
    const closedFrames = trace.filter(frame => frame.blink === 1).length;
    check(`${fps} FPS 保持原时钟，没有延长闭合或重复触发`,
      closedFrames >= 1 && closedFrames <= Math.ceil(avatar.idle.cfg.blink.hold_ms / 1000 * fps) + 1
        && avatar.idle.blinkPhase === -1 && avatar.idle.blinkQueued === 0);
    const after = trace.at(-1).params;
    check(`${fps} FPS 自动眨眼恢复原困眼开度`,
      Math.abs(after.ParamEyeLOpen - before.ParamEyeLOpen) < 1e-6
        && Math.abs(after.ParamEyeROpen - before.ParamEyeROpen) < 1e-6);
  }
}

console.log(failures === 0 ? "\n全部通过" : `\n${failures} 条不通过`);
process.exit(failures === 0 ? 0 : 1);
