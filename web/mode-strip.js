/* 模式状态条（U-5）：让用户随时知道她现在处于什么状态。
 * 连续对话开关沿用既有 #bargeToggle（id、可访问名与文案不变，既有测试零改动）；
 * 其余模式是只读状态片，只在对应模式真正生效时出现。
 * 续播按钮从输入区迁到这里：它本就是打断后的一次性上下文动作。 */

export function mountModeStrip(root, { onToggleContinuous } = {}) {
  if (!root) return null;
  root.classList.add("mode-strip");
  root.replaceChildren();

  const chip = (id, text, { action = false } = {}) => {
    const node = document.createElement(action ? "button" : "span");
    if (id) node.id = id;
    node.className = action ? "mode-chip mode-action" : "mode-chip";
    if (action) node.type = "button";
    node.textContent = text;
    return node;
  };

  const continuous = chip("bargeToggle", "连续对话：关", { action: true });
  continuous.setAttribute("aria-pressed", "false");
  continuous.title = "开启后检测开口与停顿；确认开口才上传音频。建议戴耳机。";
  const experience = chip("modeExperience", "");
  const practice = chip("modePractice", "");
  const micro = chip("modeMicro", "微句流");
  const starmap = chip("modeStarmap", "星图讲述中");
  const self = chip("modeSelf", "");
  for (const node of [experience, practice, micro, starmap, self]) node.hidden = true;
  const resume = chip("resumeSpeechBtn", "续播被打断句", { action: true });
  resume.title = "只续播刚被打断的当前句剩余音频，不重做任务；90秒内有效。";
  resume.hidden = true;
  root.append(continuous, experience, practice, micro, starmap, self, resume);

  if (typeof onToggleContinuous === "function") {
    continuous.addEventListener("click", () => onToggleContinuous());
  }

  const show = (node, text) => {
    if (!text) { node.hidden = true; node.textContent = ""; return; }
    node.textContent = text;
    node.hidden = false;
  };

  function update(state = {}) {
    const on = !!state.continuous;
    continuous.textContent = `连续对话：${on ? "开" : "关"}`;
    continuous.setAttribute("aria-pressed", String(on));
    continuous.classList.toggle("is-on", on);
    show(experience, state.experience || "");
    show(practice, state.practice || "");
    show(micro, state.micro ? "微句流" : "");
    show(starmap, state.starmap ? "星图讲述中" : "");
    show(self, state.self || "");
    for (const node of [experience, practice, micro, starmap, self]) {
      node.classList.toggle("is-active", !node.hidden);
    }
  }

  update({});

  return { element: root, continuous, resume, update };
}
