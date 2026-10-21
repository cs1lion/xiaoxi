/**
 * 改色预览用的 Vite 配置：运行时把 `palette-data.ts` 的 token 值换成别的 hex。
 *
 * 为什么不能在页面里改 WORLD
 * --------------------------
 * `WORLD` 是模块级常量，而且它**先被读成快照**再往下游传：`TERRAIN_COLORS`、
 * `TERRAIN_SPECK`、以及 `blueprints.ts` 解析 prefab 时把 token 名解析成的数字，
 * 全都在模块初始化那一刻就复制了值。等页面跑起来再去改 `WORLD.stone`，
 * 已经读过的表全是旧值 —— 改了等于没改。
 *
 * 所以在**模块求值之前**换掉源文本：load 钩子拦截 `palette-data.ts`，把
 * `stone: 0x3a3f3b` 改写成 `stone: 0x3e433f`，其余一律不动。
 * 磁盘上的源码文件零改动，是一次性的运行时替换。
 *
 * 用法：不要直接跑这个配置文件，用配套的两个脚本
 * ------------------------------------------------
 * 起服务和截图必须分开两个进程——ego 的嵌入式 Node 运行时里跑 Vite 的
 * createServer 会直接断开（NodeRuntime disconnected, exit 1）。所以：
 *
 *   1) 普通 node 起服务：  node scripts/palette-serve.mjs
 *   2) ego 里截图+断言：   ego-browser nodejs -e "await import('file:///.../scripts/palette-capture.mjs')"
 *
 * 覆盖串经 PALETTE_OVERRIDE 传给 serve，capture 用同一个串做断言。
 * 多个覆盖用逗号分隔：`"stone:#3e433f,grass:#2d3b2e"`。
 *
 * 端口：基线 5189 / 覆盖 5190，刻意避开常规 harness 的 5188，可以和它同时开着。
 * 直接跑 `vite --config vite.palette.mjs` 也能用（只起覆盖这一个服务，端口取 PALETTE_PORT）。
 */

import { readFileSync } from 'node:fs';

/** 单独跑时监听这个端口；可用 PALETTE_PORT 覆盖（palette-serve.mjs 会并行起两个）。 */
const PORT = Number(process.env.PALETTE_PORT ?? 5189);

/** 解析 `token:#rrggbb[,...]`。格式不合规就立刻抛，不吃沉默的错误。 */
export function parseOverrides(raw) {
  const text = (raw ?? '').trim();
  const out = new Map();
  if (text === '') return out;
  for (const item of text.split(',')) {
    const piece = item.trim();
    if (piece === '') continue;
    const matched = /^([A-Za-z][A-Za-z0-9]*)\s*:\s*#?([0-9a-fA-F]{6})$/.exec(piece);
    if (matched === null) {
      throw new Error(`PALETTE_OVERRIDE 里这一项看不懂：「${piece}」。期望 token:#rrggbb，例如 stone:#3e433f`);
    }
    out.set(matched[1], `0x${matched[2].toLowerCase()}`);
  }
  return out;
}

/** 把覆盖写进 palette-data.ts 的源码文本；只替换确有其名的 token。 */
export function applyOverrides(source, overrides) {
  let out = source;
  for (const [token, hex] of overrides) {
    const pattern = new RegExp(`(\\b${token}\\s*:\\s*)0x[0-9a-fA-F]{6}\\b`);
    if (!pattern.test(out)) {
      throw new Error(`调色板里没有叫 ${token} 的 token（或它不是一个 0xrrggbb 字面量）`);
    }
    out = out.replace(pattern, `$1${hex}`);
  }
  return out;
}

export function paletteOverridePlugin(overrides) {
  return {
    name: 'palette-override',
    enforce: 'pre',
    load(id) {
      if (!/[\\/]src[\\/]world[\\/]palette-data\.ts$/.test(id)) return null;
      const source = readFileSync(id, 'utf8');
      const next = applyOverrides(source, overrides);
      if (overrides.size > 0) {
        const list = [...overrides].map(([token, hex]) => `${token}=${hex}`).join(' ');
        console.log(`[palette-override] 已替换 ${overrides.size} 个 token：${list}`);
      }
      return next;
    },
  };
}

/**
 * 独立跑这个配置时（`--config vite.palette.mjs`）从环境变量取覆盖。
 *
 * 注意：这是个**模块级**读取，同进程内第二次 createServer 会复用已缓存的模块、
 * 拿不到新的 env 值。所以脚本化批量对照（palette-preview.mjs）不要走这条，
 * 而是直接 import `paletteOverridePlugin` 传参，两次各建一个实例。
 */
export default {
  base: './',
  plugins: [paletteOverridePlugin(parseOverrides(process.env.PALETTE_OVERRIDE))],
  server: { port: PORT, strictPort: true },
};
