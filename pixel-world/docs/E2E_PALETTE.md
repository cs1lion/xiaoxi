# 调色板截图（E2E_PALETTE）

> 适用于 `pixel-world/scripts/capture-palette.mjs`（task-28 P2-2 起）。
> 目的只有两个：给「晴 / 雨 / 夜可读性」留可复查的图；让同一份图能被另一个人用同一条命令再跑出来。

## 1. 前置

1. 世界在跑：`cd pixel-world && npm run dev`（默认 `http://127.0.0.1:5188`）。
2. 浏览器在跑，**脚本不负责启动它**：DSH 沙箱禁止子进程命名管道，Playwright 自己
   `chromium.launch()` 会直接 `spawn EPERM`。所以先手动起一个带 CDP 端口的 Chrome：

   ```bash
   chrome-headless-shell --headless --remote-debugging-port=9333 --no-sandbox \
     --enable-unsafe-swiftshader --window-size=1010,1400 about:blank
   ```

   可执行文件在 `%LOCALAPPDATA%\ms-playwright\chromium_headless_shell-*/chrome-headless-shell-win64\`。
   想肉眼看窗口就去掉 `--headless`。
3. 依赖：仓库根目录已装 `@playwright/test`（`pixel-world` 自己不带这份依赖）。

## 2. 命令

```bash
cd pixel-world
node scripts/capture-palette.mjs
```

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| `CDP_URL` | `http://127.0.0.1:9333` | 已启动浏览器的 CDP 端点 |
| `PIXEL_WORLD_URL` | `http://127.0.0.1:5188/?ledger=off` | 世界页面；默认离线沙盒 |
| `PALETTE_SEED` | `20261019` | 雨滴与随机数的种子 |

端点连不上时脚本会直接退出并打印启动命令，不会自己拉起浏览器。

## 3. 产物

```
eval/screens/palette-sunny-100.png            100% 整页（含页头/DOM），给人看窗口实际效果
eval/screens/palette-sunny-canvas.png         画布 960×640
eval/screens/palette-sunny-native-480x320.png 原生帧 480×320
eval/screens/palette-sunny-400pct.png         400% 最近邻 1920×1280
（palette-rain-* / palette-night-* 同上）
```

`eval/` 已在 `.gitignore` 里，图不进仓库。

## 4. 为什么这样截

- **不用 `canvas.toDataURL()` / `drawImage()`**：Phaser AUTO 走 WebGL，没开
  `preserveDrawingBuffer`，取到的是全黑。全部走元素截图。
- **原生 480×320**：后备缓冲就是 480×320、CSS 显示 960×640。截图时把 `canvas` 的 CSS
  改回 480×320，元素截图即 1:1 出帧，没有重采样；400% 则把 CSS 改成 1920×1280、
  由 `image-rendering: pixelated` 做精确最近邻。脚本**不做 PNG 缩图**。
- **可复现**：`window.__RAIN_SEED__` 注入 `weather.ts` 的确定性种子（同种子同雨滴序列），
  `Math.random` 用同一 LCG 覆盖，rAF 用帧计数 + 固定时间戳（`1000/60`ms）推进，
  冻结在第 240 帧，雨在第 30 帧按下。截图前先开一张热身页，丢掉首帧
  着色器/合成器预热差异。
- **`localStorage.clear()`**：截图前清存档，避免读到旧色表留下的存档（读档会按 prefab
  重解颜色，但存档里若带旧字段仍可能干扰比对）。
- **夜景**：1.0 没有昼夜系统，脚本只是给画布加 `brightness(0.55)` 的只读试验，
  用来回答「24 色能不能撑起夜景」，不参与产品验收。

## 5. 自检

同一台机器、同一个种子连续跑两次，十二个产物（晴/雨/夜 × 100%/canvas/native-480x320/400pct）应逐字节一致。脚本每次都会打印每个产物的
sha256 前 16 位，直接对比即可；不一致先检查是不是有别的页面在动同一份 `localStorage`，
或者 `--window-size` 变了导致布局变化。
