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

import { mkdirSync, writeFileSync } from 'node:fs';
import { inflateSync } from 'node:zlib';
import { parseOverrides } from '../vite.palette.mjs';

const ROOT = new URL('..', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const SHOT_DIR = `${ROOT}eval/screens`;

/** 文件名安全的后缀：去掉不合法字符，只留大写字母数字与短横。 */
function safeLabel(text) {
  const cleaned = String(text).toUpperCase().replace(/[^A-Z0-9-]/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '');
  return cleaned === '' ? 'OVR' : cleaned;
}

/** 判断 PNG 是否「全白/全黑」这类空白图。
 *
 * 为什么需要：canvas.toDataURL() **在某些时序下**会静默返回全黑图——
 * 不抛错、字节数也正常。只看 length 判不出来，必须解到像素。
 *
 * 注意：这不是「toDataURL 不可用」。同一个 API 在别的时序下能拿到真实内容：
 * UIUX 的 round-12 图就是 toDataURL 拍的，480×320、blackPct 0.00%、68 种颜色。
 * 最可能的原因是 Phaser 的 WebGL 默认 preserveDrawingBuffer=false，
 * 在 rAF 回调之外读时 drawing buffer 已被清空 → 全黑；紧跟渲染读则正常。
 * 所以结论是「需要守卫」，不是「这条路走不通」。
 *
 * 这里不做通用 PNG 解码（避免引依赖），只按 zlib 解压后统计 IDAT 里
 * 是否存在两个不同的字节值——真正的游戏画面必然有丰富变化，
 * 全黑/全白图的解压数据几乎全是同一个值。
 */
function isLikelyBlank(pngBuf) {
  try {
    let pos = 8;
    let width = 0, height = 0, colorType = 2;
    const idat = [];
    while (pos + 8 <= pngBuf.length) {
      const len = pngBuf.readUInt32BE(pos);
      const type = pngBuf.toString("ascii", pos + 4, pos + 8);
      const data = pngBuf.subarray(pos + 8, pos + 8 + len);
      if (type === "IHDR") {
        width = data.readUInt32BE(0);
        height = data.readUInt32BE(4);
        colorType = data[9] ?? 2;
      }
      else if (type === "IDAT") idat.push(data);
      else if (type === "IEND") break;
      pos += 12 + len;
    }
    if (idat.length === 0) return true;
    if (colorType === 0 || colorType === 2 || colorType === 4 || colorType === 6) {
      // 可解
    } else {
      return false;
    }
    const raw = inflateSync(Buffer.concat(idat));
    // 每行布局是 [1 字节 filter][stride 字节像素]，所以 raw.length = height * (stride + 1)。
    // stride 必须由**实际字节数反推**，不能自己编公式：早先写成
    // Math.floor((raw.length - height) / height) 会少算 1 字节，导致每行错位一字节、
    // 采样到的基本是 filter 字节（全 0），于是把「100% 纯黑」误判成「有变化」。
    // 实测代价：两张 native 图 100% #000000 却通过了校验，被当成有效图写盘。
    const stride = Math.floor(raw.length / height) - 1;
    if (stride <= 0) return true;
    const seen = new Set();
    for (let y = 0; y < height; y += 1) {
      const base = y * (stride + 1) + 1;
      for (let x = 0; x < stride; x += 1) {
        const v = raw[base + x];
        if (v === undefined) break;
        seen.add(v);
        if (seen.size > 8) return false;
      }
    }
    return seen.size <= 2;
  } catch {
    // 解不出来就当作有效，交给下游判断
    return false;
  }
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
  // 必须裁到 canvas 区域再截，不能用 page.screenshot() 的整页/视口截图。
  //
  // 为什么：page.screenshot() 拍的是整个视口，画面里混着 header、状态行、
  // 神谕输入区与纸面日报。任何拿这两张图做像素统计的人（明度分布、色数覆盖、
  // 众数亮度带）都会把页面 UI 的像素算进去，得到与本文件目的不符的数字。
  // 实测证据：旧实现产出的 palette-verify-*.png 是 1126×765（整页）。
  //
  // 产出**两张**，尺寸口径不同，绝对不要混用：
  //
  //   palette-verify-<label>.png
  //     裁到 canvas 区域（CDP clip）。**产出尺寸 = canvas CSS 尺寸 × 设备像素比**。
  //     本机实测：CSS 960×640、devicePixelRatio 2.25 → 产出 **2160×1440**。
  //     用途：人眼看整体观感、对比材质与配色。
  //     注意它既不是 480×320 也不是 960×640；拿它做像素统计会把 DPR 放大算进去。
  //
  //   palette-verify-<label>-native.png
  //     canvas.toDataURL() 的原始 backing = **480×320**（Phaser 逻辑分辨率）。
  //     用途：像素统计（明度分布、色数、众数亮度带）。一个像素 = 一个游戏原生像素，
  //     没有缩放插值，也不含任何页面 UI 像素；与 round-12 回读口径一致。
  //
  // 三个尺寸记清：backing 480×320 / CSS 960×640 / clip 产出 2160×1440（本机 DPR 2.25）。
  // DPR 随机器与窗口大小变，别把这三个数写死进任何断言，用实测回读。
  const measured = await page.evaluate(() => {
    const c = document.querySelector('#app canvas');
    const rect = c.getBoundingClientRect();
    return {
      x: Math.round(rect.left),
      y: Math.round(rect.top),
      width: Math.round(rect.width),
      height: Math.round(rect.height),
      nativeWidth: c.width,
      nativeHeight: c.height,
      dataUrl: c.toDataURL('image/png'),
    };
  });
  // 不加 captureBeyondViewport：canvas 一定在视口内，加了反而可能改变布局。
  const shot = await page.cdp('Page.captureScreenshot', {
    format: 'png',
    clip: { x: measured.x, y: measured.y, width: measured.width, height: measured.height, scale: 1 },
  });
  writeFileSync(`${SHOT_DIR}/${file}`, Buffer.from(shot.data, 'base64'));

  // native 回读有一个已知陷阱：canvas.toDataURL() **在某些时序下**会返回一张
  // **全黑**图，而且它不会报错——字节数甚至可能是正常的。
  //
  // 实测：本工具跑时拿到 100% #000000，而同一时刻 CDP 截图完全正常；
  // 同一份代码在 UIUX 那次（round-12）却拿到了真实内容（blackPct 0.00%、68 色）。
  // 差异指向读取时序而非 API 能力：Phaser 的 WebGL 默认 preserveDrawingBuffer=false，
  // 在 rAF 回调之外读，drawing buffer 可能已被清空。
  //
  // 结论：**不是 toDataURL 不可用，是它需要守卫**。所以这里不删这条路，
  // 而是校验非空——否则下游拿它算像素统计会得到「100% 落在 band 0」这种
  // 假数据，且全程不报错（本项目已踩过一次）。
  const nativeFile = `palette-verify-${label}-native.png`;
  const base64 = String(measured.dataUrl).replace(/^data:image\/png;base64,/, "");
  const nativeBuf = Buffer.from(base64, "base64");
  const nativeBlank = isLikelyBlank(nativeBuf);
  if (nativeBlank) {
    console.warn(`  原生：跳过（toDataURL 本次返回空白图，属读取时序问题；以 CSS 截图为准，下次可重试）`);
  } else {
    writeFileSync(`${SHOT_DIR}/${nativeFile}`, nativeBuf);
  }

  console.log(`  截图：${SHOT_DIR}/${file}  (CSS ${measured.width}x${measured.height})`);
  if (!nativeBlank) console.log(`  原生：${SHOT_DIR}/${nativeFile}  (canvas ${measured.nativeWidth}x${measured.nativeHeight})`);
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
