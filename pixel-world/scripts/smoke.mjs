/* 神谕闭环的浏览器回归。
 *
 * 验的不是"能编译"，而是用户在屏幕上能看到的四件事：
 *   1. 开局小人在祭坛边自己走动
 *   2. 一句话神谕 → 行礼 → 走进对应铺子 → 铺子开工
 *   3. 交付 → 人出现在门口 → 回到祭坛
 *   4. 认不出的话不让世界动
 *
 * 两种模式：
 *   - 默认（?ledger=off）：纯沙盒，不依赖本地服务
 *   - `LEDGER=1 node scripts/smoke.mjs`：接主服务台账，验"操作台与世界连通"
 *
 * 跑法：
 *   1. npm run dev（端口 5188）
 *   2. node scripts/smoke.mjs                # 沙盒模式
 *      LEDGER=1 node scripts/smoke.mjs       # 台账模式（需先起 8000）
 * 浏览器用主仓库根目录的 Playwright；原型目录不带这份依赖。
 */
import { chromium } from '../../node_modules/@playwright/test/index.mjs';
import { mkdir } from 'node:fs/promises';
import assert from 'node:assert/strict';

const URL_BASE = process.env.PIXEL_WORLD_URL ?? 'http://127.0.0.1:5188/';
const OUT = 'eval/screens';
// 台账模式：先起主服务（8000），再 `LEDGER=1 node scripts/smoke.mjs`。
const LEDGER = process.env.LEDGER === '1';

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1010, height: 940 } });
const page = await context.newPage();
const errors = [];
page.on('pageerror', (error) => errors.push(error.message));

const checks = [];
const check = (ok, label) => {
  assert.ok(ok, label);
  checks.push(label);
};

const state = () => page.evaluate(() => window.Oracle.view());
const waitFor = async (predicate, label, timeout = 25000) => {
  const started = Date.now();
  for (;;) {
    const snapshot = await state();
    if (predicate(snapshot)) {
      checks.push(label);
      return snapshot;
    }
    if (Date.now() - started > timeout) {
      throw new Error(`超时：${label}\n最后状态 ${JSON.stringify(snapshot.villager)}`);
    }
    await page.waitForTimeout(120);
  }
};

const report = (title) => {
  console.log(`\n${title}：${checks.length} 项通过`);
  for (const label of checks) console.log(`  ok  ${label}`);
};

/** 台账模式：验主服务的督办状态真的能驱动这个世界。 */
async function runLedgerSuite() {
  await waitFor((s) => s.ledger.connected === true, '台账连接建立', 15000);
  const linked = await state();
  check(linked.ledger.lastSyncAt !== null, '世界拿到过一次台账快照');
  await page.waitForFunction(
    () => (document.querySelector('#status')?.textContent ?? '').includes('台账在线'),
    null,
    { timeout: 5000 },
  );
  const statusText = await page.textContent('#status');
  check(
    statusText.includes('台账在线') || statusText.includes('台账离线'),
    '状态行读得出台账状态',
  );
  check(
    statusText.includes('台账在线') && !statusText.includes('离线 Mock'),
    '台账在线时状态行不标离线 Mock',
  );

  // 注入一帧 commission_update，等价于服务端真发了一帧。不写主服务：
  // 推送帧本来就是外部数据，用它验"上游派活 → 世界动起来"这条链。
  const driven = await page.evaluate(async () => {
    window.Oracle.pushLedger({
      id: 'c_smoke_probe',
      title: '把便笺整理成三版标题',
      agent_id: 'scribe',
      status: 'running',
      step_idx: 2,
      blocked_question: '',
    });
    await new Promise((resolve) => setTimeout(resolve, 400));
    return window.Oracle.view();
  });
  check(driven.tasks.some((t) => t.id === 'c_smoke_probe'), '上游单进了世界的活动清单');
  check(driven.villager.mode !== 'idle', '她停下日常，去干上游派的活');

  // 完成权在台账：台账单不许人工收尾，按 #finish 必须被拒且她继续干活。
  await waitFor((s) => s.villager.mode === 'working', '上游单进入干活态', 15000);
  await page.click('#finish');
  await page.waitForFunction(
    () => (document.querySelector('#reply')?.textContent ?? '').includes('完成权在台账'),
    null,
    { timeout: 5000 },
  );
  check(true, '台账单人工完成被拒，回执写明完成权在台账');
  check((await state()).villager.mode === 'working', '被拒后台账单继续跑');

  // 上游说收工，她走正常交付，不是凭空结束。
  const delivered = await page.evaluate(async () => {
    window.Oracle.pushLedger({
      id: 'c_smoke_probe',
      title: '把便笺整理成三版标题',
      agent_id: 'scribe',
      status: 'ready_to_report',
      step_idx: 3,
      blocked_question: '',
    });
    await new Promise((resolve) => setTimeout(resolve, 300));
    return window.Oracle.view();
  });
  check(
    delivered.villager.mode === 'delivering' || delivered.villager.mode === 'toAltar',
    '台账说收工，她走交付',
  );
  check(!delivered.tasks.some((t) => t.id === 'c_smoke_probe'), '收工的单退出活动清单');
  check(delivered.deliveries.ledger === 1, '台账交付单独计入台账统计');
  check(delivered.deliveries.mock === 0, '台账交付不混进 Mock 统计');

  // blocked_question 只展示、不回答：进快照，状态行与回执都能读到，转 running 后清除。
  const blocked = await page.evaluate(async () => {
    window.Oracle.pushLedger({
      id: 'c_smoke_blocked',
      title: '把便笺整理成三版标题',
      agent_id: 'scribe',
      status: 'blocked',
      step_idx: 3,
      blocked_question: '先按短句还是完整句式？',
    });
    await new Promise((resolve) => setTimeout(resolve, 400));
    return window.Oracle.view();
  });
  check(
    blocked.tasks.some((t) => t.blockedQuestion === '先按短句还是完整句式？'),
    'blocked_question 进了世界快照',
  );
  await page.waitForFunction(
    () => (document.querySelector('#status')?.textContent ?? '').includes('在等你的答复：'),
    null,
    { timeout: 5000 },
  );
  check(true, '状态行写出在等你的答复');
  check(
    (await page.textContent('#reply')).includes('在等你的答复：'),
    '同步回执写出在等你的答复',
  );

  // 上游把单转成 running：问题立刻清掉，不残留。
  const resumed = await page.evaluate(async () => {
    window.Oracle.pushLedger({
      id: 'c_smoke_blocked',
      title: '把便笺整理成三版标题',
      agent_id: 'scribe',
      status: 'running',
      step_idx: 4,
      blocked_question: '',
    });
    await new Promise((resolve) => setTimeout(resolve, 400));
    return window.Oracle.view();
  });
  check(
    resumed.tasks.every((t) => t.blockedQuestion === null),
    '转 running 后答复等待被清除',
  );

  // 收工后这条单离开活动清单，问题不残留。
  await page.evaluate(async () => {
    window.Oracle.pushLedger({
      id: 'c_smoke_blocked',
      title: '把便笺整理成三版标题',
      agent_id: 'scribe',
      status: 'ready_to_report',
      step_idx: 5,
      blocked_question: '',
    });
    await new Promise((resolve) => setTimeout(resolve, 300));
  });
  check(
    !(await state()).tasks.some((t) => t.id === 'c_smoke_blocked'),
    '收工后带问题的单退出活动清单',
  );
}

/** 沙盒模式：神谕闭环 + 日常 + 天气 + 造物主 + 信仰 + 日报 + 外部桥接。 */
async function runSandboxSuite() {
  const opening = await state();
  check(opening.villagers.length === 2, '世界里有两个小人');
  check(
    opening.villagers[0]?.name !== opening.villagers[1]?.name &&
      opening.villagers[0]?.id !== opening.villagers[1]?.id,
    '两个角色各有独立 id 与名字',
  );
  check(opening.ledger.mock === true, '离线模式在快照里标成离线 Mock');
  await page.waitForFunction(
    () => (document.querySelector('#status')?.textContent ?? '').includes('离线 Mock'),
    null,
    { timeout: 5000 },
  );
  const openingStatus = await page.textContent('#status');
  check(
    openingStatus.includes('离线 Mock') && openingStatus.includes('台账离线'),
    '状态行标出离线 Mock',
  );
  check(
    openingStatus.includes('莞尔：') && openingStatus.includes('阿雾：'),
    '状态行显示多角色摘要',
  );
  // 她自己会动，不是屏保。
  const before = opening.villager.pos;
  await page.waitForTimeout(3200);
  const wandering = await state();
  const moved = Math.hypot(
    wandering.villager.pos.x - before.x,
    wandering.villager.pos.y - before.y,
  );
  check(moved > 0.2 || wandering.villager.mode !== 'idle', '没人管的时候她会自己走动');
  check(
    wandering.villagers.every((item) => typeof item.routine === 'string'),
    '两个角色各自有独立作息',
  );

  // 认不出的句子不让世界动。
  const unknown = await page.evaluate(() => window.Oracle.say('今天天气不错'));
  check(unknown.ok === false, '认不出的输入被拒');
  check((await state()).villager.mode !== 'saluting', '认不出的输入没有触发神谕');

  // 一句代码活：行礼 → 赶路 → 开工。派活只叫一个人。
  await page.fill('#utterance', '帮我写个爬虫脚本');
  await page.click('button[type="submit"]');
  await page.waitForFunction(
    () => (document.querySelector('#reply')?.textContent ?? '').includes('神谕已送达'),
    null,
    { timeout: 5000 },
  );
  check(true, '界面回了世界结算后的神谕回执');
  const saluting = await waitFor((s) => s.villager.mode === 'saluting', '她停下手里的事行礼');
  check(saluting.villager.bubble !== null, '行礼时头上有字');
  await page.screenshot({ path: `${OUT}/01-salute.png` });

  const working = await waitFor((s) => s.villager.mode === 'working', '她走进铺子开始干活');
  check(working.villager.visible === false, '进屋后人不在门外');
  check(
    working.villagers[1]?.mode !== 'working' && working.villagers[1]?.taskId === null,
    '派活只叫一个人，另一个还在祭坛',
  );
  const operating = working.workstations.filter((w) => w.activity.kind === 'operating');
  check(operating.length === 1, '恰好一台铺子在运转');
  const activeBuilding = working.buildings.find(
    (b) => b.instanceId === operating[0].buildingId,
  );
  check(activeBuilding?.boundTask === 'code', '开工的是接代码活的那栋');
  const workingStatus = await page.textContent('#status');
  check(workingStatus.includes('默认交付：莞尔'), '状态行写清默认交付对象');
  await page.waitForTimeout(700);
  await page.screenshot({ path: `${OUT}/02-working.png` });

  const delivering = await waitFor((s) => s.villager.mode === 'delivering', '活干完，她出门交付');
  check(delivering.villager.visible === true, '交付时人重新出现在画面里');
  check(delivering.tasks[0]?.status === 'done', '台账里这单已完成');
  check(
    delivering.workstations.some((w) => w.activity.kind === 'cooling'),
    '铺子进入冷却',
  );
  await page.screenshot({ path: `${OUT}/03-deliver.png` });

  const back = await waitFor((s) => s.villager.mode === 'idle', '她回到祭坛恢复闲逛');
  check(back.villager.taskId === null, '回到祭坛后手上没有单');

  // 两个角色各接一单：第一单给主角色，第二单给空闲的助手。
  await page.click('button[data-kind="research"]');
  await waitFor((s) => s.villager.mode === 'working', '第二单能派出去');
  await page.click('button[data-kind="writing"]');
  const second = await waitFor(
    (s) => s.villagers[1]?.taskId !== null,
    '第二个角色接下了另一单',
  );
  check(second.villagers[0]?.taskId !== second.villagers[1]?.taskId, '派活不串任务');

  // 两个人都被占着时再派一单：进等待队列；回执与状态行必须说出来。
  await page.fill('#utterance', '再整理一份便笺');
  await page.click('button[type="submit"]');
  await page.waitForFunction(
    () => (document.querySelector('#reply')?.textContent ?? '').includes('已排队'),
    null,
    { timeout: 5000 },
  );
  check(true, '两人都忙时新神谕进排队，回执写明第几位');
  const queuedView = await state();
  check(queuedView.tasks.length === 3, '排队的单没有立刻进台账');
  check(queuedView.pending.length === 1, '排队单在快照 pending 里可见');
  await page.waitForFunction(
    () => (document.querySelector('#status')?.textContent ?? '').includes('排队 1 条'),
    null,
    { timeout: 5000 },
  );
  check(true, '状态行显示排队数');

  // 「现实完成」可指定角色（id 或名字）且只对沙盒单生效。
  await page.evaluate(() => window.Oracle.finish('v0'));
  const manual = await waitFor((s) => s.villager.mode === 'delivering', '人工交付也能结束这单');
  check(manual.tasks[1]?.status === 'done', '人工交付对沙盒单生效');
  const manualReply = await page.textContent('#reply');
  check(
    manualReply.includes('离线 Mock') && manualReply.includes('不计入台账'),
    '沙盒单人工交付回执标明离线 Mock、不计入台账',
  );
  check(manualReply.includes('莞尔'), '回执写清收的是谁的单');
  check(
    manual.deliveries.ledger === 0 && manual.deliveries.mock >= 2,
    '交付统计把 Mock 单独计，不混进台账',
  );
  check(manual.deliveriesByVillager[0]?.mock >= 1, '交付归属到指定角色');

  // 按名字指定另一个人：只收阿雾那单。
  await waitFor((s) => s.villagers[1]?.mode === 'working', '助手也在干活', 20000);
  await page.evaluate(() => window.Oracle.finish('阿雾'));
  await page.waitForFunction(
    () => (document.querySelector('#reply')?.textContent ?? '').includes('阿雾'),
    null,
    { timeout: 5000 },
  );
  check(true, '按名字指定交付对象');
  const perWorker = await state();
  check(
    perWorker.deliveriesByVillager.every((item) => item.mock >= 1),
    '两个角色都各自记了交付',
  );

  // 主角色空下来后，排队单自动开工，不需要再点任何按钮。
  const queuedStarted = await waitFor(
    (s) => s.tasks.length === 4 && s.tasks[3]?.status === 'in_progress',
    '排队的神谕在上一单交付后自动开工',
    20000,
  );
  check(queuedStarted.tasks[3]?.summary === '再整理一份便笺', '自动开工的是排队的那条神谕');
  check(queuedStarted.pending.length === 0, '出队后 pending 清空');

  // 未知角色：明确拒绝，不碰任何人的活。
  await page.evaluate(() => window.Oracle.finish('v9'));
  await page.waitForFunction(
    () => (document.querySelector('#reply')?.textContent ?? '').includes('没有叫'),
    null,
    { timeout: 5000 },
  );
  check(true, '未知角色的交付被明确拒绝');

  // 把剩下的活逐单收尾，最后两个人都回到日常。
  for (let i = 0; i < 20; i += 1) {
    const snapshot = await state();
    if (
      snapshot.tasks.every((task) => task.status === 'done') &&
      snapshot.villagers.every((villager) => villager.mode === 'idle')
    ) {
      break;
    }
    await page.click('#finish');
    await page.waitForTimeout(500);
  }
  const settled = await state();
  check(settled.tasks.every((task) => task.status === 'done'), '所有单都收尾');
  const paperByWorker = await page.evaluate(() => window.Oracle.report());
  const workerLine = paperByWorker.lines.find((line) => line.includes('交付')) ?? '';
  check(
    workerLine.includes('莞尔交付') && workerLine.includes('阿雾交付'),
    '日报按角色分账，两个人都写清交付数',
  );
  await waitFor(
    (s) => s.villagers.every((item) => item.mode === 'idle'),
    '两个人都回到日常',
  );

  const routine = await waitFor(
    (s) => typeof s.villager.routine === 'string' && s.villager.routine.length > 0,
    '日常作息给出了一句可读的状态',
    15000,
  );
  check(routine.villager.mode === 'idle', '日常状态不是被神谕打断的');

  await page.click('button[data-kind="quick"]');
  const quick = await waitFor((s) => s.villager.mode === 'working', '短活当场开始');
  check(
    quick.workstations.every((w) => w.activity.kind !== 'operating'),
    '短活没占用任何铺子',
  );
  await waitFor((s) => s.villager.mode === 'idle', '短活自己结束');

  // 世界法则：下雨 → 她躲雨 → 雨停。
  await page.click('#rain');
  const raining = await waitFor((s) => s.weather === 'rain', '神谕让世界下雨了');
  check(raining.villager.umbrella === true, '下雨时她撑起伞');
  check(raining.villager.mode === 'sheltering', '户外的她当场跑去躲雨');
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/04-rain.png` });
  await waitFor((s) => s.villager.mode === 'idle', '跑到屋檐下停住');
  await page.click('#clear');
  await waitFor((s) => s.weather === 'clear', '神谕让天放晴');

  // 造物主：扩建一栋，施工期间不接活，落成后进世界。
  const beforeBuild = await state();
  await page.click('button[data-build="workshop_plot"]');
  const building = await waitFor(
    (s) => s.buildings.length === beforeBuild.buildings.length + 1,
    '造物主落下一栋新楼',
  );
  check(building.constructions.length === 1, '新楼处于施工中');
  check(building.roads.length > beforeBuild.roads.length, '新楼自动铺出了一条路');
  await page.screenshot({ path: `${OUT}/05-build.png` });
  await waitFor((s) => s.constructions.length === 0, '施工结束，新楼落成');

  for (let i = 0; i < 2; i += 1) {
    await page.click('button[data-kind="quick"]');
    await waitFor((s) => s.villager.mode === 'working', `第 ${i + 1} 单短活开始`);
    await waitFor((s) => s.villager.mode === 'idle', `第 ${i + 1} 单短活结束`);
  }
  const withProp = await waitFor((s) => s.props.length > 0, '神恩攒够，世界多了一件东西');
  // 队列让交付多了一单，里程碑可能提前落到第 2 次：只要求确实记上了。
  check(withProp.faith.milestones >= 1, '里程碑记上了');
  await page.waitForTimeout(400);
  await page.screenshot({ path: `${OUT}/06-faith.png` });

  // 日报是纸面，读得到，且写的是真发生过的事。
  const paper = await page.evaluate(() => window.Oracle.report());
  check(paper.lines.some((line) => line.includes('交付')), '日报写了交付数');
  const paperText = await page.textContent('#paper');
  check(paperText.includes(paper.headline), '报纸把标题渲染到了纸面上');
  check(paperText.includes('神恩') || paperText.includes('路灯'), '报纸写了落地的东西');

  // 桥接：外部消息进世界，结果能从快照里读回来。
  const bridged = await page.evaluate(async () => {
    const before = window.Oracle.view().tasks.length;
    window.Oracle.feed({
      type: 'task.assigned',
      taskId: 'ext-1',
      taskKind: 'quick',
      summary: '外部派来的小事',
    });
    await new Promise((resolve) => setTimeout(resolve, 500));
    return { before, after: window.Oracle.view().tasks.length };
  });
  check(bridged.after === bridged.before + 1, '外部 Agent 的消息进了同一条命令队列');

  // 存档：写单槽 → 刷新 → 世界恢复（BRIEF 验收目标 4）。
  const beforeSave = await state();
  const saveResult = await page.evaluate(() => window.Oracle.saveNow());
  check(saveResult.ok === true, '手动存档写入单槽');
  const savedRaw = await page.evaluate(() =>
    localStorage.getItem('xiaoxi.pixel-world.save.v1'),
  );
  check(
    typeof savedRaw === 'string' && savedRaw.includes('"schemaVersion":1'),
    '单槽里是 v1 JSON',
  );

  await page.reload({ waitUntil: 'load' });
  await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: 15000 });
  const restored = await state();
  check(restored.buildings.length === beforeSave.buildings.length, '刷新后建筑数量恢复');
  check(restored.roads.length === beforeSave.roads.length, '刷新后道路恢复');
  check(restored.faith.milestones === beforeSave.faith.milestones, '刷新后神恩恢复');
  check(
    JSON.stringify(restored.deliveriesByVillager) ===
      JSON.stringify(beforeSave.deliveriesByVillager),
    '刷新后按角色交付统计恢复',
  );
  check(restored.ledger.reconciled === false, '读档后标记未对账');
  await page.waitForFunction(
    () => (document.querySelector('#status')?.textContent ?? '').includes('读档未对账'),
    null,
    { timeout: 5000 },
  );
  check(true, '状态行标出读档未对账');

  // 清档 → 刷新 → 回到新世界。
  const cleared = await page.evaluate(() => window.Oracle.clearSave());
  check(cleared.ok === true, '清档入口可用');
  check(
    (await page.evaluate(() => localStorage.getItem('xiaoxi.pixel-world.save.v1'))) === null,
    '清档后单槽为空',
  );
  await page.reload({ waitUntil: 'load' });
  await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: 15000 });
  const freshWorld = await state();
  check(
    freshWorld.buildings.length === 3 && freshWorld.faith.milestones === 0,
    '清档后从新世界开始',
  );
}

try {
  await mkdir(OUT, { recursive: true });
  await page.goto(
    LEDGER ? `${URL_BASE}?ledger=on` : `${URL_BASE}?ledger=off`,
    { waitUntil: 'load' },
  );
  await page.waitForFunction(() => window.Oracle !== undefined, null, { timeout: 15000 });
  check(errors.length === 0, '页面加载无 JS 报错');

  const opening = await state();
  check(opening.buildings.length === 3, '世界里摆了三栋铺子');
  check(opening.villager.mode === 'idle', '开局她在闲逛');

  if (LEDGER) {
    await runLedgerSuite();
    report('台账模式');
  } else {
    await runSandboxSuite();
    report('神谕闭环');
  }

  check(errors.length === 0, '全程无 JS 报错');
} catch (error) {
  console.error(`\n失败：${error.message}`);
  console.error(`已完成 ${checks.length} 项：`);
  for (const label of checks) console.error(`  ok  ${label}`);
  if (errors.length > 0) console.error(`页面报错：\n${errors.join('\n')}`);
  process.exitCode = 1;
} finally {
  await browser.close();
}
