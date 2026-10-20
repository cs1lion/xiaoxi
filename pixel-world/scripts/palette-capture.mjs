#!/usr/bin/env node
/** 改色对照的截图端：**只截图 + 断言**，不启动 Vite。
 *
 * 职责切分（重要）
 * ----------------
 * 这个文件用 **ego-browser nodejs** 跑。ego 的嵌入式 Node 运行时里，
 * import vite 的 createServer 会直接断开（NodeRuntime disconnected, exit 1）。
 * 所以本脚本**绝不 import vite**：服务必须先由 palette-serve.mjs（普通 node）起好，
 * 再把 URL 传进来。这样脚本既能在 ego 里跑，也能对着任何已有服务跑。
 *
 * 用法
 * ----
 *   1) 普通 node 起服务并让它常驻（另开一个终端）：
 *        $env:PALETTE_OVERRIDE="stone:#3e433f"; node scripts/palette-serve.mjs
 *   2) ego 里跑本脚本：
 *        ego-browser nodejs -e "await import('file:///<绝对路径>/scripts/palette-capture.mjs')"
 *
 * 环境变量
 * --------
 *   BASE_URL          基线地址，默认 http://127.0.0.1:5189/
 *   OVERRIDE_URL      覆盖地址；给了才拍第二张，默认 http://127.0.0.1:5190/
 *   PALETTE_OVERRIDE  覆盖串，用来做断言（必须与起服务时一致），例如 "stone:#3e433f"
 *   PALETTE_LABEL     覆盖图文件名后缀，默认取覆盖串第一个 token 名，例如 "STONE"
 *   PALETTE_SETTLE_MS 打开页面后等多少毫秒再拍，默认 1200
 *
 * 产物：eval/screens/palette-verify-{BASE|<LABEL>}.png（gitignored）
 */

import { mkdirSync } from 'node:fs';
import { parseOverrides } from '../vite.palette.mjs';

const ROOT = new URL('..', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const SHOT_DIR = `${ROOT}eval/screens`;
const BASE_URL = (process.env.BASE_URL ?? 'http://127.0.0.1:5189/').trim();
const OVERRIDE_URL = (process.env.OVERRIDE_URL ?? 'http://127.0.0.1:5190/').trim();
const OVERRIDE = (process.env.PALETTE_OVERRIDE ?? '').trim();
const SETTLE_MS = Number(process.env.PALETTE_SETTLE_MS ?? 1200);

/** 标签：默认取覆盖串第一个 token 名，文件名就能看出改了什么。 */
function defaultLabel(override) {
  if (override === '') return 'OVR';
  const first = override.split(',')[0] ?? '';
  return (first.split(':')[0] ?? 'OVR').trim().toUpperCase();
}
const LABEL = (process.env.PALETTE_LABEL ?? defaultLabel(OVERRIDE)).trim();
const overrides = parseOverrides(OVERRIDE);

/** 页内把实际渲染用的色值读出来。
 *
 * 为什么读模块而不是采样 canvas：WebGL 读回需要 preserveDrawingBuffer，拿不到。
 * 页内动态 import 拿到的是**同一份模块实例**，就是渲染管线正在用的那份，
 * 所以它能证明覆盖真的生效——而不是「两张图碰巧一样」。
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

/** 拍一张：清档 → 打开 → 等就绪 → 断言色值 → 截图。 */
async function capture(task, label, url, expectOverride) {
  const page = task.page(label.toLowerCase());
  await page.cdp('Storage.clearDataForOrigin', {
    origin: new URL(url).origin,
    storageTypes: 'local_storage,session_storage',
  });
  await page.goto(url, { timeout: 30_000 });
  await page.waitForFunction(() => window.Oracle !== undefined, undefined, { timeout: 20_000 });
  // 等世界跑起来，避免拍到首帧空场。
  await new Promise((r) => setTimeout(r, SETTLE_MS));

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
  await page.close();
}

/* ---------------- 主流程 ---------------- */

const task = await taskSpace(`palette-capture ${LABEL}`);

try {
  mkdirSync(SHOT_DIR, { recursive: true });
  console.log(`改色对照：基线 ${BASE_URL}${overrides.size > 0 ? '  覆盖 ' + OVERRIDE_URL : ''}`);
  for (const [token, hex] of overrides) console.log(`  ${token} → ${hex}`);

  await capture(task, 'BASE', BASE_URL, false);
  if (overrides.size > 0) {
    await capture(task, LABEL, OVERRIDE_URL, true);
    console.log(`\n完成。两张图同视角，可直接对比。`);
  } else {
    console.log(`\n完成。只拍了一张基线（未给 PALETTE_OVERRIDE）。`);
  }
} finally {
  await task.finish({ keep: [] }).catch(() => {});
}
