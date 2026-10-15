/* V2 星图渲染模型单测（协议 §18）：seed/reflection 节点、epiphany/parent_child 边。
 * bridge.js 零导入纯模块，经 data: URL 以 ESM 加载。 */
import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const load = async (name) => import(`data:text/javascript;base64,${Buffer.from(
  await readFile(new URL(name, import.meta.url))).toString("base64")}`);
const { toRenderModel, NODE_KINDS, EDGE_KINDS } = await load("./bridge.js");

const payload = {
  nodes: [
    { id: "seed:s1", kind: "seed", label: "深夜决策", date: "2026-10-14", weight: 0.6, group: "self", ref_id: "s1" },
    { id: "seed:s2", kind: "seed", label: "无干扰时间", date: "2026-10-15", weight: 0.5, group: "self", ref_id: "s2" },
    { id: "reflection:r1", kind: "reflection", label: "与被打断有关", date: "2026-10-15", weight: 0.6, group: "self", ref_id: "r1" },
  ],
  edges: [
    { source: "seed:s1", target: "seed:s2", kind: "parent_child", weight: 0.6, reason: "裂变", created_at: 1 },
    { source: "seed:s1", target: "reflection:r1", kind: "epiphany", weight: 0.7, reason: "跨界共振", created_at: 2 },
    { source: "diary:d", target: "seed:s2", kind: "related_memory", weight: 0.5, reason: "dangling", created_at: 3 },
  ],
  stats: {},
  truncated: false,
};

test("V2 node and edge kinds are declared", () => {
  for (const k of ["seed", "reflection"]) assert.ok(NODE_KINDS.includes(k));
  for (const k of ["parent_child", "related_memory", "epiphany"]) assert.ok(EDGE_KINDS.includes(k));
});

test("seed/reflection nodes render with correct kind and group", () => {
  const model = toRenderModel(payload);
  const seed = model.byId.get("seed:s1");
  assert.equal(seed.kind, "seed");
  assert.equal(seed.group, "self");
  assert.equal(model.byId.get("reflection:r1").kind, "reflection");
  assert.equal(model.kindCounts.seed, 2);
  assert.equal(model.kindCounts.reflection, 1);
});

test("epiphany edge keeps its reason; dangling edge dropped", () => {
  const model = toRenderModel(payload);
  const epiphany = model.edges.find((e) => e.kind === "epiphany");
  assert.ok(epiphany, "epiphany between two real nodes is kept");
  assert.equal(epiphany.reason, "跨界共振");
  assert.equal(epiphany.source, "seed:s1");
  const parentChild = model.edges.find((e) => e.kind === "parent_child");
  assert.equal(parentChild.reason, "裂变");
  // diary:d is not a node -> the related_memory edge to it is dropped (no dangling)
  assert.equal(model.edges.find((e) => e.reason === "dangling"), undefined);
});
