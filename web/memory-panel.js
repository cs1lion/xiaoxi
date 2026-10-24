/* 记忆面板纯函数（issue #28「何时说过 / 停用」）。
 * 零 DOM、零请求：渲染文案与停用操作的数据形状都从这里出，app.js 只负责
 * 建节点、发请求。字段全部来自 GET /api/memories 的 Memory.to_dict()
 * （evidence_message_id / evidence_quote / created_at / updated_at / status /
 * disabled），不新增协议字段。 */

export const DISABLED_TAG = "已停用，检索不使用";

function isoDate(seconds) {
  if (!Number.isFinite(seconds)) return "";
  const date = new Date(seconds * 1000);
  return Number.isFinite(date.getTime()) ? date.toISOString().slice(0, 10) : "";
}

/* 停用标签：只有显式 disabled 才出字，缺字段视为未停用。 */
export function memoryDisabledTag(memory) {
  return memory && memory.disabled === true ? DISABLED_TAG : "";
}

/* 「何时说过」的更新侧：只有真的改过（updated_at 晚于 created_at）才出字，
 * 未编辑过的记忆不重复渲染同一个日期。 */
export function memoryUpdatedNote(memory) {
  if (!memory) return "";
  const when = isoDate(memory.updated_at);
  if (!when) return "";
  if (!(Number.isFinite(memory.created_at) && memory.updated_at > memory.created_at)) return "";
  return `更新于 ${when}（UTC）`;
}

/* 来源引用的可追溯 id：linked 证据且消息 id ≥ 1 才有，否则不编。 */
export function memoryEvidenceNote(memory) {
  if (!memory || memory.evidence_state !== "linked") return "";
  if (!Number.isFinite(memory.evidence_message_id) || memory.evidence_message_id < 1) return "";
  return `原话出自消息 #${memory.evidence_message_id}`;
}

/* 停用/启用操作。只有检索真正会用的行（status 为 active）才提供开关：
 * 历史（superseded）与待核对（conflict）本来就不进装配，停用它们没有意义。 */
export function memoryDisableControl(memory) {
  if (!memory || memory.status !== "active") return null;
  return memory.disabled === true
    ? {
        label: "启用",
        body: { disabled: false },
        done: "已恢复，检索会重新考虑这条记忆。",
      }
    : {
        label: "停用",
        body: { disabled: true },
        done: "已停用，检索不再使用这条记忆。",
      };
}
