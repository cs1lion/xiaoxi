/* V2 微光想法气泡状态机（协议 §16.2）。纯逻辑、零 DOM，便于 node 单测；
 * 真正渲染由 app.js 把回调接到 DOM。mumble 气泡可展开（带上下文接话）、
 * 可关闭、可自然消散；event_id 去重，重连不重复弹。 */

export function createGlimmer({ onExpand, onDismiss, onExpire } = {}) {
  let status = "hidden"; // hidden | visible | expanded
  let current = null;
  return {
    get status() { return status; },
    get current() { return current; },
    show(event) {
      if (!event || typeof event !== "object" || typeof event.text !== "string" || !event.text.trim()) {
        return false;
      }
      if (current && current.event_id === event.event_id) return false; // dedup / reconnect
      current = {
        event_id: event.event_id ?? null,
        text: event.text,
        source_refs: Array.isArray(event.source_refs) ? event.source_refs : [],
        expires_at: typeof event.expires_at === "number" ? event.expires_at : null,
      };
      status = "visible";
      return true;
    },
    expand() {
      if (status !== "visible" || !current) return null;
      status = "expanded";
      const context = { text: current.text, source_refs: current.source_refs };
      if (typeof onExpand === "function") onExpand(context);
      return context;
    },
    dismiss() {
      if (status === "hidden") return false;
      status = "hidden";
      current = null;
      if (typeof onDismiss === "function") onDismiss();
      return true;
    },
    poll(now) {
      if (status !== "visible" || !current || current.expires_at == null) return false;
      if (now >= current.expires_at) {
        const expired = current;
        status = "hidden";
        current = null;
        if (typeof onExpire === "function") onExpire(expired);
        return true;
      }
      return false;
    },
  };
}
