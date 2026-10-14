/* 渲染层的冒烟测试：用最小 DOM 桩把 svg.js 挂起来跑几帧。
 *
 * 存在的理由很具体：之前 idle.js 里那个 custom 注视点的空指针，
 * 纯物理测试测不出来（它不碰渲染），而浏览器里只表现为"整个形象不动了"。
 * 没有桩就只能靠人眼发现——而人眼是最容易漏掉静默崩溃的一环。
 *
 * 桩要实现的东西只有 setAttribute / appendChild / querySelector，
 * 因为 svg.js 只用这几个。路径数据本身不断言对错，那是几何的事；
 * 这里只关心"会不会抛"和"属性有没有真的被写下去"。
 */

class FakeNode {
  constructor(name, ns) {
    this.nodeName = name;
    this.ns = ns;
    this.attrs = Object.create(null);
    this.children = [];
    this.text = "";
    /* 有子节点时 querySelector 才有意义。这里用最朴素的实现：
     * 只按选择器里的属性值找，够 svg.js 那两处 querySelector 用。 */
    this._queries = Object.create(null);
  }
  setAttribute(k, v) {
    this.attrs[k] = String(v);
    if (this.nodeName === "path" && k === "d") this._d = String(v);
  }
  getAttribute(k) { return this.attrs[k] ?? null; }
  appendChild(child) {
    this.children.push(child);
    this._queries[child.nodeName] = child;
    return child;
  }
  querySelector(sel) {
    const m = sel.match(/\[fill="([^"]+)"\]/);
    if (m) {
      for (const c of this.children) if (c.attrs.fill === m[1]) return c;
      return null;
    }
    return this._queries[sel] || null;
  }
}

const created = [];
globalThis.document = {
  createElementNS(ns, name) {
    const n = new FakeNode(name, ns);
    created.push(n);
    return n;
  },
};
globalThis.performance = globalThis.performance || { now: () => 0 };
globalThis.requestAnimationFrame = () => 0;

const { SvgRig } = await import("./render/svg.js");
const { ParamStore } = await import("./params.js");

let failures = 0;
function check(name, ok, detail = "") {
  if (ok) console.log(`  ok   ${name}`);
  else { console.log(`  FAIL ${name}${detail ? "  -> " + detail : ""}`); failures++; }
}

/* 挂载 + 跑若干帧，覆盖各个参数区间的极值 */
function render(frames, paramsFor) {
  const rig = new SvgRig();
  const host = new FakeNode("div");
  rig.mount(host, {});
  const store = new ParamStore({ ParamEyeLOpen: 1, ParamEyeROpen: 1 }, {});
  const dt = 1 / 60;
  let nan = null;
  for (let i = 0; i < frames; i++) {
    const p = paramsFor(i / 60);
    for (const [k, v] of Object.entries(p)) store.current[k] = v;
    try {
      rig.draw(store.current, dt);
    } catch (e) {
      return { ok: false, err: e, stage: "draw" };
    }
    for (const n of created) {
      for (const [k, v] of Object.entries(n.attrs)) {
        if (typeof v === "string" && /NaN|Infinity/.test(v)) {
          nan = nan || `${n.nodeName}.${k} = ${v}`;
        }
      }
    }
  }
  return { ok: true, nan, nodes: created.length, host };
}

console.log("svg 挂载与绘制");
{
  created.length = 0;
  const r = render(120, () => ({}));
  check("空姿态跑 120 帧不抛异常", r.ok, r.err ? r.err.message : "");
  check("产出节点数量合理", r.ok && r.nodes > 40, `${r.nodes} 个`);
  check("无 NaN / Infinity 属性", !r.nan, r.nan || "");
}

console.log("眼睑随眼球上下动");
{
  created.length = 0;
  const rig = new SvgRig();
  rig.mount(new FakeNode("div"), {});
  const store = new ParamStore({ ParamEyeLOpen: 1, ParamEyeROpen: 1 }, {});
  // 预热到收敛
  for (let i = 0; i < 120; i++) { store.current.ParamEyeBallY = 1; rig.draw(store.current, 1 / 60); }
  const lookingDown = topOfEyePath(rig);
  for (let i = 0; i < 120; i++) { store.current.ParamEyeBallY = -1; rig.draw(store.current, 1 / 60); }
  const lookingUp = topOfEyePath(rig);
  check("眼球下看时上眼睑压下来", lookingDown > lookingUp,
    `down=${lookingDown} up=${lookingUp}`);
}

/* 从已挂载的 rig 里量出眼裂上沿的高度。
 * 眼裂路径的第一个控制点是上眼睑，y 越小越高。 */
function topOfEyePath(rig) {
  const eye = rig.nodes.eyes[0];
  const d = eye.clipPath.getAttribute("d") || "";
  const m = d.match(/Q\s+(-?[\d.]+)\s+(-?[\d.]+)/);
  return m ? parseFloat(m[2]) : NaN;
}

console.log("极端参数");
{
  const cases = {
    "全 1": { ParamAngleX: 1, ParamAngleY: 1, ParamAngleZ: 1, ParamEyeLOpen: 1, ParamEyeROpen: 1,
              ParamMouthOpenY: 1, ParamMouthForm: 1, ParamMouthRound: 1, ParamCheek: 1 },
    "全 -1": { ParamAngleX: -1, ParamAngleY: -1, ParamAngleZ: -1, ParamEyeLOpen: 0, ParamEyeROpen: 0,
               ParamMouthOpenY: 0, ParamMouthForm: -1, ParamMouthRound: -1, ParamCheek: 0 },
    "笑眼+张口": { ParamEyeLSmile: 1, ParamEyeRSmile: 1, ParamEyeLOpen: 0.1, ParamEyeROpen: 0.1,
                   ParamMouthOpenY: 1, ParamMouthForm: 1 },
    "眼球到底": { ParamEyeBallX: 1, ParamEyeBallY: 1, ParamEyeLOpen: 1, ParamEyeROpen: 1 },
  };
  for (const [name, p] of Object.entries(cases)) {
    created.length = 0;
    const r = render(90, () => p);
    check(`${name} 不抛异常`, r.ok, r.err ? r.err.message : "");
    check(`${name} 无 NaN`, !r.nan, r.nan || "");
  }
}

console.log("虹膜缩放 ParamEyeBallForm");
{
  /* 读 iris 变换里的 scale 值。这是唯一能证明"参数真的接到了渲染上"的地方：
   * params.js 声明了、avatar.yaml 的 neutral 写了、协议检查也过了，
   * 但 svg.js 少读一个 P. 就全都白搭，而且不报错。 */
  function irisScaleFor(form) {
    const rig = new SvgRig();
    rig.mount(new FakeNode("div"), {});
    const st = new ParamStore(
      { ParamEyeLOpen: 1, ParamEyeROpen: 1, ParamEyeBallForm: form },
      {}
    );
    // 多跑几帧，iris 上没有弹簧，第一帧就该到位
    let s = null;
    for (let i = 0; i < 30; i++) {
      st.current.ParamEyeBallForm = form;
      rig.draw(st.current, 1 / 60);
      s = rig.nodes.eyes[0].iris.getAttribute("transform").match(
        /scale\((-?[\d.]+) /);
      if (s) break;
    }
    return s ? parseFloat(s[1]) : NaN;
  }

  const base = irisScaleFor(0);
  const big = irisScaleFor(1);
  const small = irisScaleFor(-1);
  check("中位不缩放", Math.abs(base - 1) < 0.02, `scale=${base}`);
  check("+1 放大虹膜", big > 1.25, `scale=${big}`);
  check("-1 收小虹膜", small < 0.75, `scale=${small}`);
  check("缩放关于虹膜中心对称", Math.abs((big - 1) + (small - 1)) < 0.02,
    `+1:+${(big - 1).toFixed(3)} -1:${(small - 1).toFixed(3)}`);

  /* 和 ParamPupil 必须相乘而不是互相覆盖：两者驱动的是不同肌肉
   * （虹膜 vs 瞳孔括约肌），表达的东西也不同。同时给满，
   * iris 和 pupilG 上应该分别有自己的 scale。 */
  const rig = new SvgRig();
  rig.mount(new FakeNode("div"), {});
  const st = new ParamStore(
    { ParamEyeLOpen: 1, ParamEyeROpen: 1, ParamEyeBallForm: 1, ParamPupil: 1.4 },
    []
  );
  for (let i = 0; i < 30; i++) {
    st.current.ParamEyeBallForm = 1;
    st.current.ParamPupil = 1.4;
    rig.draw(st.current, 1 / 60);
  }
  const irisT = rig.nodes.eyes[0].iris.getAttribute("transform");
  const pupilT = rig.nodes.eyes[0].pupilG.getAttribute("transform");
  check("虹膜缩放与瞳孔缩放同时生效",
    /scale\(1\.[3-9]/.test(irisT) && /scale\(1\.4/.test(pupilT),
    `iris=${irisT} pupil=${pupilT}`);

  /* 放大的虹膜必须仍被眼裂裁掉，不能溢出到眼白外 */
  const created2 = [];
  const r2 = render(60, () => ({ ParamEyeBallForm: 1, ParamEyeLOpen: 1 }));
  check("虹膜放大后无 NaN", !r2.nan, r2.nan || "");
}

console.log("长时间运行不漂移");
{
  created.length = 0;
  // 眼球持续上看到极限，检验眼睑不会一路压到把眼睛挤出画面
  const r = render(600, () => ({ ParamEyeBallY: -1, ParamEyeLOpen: 1, ParamEyeROpen: 1 }));
  check("眼球持续上看 600 帧无 NaN", !r.nan, r.nan || "");
  const rig2 = new SvgRig();
  rig2.mount(new FakeNode("div"), {});
  const st = new ParamStore({ ParamEyeLOpen: 1, ParamEyeROpen: 1 }, {});
  for (let i = 0; i < 600; i++) { st.current.ParamEyeBallY = -1; st.current.ParamEyeLOpen = 1; st.current.ParamEyeROpen = 1; rig2.draw(st.current, 1 / 60); }
  const top = topOfEyePath(rig2);
  check("上眼睑没有塌到眼睛底部以下", Number.isFinite(top) && top > -60, `y=${top}`);
}

console.log(failures === 0 ? "\n全部通过" : `\n${failures} 条不通过`);
process.exit(failures === 0 ? 0 : 1);
