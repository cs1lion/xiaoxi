// Round 6: capture the audio the browser actually receives, and re-record frames.
//
// The page's WebSocket is created in app.js:56. To capture the audio frames
// the browser receives, we wrap WebSocket.prototype BEFORE the app script
// runs, then collect `audio` event payloads (base64) into window.__xiaoxiAudio.
//
// Then: type -> send -> wait -> collect frames + audio.
import { mkdir } from "node:fs/promises";

const DIR = "C:/Users/18712/ego/xiaoxi-20261112/frames/run2";
await mkdir(DIR, { recursive: true });

const task = await taskSpace("xiaoxi-live-accept-20261112");
const page = task.page("p1");

// Hook WebSocket before the app loads.
await page.cdp("Page.addScriptToEvaluateOnNewDocument", {
  source: `
    window.__xiaoxiAudio = [];
    window.__xiaoxiEvents = [];
    const NativeWS = window.WebSocket;
    window.WebSocket = function (...args) {
      const ws = new NativeWS(...args);
      ws.addEventListener("message", (ev) => {
        try {
          const data = JSON.parse(ev.data);
          window.__xiaoxiEvents.push({ type: data.type });
          if (data.type === "audio") {
            const raw = data.data || data.audio || "";
            if (raw) window.__xiaoxiAudio.push(raw);
          }
        } catch (e) { /* 不是 JSON 就忽略 */ }
      });
      return ws;
    };
    window.WebSocket.prototype = NativeWS.prototype;
    Object.assign(window.WebSocket, NativeWS);
  `,
});

await page.goto("http://127.0.0.1:8765/", { timeout: 30000 });
await page.waitForTimeout(2000);

let n = 0;
async function grab(tag) {
  n += 1;
  const p = await page.screenshot({ path: `${DIR}/f${String(n).padStart(3, "0")}-${tag}.png` });
  console.log(`frame ${n} ${tag}`);
}

await grab("idle");
await page.waitForSelector("loc=css:#textInput", { state: "visible", timeout: 15000 });
await page.fill("loc=css:#textInput", "我今天有点累，随便说两句吧。");
await page.waitForTimeout(500);
await grab("typed");
await page.waitForSelector("loc=css:#sendBtn", { state: "visible", timeout: 10000 });
await page.click("loc=css:#sendBtn");

// Capture while the model replies and TTS renders. Fish TTS takes ~2-4s per
// chunk, so give it room and keep the frame cadence even.
for (let i = 1; i <= 16; i += 1) {
  await page.waitForTimeout(1000);
  await grab(`w${String(i).padStart(2, "0")}`);
}

const collected = await page.evaluate(() => ({
  audio: window.__xiaoxiAudio || [],
  events: window.__xiaoxiEvents || [],
}));
console.log("audio chunks:", collected.audio.length);
console.log("event types:", JSON.stringify(collected.events));

// Write the base64 audio chunks as separate mp3 files.
const { writeFile } = await import("node:fs/promises");
for (let i = 0; i < collected.audio.length; i += 1) {
  const b64 = collected.audio[i];
  const path = `${DIR}/audio-${String(i).padStart(2, "0")}.mp3`;
  await writeFile(path, Buffer.from(b64, "base64"));
  console.log("wrote", path);
}
console.log("frames:", n);
console.log("spaceId:", task.spaceId);
