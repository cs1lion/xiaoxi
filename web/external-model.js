/* 外部 .vrm 一键加载（打磨轮 U-2 设置面板控件）。
 * 只调用形象层既有的 avatar.setRenderer(kind, url)，不新增任何协议字段或事件；
 * 地址校验、失败路径与状态文案都在这里，加载失败时保留当前形象。 */

const ALLOWED_SCHEME = /^https:\/\//i;
const IDLE = "可加载任意外部 .vrm（HTTPS 或站内路径）；失败时保留当前形象。模型许可由提供方负责，加载前请确认你有权使用。";

export function normalizeModelUrl(raw) {
  const value = typeof raw === "string" ? raw.trim() : "";
  if (!value) return { ok: false, reason: "请先填写 .vrm 模型地址。" };
  if (value.length > 2048) return { ok: false, reason: "地址太长，请改用更短的链接。" };
  if (ALLOWED_SCHEME.test(value)) {
    try { new URL(value); } catch { return { ok: false, reason: "地址无法解析，请检查后重试。" }; }
    return { ok: true, url: value };
  }
  if (value.startsWith("/")) return { ok: true, url: value };
  return { ok: false, reason: "只支持 https:// 链接或站内 /static/ 路径，未做任何加载。" };
}

export function mountExternalModel({ input, button, status, load }) {
  if (!input || !button || !status || typeof load !== "function") return null;
  let busy = false;

  const say = (text) => { status.textContent = text; };

  async function apply() {
    if (busy) return;
    const checked = normalizeModelUrl(input.value);
    if (!checked.ok) { say(checked.reason); input.focus(); return; }
    busy = true;
    button.disabled = true;
    say("正在加载外部形象，聊天和语音不受影响…");
    try {
      await load(checked.url);
      say("外部形象已启用。加载失败时会自动保留当前形象。");
    } catch (error) {
      say(`加载失败，保留当前形象：${error?.message || error}`);
    } finally {
      busy = false;
      button.disabled = false;
    }
  }

  button.addEventListener("click", () => { void apply(); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.isComposing) { event.preventDefault(); void apply(); }
  });
  say(IDLE);
  return { apply, idle: () => say(IDLE) };
}
