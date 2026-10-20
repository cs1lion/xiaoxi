#!/usr/bin/env node
/** 改色对照的截图端：**只截图 + 断言**，不启动 Vite。
 *
 * 职责切分（重要）
 * ----------------
 * 这个文件用 **ego-browser nodejs** 跑。ego 的嵌入式 Node 运行时里，
 * import vite 的 createServer 会直接断开（NodeRuntime disconnected, exit 1）。
 * 所以本脚本**绝不 import vite**：服务必须先由 palette-serve.mjs（普通 node）起好。
 *
 * 配置怎么传进来（这一条踩过坑）
 * ------------------------------
 * ego 的 node 运行时**不继承父进程的环境变量、也不接受 argv**：
 * 实测 process.env 里看不到 PowerShell 设的变量，process.argv 只有 exe 自己一条。
 * 所以在 PowerShell 里 $env:PALETTE_OVERRIDE=... 再跑 ego 是**无效的**。
 * 唯一可靠的传参方式是把配置写进 -e 的脚本文本里，显式调 run(config)。
 *
 * 用法
 * ----
 *   1) 另开一个终端起服务（普通 node）：
 *        $env:PALETTE_OVERRIDE="stone:#3e433f"; node scripts/palette-serve.mjs
 *   2) ego 里跑本脚本。两种传参都支持：
 *
 *      (a) 直接调 run()，配置写在 -e 里（最稳，推荐）：
 *        ego-browser nodejs -e "const m = await import('file:///<绝对路径>/scripts/palette-capture.mjs'); await m.run({ override: 'stone:#3e433f' })"
 *
 *      (b) 写个入口文件，**在 import 之前**设 env（实测同样有效）：
 *        process.env.PALETTE_OVERRIDE = 'stone:#3e433f';
 *        await import('file:///<绝对路径>/scripts/palette-capture.mjs');
 *
 *      config 字段（都可选，优先级高于同名环境变量）：
 *        override   覆盖串，要与起服务时一致，例如 "stone:#3e433f"；不给就只拍基线
 *        label      覆盖图文件名后缀，默认取覆盖串第一个 token 名的安全大写形式
 *        baseUrl    基线地址，默认 http://127.0.0.1:5189/
 *        ovrUrl     覆盖地址，默认 http://127.0.0.1:5190/
 *        settleMs   打开页面后等多少毫秒再拍，默认 1200
 *
 *      对应环境变量：PALETTE_OVERRIDE / PALETTE_LABEL / BASE_URL / OVERRIDE_URL / PALETTE_SETTLE_MS
 *
 * 产物：eval/screens/palette-verify-{BASE|<LABEL>}.png（gitignored）
 */

import { mkdirSync } from 'node:fs';
import { parseOverrides } from '../vite.palette.mjs';

const ROOT = new URL('..', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const SHOT_DIR = `${ROOT}eval/screens`;

/** 文件名安全的后缀：去掉不合法字符，只留大写字母数字与短横。 */
function safeLabel(text) {
  const cleaned = String(text).toUpperCase().replace(/[^A-Z0-9-]/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '');
  return cleaned === '' ? 'OVR' : cleaned;
}

/** 默认标签：取覆盖串第一个 token 名（stone → STONE）。 */
function defaultLabel(override) {
  if (override === '') return 'OVR';
  const first = override.split(',')[0] ?? '';
  return safeLabel((first.split(':')[0] ?? 'OVR').trim());
}

/** 页内把实际渲染用的色值读出来。
 *
 * 为什么读模块而不是采样 canvas：WebGL 读回需要 preserveDrawingBuffer，采不到像素。
 * 页内动态 import 拿到的是**同一份模块实例**，就是渲染管线正在用的那份，
 * 所以它能证明覆盖真的生效 —— 而不是「两张图碰巧一样」。
 */
async function probePalette(page) {
  return page.evaluate(async () => {
    const mod = await import('/src/world/palette-data.ts');
    const canvas = document.querySelector('#app canvas');
    return {
      world: { ...mod.WORLD },
      canvasWidth: canvas?.width ?? 0,
      canvasHeight: canvas?.height ?? 0,
    };
  });
}

const hexOf = (n) => '#' + Number(n).toString(16).padStart(6, '0');

/** 拍一张：清档 → 打开 → 等就绪 → 断言色值 → 截图。
 *
 * 页面复用：`task.page(label)` **只引用已存在的页面**，不会创建。新建 task space 里
 * 只有唯一一个 `p1`，传别的标签会抛 `page label not found`。两次截图本来就是串行
 * （先基线后覆盖），复用同一个 p1 各自 goto 不同 URL 即可，不需要 newPage。
 */
async function capture(page, label, url, overrides, expectOverride, settleMs) {
  await page.cdp('Storage.clearDataForOrigin', {
    origin: new URL(url).origin,
    storageTypes: 'local_storage,session_storage',
  });
  await page.goto(url, { timeout: 30_000 });
  await page.waitForFunction(() => window.Oracle !== undefined, undefined, { timeout: 20_000 });
  // 等世界跑起来，避免拍到首帧空场。
  await new Promise((r) => setTimeout(r, settleMs));

  const probe = await probePalette(page);
  console.log(`  ${label}: canvas ${probe.canvasWidth}x${probe.canvasHeight}`);

  for (const [token, value] of overrides) {
    const actual = hexOf(probe.world[token]);
    const wanted = value.replace('0x', '#');
    const matches = actual.toLowerCase() === wanted.toLowerCase();
    if (expectOverride) {
      if (!matches) {
        throw new Error(`${label} 的 ${token} 覆盖没进渲染：实际 ${actual}，期望 ${wanted}`);
      }
      console.log(`    ${token}: 页面实际 ${actual}  期望 ${wanted}  ✓ 生效`);
    } else {
      // 基线必须**不是**覆盖值，否则两张图一样，等于没验。
      if (matches) {
        throw new Error(`基线竟然已经是覆盖值 ${actual}，两张图会一样，无法对比`);
      }
      console.log(`    ${token}: 基线实际 ${actual}（与覆盖值 ${wanted} 不同 ✓）`);
    }
  }

  const file = `palette-verify-${label}.png`;
  await page.screenshot({ path: `${SHOT_DIR}/${file}` });
  console.log(`  截图：${SHOT_DIR}/${file}`);
  // 不 close：两次 capture 复用同一个 p1，关掉第二次就没页面了。
}

/** 主流程。config 见文件头；不传就是用默认端口和「只拍基线」。 */
export async function run(config = {}) {
  cancelAutoRun();
  // 两种传参都支持，config 优先、env 兜底。
  //
  // 为什么要 env 兜底：ego 不继承父 shell 的环境变量，但**在 import 之前**由入口脚本
  // 写进 process.env 是有效的（Chief 实测跑通）。所以想让「先设 env 再 import」这种
  // 写法也能用，就得读一次 env —— 否则那种入口写法的 PALETTE_OVERRIDE 会被静默忽略，
  // 只拍一张基线，让人以为覆盖没生效。
  const pick = (fromConfig, envName, fallback) =>
    (fromConfig ?? process.env[envName] ?? fallback).trim();
  const override = pick(config.override, 'PALETTE_OVERRIDE', '');
  const baseUrl = pick(config.baseUrl, 'BASE_URL', 'http://127.0.0.1:5189/');
  const ovrUrl = pick(config.ovrUrl, 'OVERRIDE_URL', 'http://127.0.0.1:5190/');
  const settleMs = Number(config.settleMs ?? process.env.PALETTE_SETTLE_MS ?? 1200);
  const label = pick(config.label, 'PALETTE_LABEL', defaultLabel(override));
  const overrides = parseOverrides(override);

  const task = await taskSpace(`palette-capture ${label}`);
  try {
    mkdirSync(SHOT_DIR, { recursive: true });
    console.log(`改色对照：基线 ${baseUrl}${overrides.size > 0 ? '  覆盖 ' + ovrUrl : ''}`);
    for (const [token, hex] of overrides) console.log(`  ${token} → ${hex}`);

    // 新建 task space 里已存在唯一页面 p1（ego 语义：task.page 只引用、不创建）。
    const page = task.page('p1');

    await capture(page, 'BASE', baseUrl, overrides, false, settleMs);
    if (overrides.size > 0) {
      await capture(page, label, ovrUrl, overrides, true, settleMs);
      console.log(`\n完成。两张图同视角，可直接对比。`);
    } else {
      console.log(`\n完成。只拍了一张基线（未给 override）。`);
    }
  } finally {
    await task.finish({ keep: [] }).catch(() => {});
  }
}

/*
 * 自动执行：被 import 就直接跑一遍 run()，但**可被显式调用取消**。
 *
 * 为什么需要：ego 里最自然的用法是「写个入口文件设好 env，再 import 这个脚本」
 * （ego 不继承父 shell 的 env，只能在 import 之前自己设）。如果 import 之后还要
 * 调用者记得再写一行 await run()，那种写法会静默什么都不做——看起来成功、实际没图。
 *
 * 为什么用 setTimeout 而不是直接 await run()：
 *   import 之后紧跟着的显式 m.run({...}) 会进微任务队列，早于这个宏任务触发；
 *   于是显式调用能先取消自动执行。否则两条路径会各跑一遍，第二遍还会因为
 *   页面已被用过而行为不确定。
 */
let autoRunTimer = setTimeout(() => {
  autoRunTimer = null;
  void run().catch((error) => {
    console.error('改色对照失败：' + (error && error.message ? error.message : String(error)));
    process.exitCode = 1;
  });
}, 0);

/** 取消待执行的自动运行（显式 run() 会先调它）。 */
function cancelAutoRun() {
  if (autoRunTimer !== null) {
    clearTimeout(autoRunTimer);
    autoRunTimer = null;
  }
}
