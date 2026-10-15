/* 星图渲染：SVG + Pointer Events，不引框架、不引图库。
 * 布局是确定性径向双簇（user 一簇、self 一簇），不跑力模拟——
 * 安静的产品不该有持续抖动的星点（UI_DESIGN 的动效预算）。
 * 节点是带 role="button" 的 <g>，键盘可达，文案一律 textContent。 */

const VIEW_W = 1000;
const VIEW_H = 700;
const GOLDEN_ANGLE = 2.399963229728653;
const GROUP_CENTER = { user: { x: 385, y: 350 }, self: { x: 615, y: 350 } };
const GROUP_RADIUS = 250;
const MIN_K = 0.25;
const MAX_K = 6;
const TAP_SLOP = 5;

export const KIND_LABELS = {
  memory: "记忆", diary: "日记", spark: "灵感火花", taste: "品味", worldview: "世界观",
  seed: "好奇心种子", reflection: "一次反思",
};
export const GROUP_LABELS = { user: "关于你", self: "小溪自己" };
export const EDGE_LABELS = {
  evidences: "记忆印证日记", born: "火花诞生自", shaped: "心境塑造", mentions: "同主题",
  parent_child: "裂变出", related_memory: "选材关联", epiphany: "跨界顿悟",
};

const SVG_NS = "http://www.w3.org/2000/svg";
const svgEl = (name) => document.createElementNS(SVG_NS, name);
const clamp = (value, lo, hi) => Math.min(hi, Math.max(lo, value));

/* 确定性布局：簇内按 weight 降序，重星点靠内圈；同权重按 id 排序保证稳定。
 * 直接改写 node 对象，补上 x/y/r 供渲染与聚焦使用。 */
export function layoutNodes(nodes) {
  const byGroup = { user: [], self: [] };
  for (const node of nodes) byGroup[node.group]?.push(node);
  for (const group of ["user", "self"]) {
    const list = byGroup[group]
      .sort((a, b) => b.weight - a.weight || (a.id < b.id ? -1 : 1));
    const center = GROUP_CENTER[group];
    list.forEach((node, index) => {
      const t = list.length === 1 ? 0.5 : (index + 0.5) / list.length;
      const angle = index * GOLDEN_ANGLE;
      const radius = GROUP_RADIUS * (0.16 + 0.84 * Math.sqrt(t)) * (1.12 - 0.45 * node.weight);
      node.x = center.x + Math.cos(angle) * radius;
      node.y = center.y + Math.sin(angle) * radius * 0.8;
      node.r = 4.5 + node.weight * 10.5;
    });
  }
  return nodes;
}

export function createGraphView(host, { onNodeActivate } = {}) {
  const controller = new AbortController();
  const on = (node, type, fn, options) => node.addEventListener(type, fn, { signal: controller.signal, ...options });

  const svg = svgEl("svg");
  svg.setAttribute("class", "starmap-svg");
  svg.setAttribute("viewBox", `0 0 ${VIEW_W} ${VIEW_H}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.setAttribute("role", "group");
  svg.setAttribute("aria-label", "星图：拖拽漫游，滚轮或双指缩放，回车选择节点");
  const world = svgEl("g");
  world.setAttribute("class", "starmap-world");
  const edgeLayer = svgEl("g");
  edgeLayer.setAttribute("class", "starmap-edges");
  const nodeLayer = svgEl("g");
  nodeLayer.setAttribute("class", "starmap-nodes");
  world.append(edgeLayer, nodeLayer);
  svg.append(world);
  host.append(svg);

  let model = null;
  let selectedId = null;
  const transform = { x: 0, y: 0, k: 1 };
  const pointers = new Map();
  let dragDistance = 0;
  let pinchDistance = 0;

  const apply = () => {
    world.setAttribute("transform", `translate(${transform.x.toFixed(2)} ${transform.y.toFixed(2)}) scale(${transform.k.toFixed(3)})`);
  };
  const toView = (clientX, clientY) => {
    const point = svg.createSVGPoint();
    point.x = clientX;
    point.y = clientY;
    return point.matrixTransform(svg.getScreenCTM().inverse());
  };
  const clampPan = () => {
    const minX = Math.min(0, VIEW_W * (1 - transform.k));
    const maxX = Math.max(0, VIEW_W * (1 - transform.k));
    const minY = Math.min(0, VIEW_H * (1 - transform.k));
    const maxY = Math.max(0, VIEW_H * (1 - transform.k));
    transform.x = clamp(transform.x, minX, maxX);
    transform.y = clamp(transform.y, minY, maxY);
  };
  const zoomAround = (px, py, factor) => {
    const k2 = clamp(transform.k * factor, MIN_K, MAX_K);
    transform.x = px - (px - transform.x) * (k2 / transform.k);
    transform.y = py - (py - transform.y) * (k2 / transform.k);
    transform.k = k2;
    clampPan();
    apply();
  };

  on(svg, "wheel", (event) => {
    event.preventDefault();
    const p = toView(event.clientX, event.clientY);
    zoomAround(p.x, p.y, Math.exp(-event.deltaY * 0.0015));
  }, { passive: false });
  on(svg, "pointerdown", (event) => {
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    dragDistance = 0;
    try { svg.setPointerCapture(event.pointerId); } catch (_) {}
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      pinchDistance = Math.hypot(a.x - b.x, a.y - b.y);
    }
  });
  on(svg, "pointermove", (event) => {
    const previous = pointers.get(event.pointerId);
    if (!previous) return;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    dragDistance += Math.hypot(event.clientX - previous.x, event.clientY - previous.y);
    if (pointers.size >= 2) {
      const [a, b] = [...pointers.values()];
      const distance = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinchDistance > 0 && distance > 0) {
        const mid = toView((a.x + b.x) / 2, (a.y + b.y) / 2);
        zoomAround(mid.x, mid.y, distance / pinchDistance);
      }
      pinchDistance = distance;
      return;
    }
    const now = toView(event.clientX, event.clientY);
    const before = toView(previous.x, previous.y);
    transform.x += now.x - before.x;
    transform.y += now.y - before.y;
    clampPan();
    apply();
  });
  const release = (event) => {
    if (!pointers.delete(event.pointerId)) return;
    if (pointers.size < 2) pinchDistance = 0;
    if (pointers.size === 0 && dragDistance <= TAP_SLOP) {
      const target = document.elementFromPoint(event.clientX, event.clientY);
      const group = target?.closest?.(".star-node");
      if (group?.dataset.id) onNodeActivate?.(group.dataset.id);
    }
  };
  on(svg, "pointerup", release);
  on(svg, "pointercancel", (event) => {
    pointers.delete(event.pointerId);
    pinchDistance = 0;
  });

  const renderNode = (node) => {
    const group = svgEl("g");
    group.setAttribute("class", `star-node group-${node.group} kind-${node.kind}`);
    group.setAttribute("transform", `translate(${node.x.toFixed(1)} ${node.y.toFixed(1)})`);
    group.setAttribute("role", "button");
    group.setAttribute("tabindex", "0");
    group.dataset.id = node.id;
    group.dataset.weight = String(node.weight);
    group.setAttribute("aria-label", `${node.label}（${GROUP_LABELS[node.group] || node.group}）`);
    const halo = svgEl("circle");
    halo.setAttribute("class", "star-halo");
    halo.setAttribute("r", (node.r + 7).toFixed(1));
    const dot = svgEl("circle");
    dot.setAttribute("class", "star-dot");
    dot.setAttribute("r", node.r.toFixed(1));
    const label = svgEl("text");
    label.setAttribute("class", "star-label");
    label.setAttribute("y", (node.r + 13).toFixed(1));
    label.textContent = node.label;
    group.append(halo, dot, label);
    if (node.weight >= 0.55) group.classList.add("has-label");
    on(group, "pointerenter", () => group.classList.add("has-label"));
    on(group, "pointerleave", () => {
      if (node.weight < 0.55 && node.id !== selectedId) group.classList.remove("has-label");
    });
    on(group, "focus", () => group.classList.add("has-label"));
    on(group, "blur", () => {
      if (node.weight < 0.55 && node.id !== selectedId) group.classList.remove("has-label");
    });
    on(group, "keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        onNodeActivate?.(node.id);
      }
    });
    return group;
  };

  return {
    get model() { return model; },
    get selectedId() { return selectedId; },
    setModel(next) {
      model = next;
      layoutNodes(next.nodes);
      edgeLayer.replaceChildren();
      nodeLayer.replaceChildren();
      for (const edge of next.edges) {
        const from = next.byId.get(edge.source);
        const to = next.byId.get(edge.target);
        if (!from || !to) continue;
        const line = svgEl("line");
        line.setAttribute("class", `star-edge kind-${edge.kind}`);
        line.setAttribute("x1", from.x.toFixed(1));
        line.setAttribute("y1", from.y.toFixed(1));
        line.setAttribute("x2", to.x.toFixed(1));
        line.setAttribute("y2", to.y.toFixed(1));
        line.setAttribute("stroke-width", (0.8 + edge.weight * 1.6).toFixed(2));
        edgeLayer.append(line);
      }
      for (const node of next.nodes) nodeLayer.append(renderNode(node));
      if (selectedId && !next.byId.has(selectedId)) selectedId = null;
      apply();
    },
    setSelected(id) {
      selectedId = id || null;
      for (const group of nodeLayer.children) {
        const active = group.dataset.id === selectedId;
        group.classList.toggle("is-selected", active);
        group.classList.toggle("has-label", active || Number(group.dataset.weight) >= 0.55);
      }
    },
    setNarrating(id) {
      for (const group of nodeLayer.children) {
        group.classList.toggle("is-narrating", group.dataset.id === id);
        if (group.dataset.id === id) group.classList.add("has-label");
      }
    },
    focusNode(id) {
      const node = model?.byId.get(id);
      if (!node) return;
      const k = Math.max(transform.k, 1.6);
      transform.x = VIEW_W / 2 - node.x * k;
      transform.y = VIEW_H / 2 - node.y * k;
      transform.k = k;
      clampPan();
      apply();
      const group = [...nodeLayer.children].find((item) => item.dataset.id === id);
      group?.focus?.({ preventScroll: true });
    },
    zoomBy(factor) {
      zoomAround(VIEW_W / 2, VIEW_H / 2, factor);
    },
    resetView() {
      transform.x = 0;
      transform.y = 0;
      transform.k = 1;
      apply();
    },
    clear() {
      edgeLayer.replaceChildren();
      nodeLayer.replaceChildren();
      model = null;
      selectedId = null;
    },
    dispose() {
      controller.abort();
      svg.remove();
    },
  };
}
