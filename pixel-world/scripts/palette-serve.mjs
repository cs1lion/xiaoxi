#!/usr/bin/env node
/** 改色对照的服务端：**只起 dev server**，不碰浏览器。
 *
 * 为什么拆成两个脚本
 * --------------------
 * 改色验证要两件事：起服务 + 截图。它们必须跑在**不同的进程**里——
 * ego 的嵌入式 Node 运行时里 import vite 的 createServer 会直接断开
 * （NodeRuntime disconnected, exit 1）。所以：
 *
 *   palette-serve.mjs    用**普通 node** 跑，只负责起服务（本文件）
 *   palette-capture.mjs  用 **ego-browser nodejs** 跑，只负责截图 + 断言
 *
 * 两者通过 HTTP 解耦：capture 拿 BASE_URL / OVERRIDE_URL 干活，谁起的服务都行。
 *
 * 用法（普通 node，Windows PowerShell）
 * ------------------------------------
 *   node scripts/palette-serve.mjs
 *   $env:PALETTE_OVERRIDE="stone:#3e433f"; node scripts/palette-serve.mjs
 *
 * 环境变量
 * --------
 *   PALETTE_OVERRIDE  覆盖串，例如 "stone:#3e433f" 或 "stone:#3e433f,plaza:#4d5244"
 *                     留空 = 只起基线一个服务
 *   PALETTE_BASE_PORT 基线端口，默认 5189
 *   PALETTE_OVR_PORT  覆盖端口，默认 5190
 *   PALETTE_HOLD_MS   起完后保持多少毫秒再退出，默认 0（0 = 一直开着等 Ctrl+C）
 *                     自动化场景设成有限值，免得忘记关
 *
 * 输出：打印两个 URL，并给一行 PALETTE_URLS JSON 供脚本抓取
 */

import { createServer } from 'vite';
import { connect } from 'node:net';
import { parseOverrides, paletteOverridePlugin } from '../vite.palette.mjs';

const ROOT = new URL('..', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const OVERRIDE = (process.env.PALETTE_OVERRIDE ?? '').trim();
const BASE_PORT = Number(process.env.PALETTE_BASE_PORT ?? 5189);
const OVR_PORT = Number(process.env.PALETTE_OVR_PORT ?? 5190);
const HOLD_MS = Number(process.env.PALETTE_HOLD_MS ?? 0);

const overrides = parseOverrides(OVERRIDE);

/** 起一个 dev server。覆盖通过**显式传参**给插件，不靠 process.env。
 *
 * 原因：vite.palette.mjs 顶层的 env 读取是模块级的，同进程里第二次 createServer
 * 会复用已缓存的模块、拿不到新值——那会让「覆盖」和「基线」变成同一份，两张图一样。
 */
/**
 * 端口预检：端口被占时给一句能看的提示，而不是让 Vite 抛一屏堆栈。
 *
 * 为什么需要：这个脚本最常撞的问题就是上一次运行留下的进程还占着 5189/5190
 * （实测撞过多次，一个 node 进程同时占两个端口）。Vite 的报错只有
 * "Port 5189 is already in use"，不告诉你怎么办，也不告诉你是谁占着。
 */
async function assertPortFree(port) {
  const inUse = await new Promise((resolve) => {
    const socket = connect({ port, host: '127.0.0.1' }, () => {
      socket.destroy();
      resolve(true);
    });
    socket.on('error', () => resolve(false));
    socket.setTimeout(1500, () => {
      socket.destroy();
      resolve(false);
    });
  });
  if (inUse) {
    console.error(`\n[端口被占] ${port} 上已经有服务在监听。`);
    console.error('  多半是上一次 palette-serve.mjs 没退出。处理方式：');
    console.error(`    PowerShell:  Get-NetTCPConnection -LocalPort ${port} -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`);
    console.error(`    或者换端口:  $env:PALETTE_BASE_PORT=${port + 10}; $env:PALETTE_OVR_PORT=${port + 11}; node scripts/palette-serve.mjs`);
    console.error('  另一条路：如果那个服务就是要用的那份，直接把 URL 传给 palette-capture.mjs，本脚本不用起。\n');
    process.exit(1);
  }
}

async function startServer(port, ovr) {
  const server = await createServer({
    root: ROOT,
    configFile: false,
    base: './',
    plugins: [paletteOverridePlugin(ovr)],
    server: { port, strictPort: true },
    logLevel: 'warn',
  });
  await server.listen();
  return server;
}

const servers = [];
const urls = {};

try {
  // 起服务之前先确认端口空闲：撞端口时给能照做的提示，而不是 Vite 的堆栈。
  await assertPortFree(BASE_PORT);
  if (overrides.size > 0) await assertPortFree(OVR_PORT);

  // 基线：空覆盖（插件原样返回源码）
  servers.push(await startServer(BASE_PORT, new Map()));
  urls.BASE_URL = `http://127.0.0.1:${BASE_PORT}/`;
  console.log(`[基线]   ${urls.BASE_URL}`);

  // 覆盖：只有给了覆盖串才起第二个
  if (overrides.size > 0) {
    servers.push(await startServer(OVR_PORT, overrides));
    urls.OVERRIDE_URL = `http://127.0.0.1:${OVR_PORT}/`;
    console.log(`[覆盖]   ${urls.OVERRIDE_URL}`);
    const list = [...overrides].map(([t, h]) => `${t}=${h}`).join(' ');
    console.log(`         覆盖：${list}`);
  } else {
    console.log('[覆盖]   未指定 PALETTE_OVERRIDE，只起基线');
  }

  // 机器可读的一行，供脚本抓取
  console.log('\nPALETTE_URLS ' + JSON.stringify(urls));

  if (HOLD_MS > 0) {
    console.log(`保持 ${HOLD_MS}ms 后退出…`);
    await new Promise((r) => setTimeout(r, HOLD_MS));
  } else {
    console.log('服务已就绪，Ctrl+C 结束。');
    await new Promise((resolve) => {
      process.on('SIGINT', resolve);
      process.on('SIGTERM', resolve);
    });
  }
} finally {
  for (const s of servers) await s.close().catch(() => {});
  console.log('服务已关闭');
}
