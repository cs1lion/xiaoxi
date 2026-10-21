import { chromium } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";

// 用法：先起服务，再跑本脚本。同时截静默与开口两个镜头态。
//   BASE_URL=http://127.0.0.1:8010/ node scripts/capture-avatar-stage.mjs
// 产出：eval/almanac-20261017/avatar/{desktop,mobile}-{idle,speaking}.png + metrics.json
const BASE = process.env.BASE_URL || "http://127.0.0.1:8010/";
const out = "eval/almanac-20261017/avatar";
await mkdir(out, { recursive: true });
const browser = await chromium.launch();

async function shot(width, height, name, speaking) {
  const page = await browser.newPage({ viewport: { width, height } });
  const errors = [];
  page.on("pageerror", e => errors.push(String(e)));
  await page.goto(`${BASE}?avatar=seed-daily-lids`, { waitUntil: "networkidle" });
  await page.waitForTimeout(2600);
  await page.evaluate(async speaking => {
    const mod = await import("/static/avatar/index.js");
    const avatar = mod.avatar;
    if (!avatar) { window.__avatarError = "no avatar singleton"; return; }
    // The exact production call path: _frame calls this.svg.setSpeaking every
    // frame with the lip clock's flag. Driving the rig directly holds the state
    // without needing a real audio timeline.
    if (avatar.svg && typeof avatar.svg.setSpeaking === "function") {
      // Hold the lip flag: _frame re-reads lip.speaking every frame and pushes it
      // into the renderer, so setting the rig directly would be overwritten.
      // No timeline is needed -- lip.sample() returns null without one, and the
      // stage close-up is driven purely by this flag.
      avatar.lip.speaking = speaking;
    } else { window.__avatarError = "renderer has no setSpeaking"; }
    avatar.setDialogueState(speaking ? "speaking" : "idle");
    window.__avatarReady = avatar.rendererKind;
  }, speaking);
  await page.waitForTimeout(2600);
  await page.screenshot({ path: `${out}/${name}.png` });
  const metrics = await page.evaluate(async () => {
    const mod = await import("/static/avatar/index.js");
    const avatar = mod.avatar;
    const camera = avatar && avatar.svg && avatar.svg.stageCamera;
    const stage = document.querySelector("#avatarStage");
    const canvas = stage && stage.querySelector("canvas");
    const plate = document.querySelector(".plate-inner");
    return {
      rendererKind: (avatar && avatar.rendererKind) || null,
      avatarError: window.__avatarError || null,
      stageMode: camera ? camera.mode : null,
      stageBlend: camera ? Number(camera.blend.toFixed(3)) : null,
      canvas: canvas ? { w: canvas.width, h: canvas.height,
                         cssW: Math.round(canvas.getBoundingClientRect().width),
                         cssH: Math.round(canvas.getBoundingClientRect().height) } : null,
      plate: plate ? (r => ({ w: Math.round(r.width), h: Math.round(r.height) }))(plate.getBoundingClientRect()) : null,
    };
  });
  await page.close();
  return { viewport: `${width}x${height}`, errors, metrics };
}

const rows = [];
rows.push(await shot(1440, 1000, "desktop-idle", false));
rows.push(await shot(1440, 1000, "desktop-speaking", true));
rows.push(await shot(390, 844, "mobile-speaking", true));
await browser.close();
await writeFile(`${out}/metrics.json`, JSON.stringify(rows, null, 2), "utf8");
console.log(JSON.stringify(rows, null, 2));
