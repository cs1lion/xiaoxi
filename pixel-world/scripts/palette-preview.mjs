/** 改色预览：同一视角拍「改前 / 改后」两张图，用真实渲染裁决配色，不信模型模拟。
 *
 * 为什么这么做
 * --------------
 * `WORLD` 是模块级常量，而且 `TERRAIN_COLORS` / `TERRAIN_SPECK` / 蓝图调色板都在
 * 模块初始化那一刻就把值复制走了。所以运行时在页面里改 `WORLD.stone` 是无效的——
 * 已经读过的表全是旧值。真正能生效的注入点是**模块求值之前**：`vite.palette.mjs`
 * 的 load 钩子拦截 `palette-data.ts`，把 `stone: 0x3a3f3b` 重写成别的值。
 * 磁盘源码零改动。
 *
 * 这个脚本负责编排：起两个 dev server（基线 5189 / 覆盖 5190），各拍一张同视角截图。
 *
 * 用法
 * ----
 *   ego-browser nodejs -e "await import('file:///<绝对路径>/scripts/palette-preview.mjs')"
 *
 * 环境变量（都可选）：
 *   PALETTE_OVERRIDE     覆盖，例如 "stone:#3e433f" 或 "stone:#3e433f,plaza:#4d5244"
 *   PALETTE_LABEL        输出文件后缀，默认由覆盖自动生成，例如 "A"
 *   PALETTE_BASE_PORT    基线段口，默认 5189
 *   PALETTE_OVR_PORT     覆盖段口，默认 5190
 *
 * 产物：eval/screens/palette-verify-{BASE|A}.png（gitignored）
 */

import { createServer } from 'vite';
import { mkdirSync } from 'node:fs';
import { parseOverrides, paletteOverridePlugin } from '../vite.palette.mjs';

const ROOT = new URL('..', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const SHOT_DIR = `${ROOT}eval/screens`;
const OVERRIDE = (process.env.PALETTE_OVERRIDE ?? '').trim();
const BASE_PORT = Number(process.env.PALETTE_BASE_PORT ?? 5189);
const OVR_PORT = Number(process.env.PALETTE_OVR_PORT ?? 5190);

/** 标签：默认取覆盖串里第一个 token 名，方便看文件名就知道改了什么。 */
function defaultLabel(override) {
  if (override === '') return 'BASE';
  const first = override.split(',')[0] ?? '';
  return (first.split(':')[0] ?? 'OVR').trim().toUpperCase();
}

const LABEL = (process.env.PALETTE_LABEL ?? defaultLabel(OVERRIDE)).trim();

/* 校验覆盖串：格式错就在这里停，不要等浏览器起来才发现。 */
const overrides = parseOverrides(OVERRIDE);
console.log(`改色预览：${overrides.size} 个覆盖`);
for (const [token, hex] of overrides) console.log(`  ${token} → ${hex}`);

/** 起一个 dev server，返回 { url, close }。
 *
 * 覆盖通过**显式传参**给插件，不靠 process.env：同一个进程里连续建两个 server 时，
 * vite.palette.mjs 已被 import 缓存，它模块顶层的 env 读取不会重跑。
 */
async function startServer(port, overrides) {
  const server = await createServer({
    root: ROOT,
    // 不用 configFile：那个文件的 plugins 在模块级就已经定死了。这里直接建插件实例。
    configFile: false,
    base: './',
    plugins: [paletteOverridePlugin(overrides)],
    server: { port, strictPort: true },
    logLevel: 'warn',
  });
  await server.listen();
  const url = `http://127.0.0.1:${port}/`;
  console.log(`  dev server 就绪：${url}${overrides.size === 0 ? '（基线）' : '（覆盖）'}`);
  return { url, close: () => server.close() };
}

/* ---------------- 主流程 ---------------- */

const task = await taskSpace(`palette-preview ${LABEL}`);
const bases = [];

try {
  mkdirSync(SHOT_DIR, { recursive: true });

  // 基线：空覆盖（插件原样返回源码）
  const base = await startServer(BASE_PORT, new Map());
  bases.push(base);

  // 覆盖：同一实现，只是多了改写
  const ovr = await startServer(OVR_PORT, overrides);
  bases.push(ovr);

  const shots = [
    { label: 'BASE', url: base.url },
    { label: LABEL, url: ovr.url },
  ];

  for (const { label, url } of shots) {
    const page = task.page(label.toLowerCase());
    // 清档：不同 origin（端口不同 = origin 不同）本来就各自独立，但稳妥起见还是清一次。
    await page.cdp('Storage.clearDataForOrigin', {
      origin: new URL(url).origin,
      storageTypes: 'local_storage,session_storage',
    });
    await page.goto(url, { timeout: 30_000 });
    await page.waitForFunction(() => window.Oracle !== undefined, undefined, { timeout: 20_000 });
    // 等世界跑起来、粒子/动画到位再拍，避免拍到首帧空场。
    await new Promise((r) => setTimeout(r, 1200));

    // 不只看截图：从页面里把**实际渲染用的色值**读出来，证明覆盖真的进了渲染管线。
    // 直接从 canvas 采样不可靠（WebGL 读回要 preserveDrawingBuffer），所以读模块导出：
    // 页面已经把 palette-data.ts 求值成真实模块，动态 import 拿到的是同一份实例。
    const probe = await page.evaluate(async () => {
      const mod = await import('/src/world/palette-data.ts');
      const canvas = document.querySelector('#app canvas');
      return {
        world: { ...mod.WORLD },
        canvasWidth: canvas?.width ?? 0,
        canvasHeight: canvas?.height ?? 0,
      };
    });
    const hexOf = (n) => '#' + Number(n).toString(16).padStart(6, '0');
    console.log(`  ${label}: canvas ${probe.canvasWidth}x${probe.canvasHeight}`);
    for (const [token, value] of overrides) {
      const actual = hexOf(probe.world[token]);
      const wanted = value.replace('0x', '#');
      const matches = actual.toLowerCase() === wanted.toLowerCase();
      if (label === 'BASE') {
        // 基线必须**不是**覆盖值，否则两张图一样，等于没验。
        if (matches) throw new Error(`基线竟然已经是覆盖值 ${actual}，两张图会一样，无法对比`);
        console.log(`    ${token}: 基线实际 ${actual}（与覆盖值 ${wanted} 不同 ✓）`);
      } else {
        console.log(`    ${token}: 页面实际 ${actual}  期望 ${wanted}  ${matches ? '✓ 生效' : '✗ 未生效'}`);
        if (!matches) throw new Error(`${label} 的 ${token} 覆盖没进渲染：实际 ${actual}，期望 ${wanted}`);
      }
    }

    const file = `palette-verify-${label}.png`;
    await page.screenshot({ path: `${SHOT_DIR}/${file}` });
    console.log(`  截图：${SHOT_DIR}/${file}`);
    await page.close();
  }

  console.log('\n完成。两张图同视角，可直接对比。');
} finally {
  for (const s of bases) await s.close().catch(() => {});
  await task.finish({ keep: [] });
}
