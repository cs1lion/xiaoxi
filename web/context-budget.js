/* 上下文预算（打磨轮 U-2 设置面板收纳项）。
 * 只展示服务端已经在 done.context_usage / assistant_result.result.context_usage
 * 里发送的资料清单摘要（协议第六节），不新增字段、不发请求、不做推断。 */

const number = (value) => (Number.isSafeInteger(value) && value >= 0 ? value : null);

export function formatContextBudget(usage) {
  if (!usage || usage.schema_version !== 1 || typeof usage.enabled !== "boolean") return "";
  if (!usage.enabled) return "上一轮已关闭记忆，没有自动带入保存的记忆、历史偏好或关联资料。";
  const budget = usage.budget;
  const parts = [];
  if (budget?.scope === "dynamic_context" && budget.unit === "characters"
    && number(budget.used_chars) !== null && number(budget.max_chars) !== null) {
    const omitted = number(budget.omitted_items);
    parts.push(`补充资料 ${budget.used_chars} / ${budget.max_chars} 字符${omitted ? `，因预算省略 ${omitted} 项` : ""}`);
  }
  const items = Array.isArray(usage.items) ? usage.items.length : null;
  if (items !== null) parts.push(`本轮列出 ${items} 项资料索引`);
  const omittedUsage = number(usage.omitted_usage_items);
  if (omittedUsage) parts.push(`另有 ${omittedUsage} 项未展示`);
  return parts.length ? `上一轮：${parts.join(" · ")}。详细清单见对应消息里的“本轮参考”。` : "上一轮没有列出补充资料。";
}

export function mountContextBudget(node) {
  if (!node) return null;
  const IDLE = "尚未开始对话。每一轮实际参考了哪些资料，会在消息里的“本轮参考”中列明。";
  node.textContent = IDLE;
  return {
    note(usage) {
      const text = formatContextBudget(usage);
      if (text) node.textContent = text;
    },
    reset() { node.textContent = IDLE; },
  };
}
