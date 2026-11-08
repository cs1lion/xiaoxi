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

/* 「忘掉」操作。DELETE /api/memories/{id} 删的是这条事实的**完整身份链**
 * ——所有历史版本加上当前生效行。所以只有 active 行能提供这个按钮：在一条
 * 标注「待核对，不自动使用」的 conflict 行上点忘掉，用户想丢弃的是那条待核对
 * 的新说法，实际被删的却是正在生效的记忆（实测整链 6 行全没）。历史
 * （superseded）与已完成（done）行同理。要删整条事实另有显式入口
 * POST /api/memories/forget-key，不靠这个按钮的默认行为。 */
export function memoryForgetControl(memory) {
  if (!memory || memory.status !== "active") return null;
  return {
    label: "忘掉",
    done: "已忘掉这条记忆。",
  };
}

/* 为什么非 active 行没有「忘掉」按钮：它们不进检索装配，删它们既无收益，
 * 又会通过整链删除误伤当前生效的记忆。给一句话，别让按钮消失像 bug。
 * 真要整条删掉时用 POST /api/memories/forget-key（memoryForgetKeyControl），
 * 它在确认文案里写明「含当前生效的一行」。全局「清空」不该在这里引导。 */
export function memoryForgetNote(memory) {
  if (!memory || memory.status === "active") return "";
  return "这条不影响当前生效的记忆，无需单独删除；要删整条事实用「忘掉整条事实」。";
}

/* 物理硬删除的确认文案：DELETE /api/memories/{id} 删完整身份链，不可恢复。
 * UI 必须先把这句话摆出来，用户再点一次「确认删除」。 */
export const DELETE_IRREVERSIBLE = "删除后不可恢复。";

export function memoryForgetConfirm(memory) {
  const value = typeof memory?.value === "string" && memory.value.trim()
    ? memory.value.trim()
    : "这条记忆";
  return `删除「${value}」的全部版本？${DELETE_IRREVERSIBLE}`;
}

/* 「忘掉整条事实」入口：POST /api/memories/forget-key，按 key 删掉所有版本。
 * active 行已有「忘掉」（同样是整链删除），不再重复给第二个删除按钮；
 * 历史/待核对行没有「忘掉」，这个入口正好补上，且确认文案会警告会连带
 * 删掉当前生效的一行。 */
export function memoryForgetKeyControl(memory) {
  if (!memory || memory.status === "active") return null;
  const key = typeof memory.key === "string" ? memory.key.trim() : "";
  if (!key) return null;
  return {
    label: "忘掉整条事实",
    key,
    done: "这条事实的全部版本已删除，且不可恢复。",
  };
}

export function memoryForgetKeyConfirm(memory) {
  const key = typeof memory?.key === "string" && memory.key.trim()
    ? memory.key.trim()
    : "这条事实";
  return `删除「${key}」的所有版本（含当前生效的一行）？${DELETE_IRREVERSIBLE}`;
}

/* 历史时间线（GET /api/memories/history?key=）：只保留渲染需要的字段，
 * 缺日期就不编日期。 */
export function memoryHistoryRows(items) {
  if (!Array.isArray(items)) return [];
  return items.map((memory) => ({
    status: typeof memory?.status === "string" ? memory.status : "",
    value: typeof memory?.value === "string" ? memory.value : "",
    created: isoDate(memory?.created_at),
  }));
}
