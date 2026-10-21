import { chromium } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";

// Usage: start the app, point BASE_URL at it, then run this script.
//   BASE_URL=http://127.0.0.1:8010/ node scripts/capture-almanac-ui.mjs
// Writes eval/almanac-20261017/{desktop,mobile}.png plus metrics.json.
const base = process.env.BASE_URL || "http://127.0.0.1:8010/";
const out = "eval/almanac-20261017";
await mkdir(out, { recursive: true });
const browser = await chromium.launch();

async function shot(width, height, name) {
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
  const errors = [];
  page.on("pageerror", error => errors.push(String(error)));
  page.on("console", message => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });
  await page.goto(base, { waitUntil: "networkidle" });
  await page.waitForTimeout(2600);
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: false });
  const metrics = await page.evaluate(() => {
    const pick = selector => {
      const node = document.querySelector(selector);
      if (!node) return null;
      const style = getComputedStyle(node);
      const box = node.getBoundingClientRect();
      return {
        font: style.font, color: style.color, background: style.backgroundColor,
        box: { x: Math.round(box.x), y: Math.round(box.y), w: Math.round(box.width), h: Math.round(box.height) },
      };
    };
    const canvas = document.querySelector("#avatarStage canvas");
    const welcome = document.querySelector("#welcome");
    const plate = document.querySelector(".plate-inner");
    return {
      innerWidth: window.innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      scrollHeight: document.documentElement.scrollHeight,
      avatarCanvas: canvas ? { w: canvas.width, h: canvas.height, css: getComputedStyle(canvas).width } : null,
      welcome: welcome ? { hidden: welcome.hidden, box: welcome.getBoundingClientRect().toJSON() } : null,
      plate: plate ? plate.getBoundingClientRect().toJSON() : null,
      title: pick(".welcome h2"),
      lede: pick(".welcome p"),
      suggestion: pick(".suggestions button"),
      composer: pick(".capsule"),
      side: pick(".side"),
      horizon: pick(".room-label"),
    };
  });
  await page.close();
  return { viewport: `${width}x${height}`, errors, metrics };
}

const desktop = await shot(1440, 1000, "desktop");
const mobile = await shot(390, 844, "mobile");
await browser.close();
await writeFile(`${out}/metrics.json`, JSON.stringify({ desktop, mobile }, null, 2), "utf8");
console.log(JSON.stringify({ desktop, mobile }, null, 2));
