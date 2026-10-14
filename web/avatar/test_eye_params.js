/* 眼部绝对目标、中位和参数范围的回归检查。 */
import assert from "node:assert/strict";
import { ALL_PARAMS, ParamStore, Rigger, clamp, clampParam } from "./params.js";
import { EmotionDriver } from "./drivers/emotion.js";

const settle = (driver) => {
  for (let i = 0; i < 600; i++) driver.update(1 / 60);
};
const commitExpression = (driver, neutral = {}) => {
  const store = new ParamStore(neutral);
  const rig = new Rigger(neutral);
  rig.register("expression", 20);
  rig.begin();
  for (const [key, value] of Object.entries(driver.shift())) rig.set("expression", key, value);
  rig.commit(store);
  return store.target;
};

{
  const store = new ParamStore();
  const rig = new Rigger();
  rig.commit(store);
  for (const key of ["ParamEyeLOpen", "ParamEyeROpen", "ParamPupil", "ParamEyeHighlight"]) {
    assert.equal(store.neutral[key], 1, `${key} 默认中位应为 1`);
    assert.equal(store.target[key], 1, `${key} 没有驱动时应保持中位`);
  }
  assert.ok(ALL_PARAMS.includes("ParamEyeHighlight"));
}

{
  const driver = new EmotionDriver({
    base: { arousal: 0.25, shift: { ParamEyeLOpen: 0.97, ParamEyeROpen: 0.97 } },
  });
  assert.equal(driver.ownsGaze, false, "尚未收到情绪时不能锁住视线");
  driver.set("base");
  assert.equal(driver.shift().ParamEyeLOpen, 1, "初次淡入应从睁眼中位开始");
  settle(driver);
  const pose = commitExpression(driver);
  assert.ok(pose.ParamEyeLOpen > 0.97 && pose.ParamEyeLOpen <= 1, "日常弱表情仍应接近完全睁眼");
  assert.ok(pose.ParamEyeROpen > 0.97 && pose.ParamEyeROpen <= 1);
}

{
  const poseFor = (arousal) => {
    const driver = new EmotionDriver({
      base: { arousal, shift: { ParamEyeLOpen: 0.4, ParamEyeROpen: 1, ParamPupil: 1.4,
        ParamEyeHighlight: 0.2, ParamMouthForm: 0.6 } },
    });
    driver.set("base");
    settle(driver);
    return commitExpression(driver);
  };
  const calm = poseFor(0), strong = poseFor(1);
  assert.ok(calm.ParamEyeLOpen > strong.ParamEyeLOpen, "低强度眯眼应更接近睁眼中位");
  assert.equal(calm.ParamEyeROpen, 1, "绝对目标等于中位时不能被强度压成半睁");
  assert.equal(strong.ParamEyeROpen, 1);
  assert.ok(calm.ParamPupil > 1 && calm.ParamPupil < strong.ParamPupil, "瞳孔变化应围绕倍率 1 缩放");
  assert.ok(calm.ParamEyeHighlight > strong.ParamEyeHighlight, "低强度高光隐藏应更接近可见中位");
  assert.ok(calm.ParamMouthForm > 0 && calm.ParamMouthForm < strong.ParamMouthForm);
}

{
  const neutral = { ParamEyeLOpen: 0.9, ParamEyeROpen: 0.92, ParamPupil: 1.15, ParamEyeHighlight: 0.8 };
  const driver = new EmotionDriver({ base: { arousal: 0, shift: { ...neutral } } }, neutral);
  driver.set("base");
  settle(driver);
  const pose = commitExpression(driver, neutral);
  for (const [key, value] of Object.entries(neutral)) assert.equal(pose[key], value, `${key} 应尊重配置中位`);
}

{
  const driver = new EmotionDriver({
    happy: { blend: 0.5, arousal: 0.8, shift: { ParamEyeLOpen: 0.7, ParamEyeHighlight: 0.9 } },
    sleepy: { blend: 0.5, arousal: 0.2, shift: { ParamEyeLOpen: 0.4, ParamEyeHighlight: 0.4 } },
  });
  driver.set("happy");
  settle(driver);
  const outgoing = driver.shift();
  const arousalBefore = driver.arousal;
  driver.set("sleepy");
  const first = driver.shift();
  assert.equal(first.ParamEyeLOpen, outgoing.ParamEyeLOpen, "新过渡必须从当前眼睑姿态开始");
  assert.equal(first.ParamEyeHighlight, outgoing.ParamEyeHighlight, "高光不应在切表情时跳到新目标");
  assert.equal(driver.arousal, arousalBefore, "表情切换不应重置连续情感状态");
  driver.update(1 / 60);
  assert.ok(Math.abs(driver.shift().ParamEyeLOpen - outgoing.ParamEyeLOpen) < 0.025,
    "切换后首帧眼睑变化应连续");
}

{
  const driver = new EmotionDriver({
    base: { shift: {} },
    side: { shift: { ParamEyeBallX: 0.6 } },
    brow: { shift: { ParamBrowLY: 0.5 } },
  });
  driver.set("base");
  settle(driver);
  driver.setMatchingTarget("side");
  const outgoing = driver.shift().ParamEyeBallX;
  driver.set("brow");
  assert.equal(driver.shift().ParamEyeBallX, outgoing,
    "匹配目标与 current 不同时应从实际呈现的目标过渡");
  assert.equal(driver.ownsGaze, true, "实际淡出的匹配目标应持有过渡视线");
}

{
  const driver = new EmotionDriver({
    base: { arousal: 0.25, shift: {} },
    happy: { blend: 0.4, arousal: 1, shift: { ParamEyeLOpen: 0.7, ParamEyeLSmile: 0.6 } },
    serious: { blend: 0.4, arousal: 1, shift: { ParamBrowLForm: 0.6 } },
  });
  driver.set("happy");
  settle(driver);
  driver.set("serious");
  assert.equal(driver.ownsGaze, false, "仅眼睑、笑眼和眉形不能接管注视");
  for (let i = 0; i < 24; i++) {
    driver.update(1 / 60);
    assert.ok(commitExpression(driver).ParamEyeLOpen >= 0.65, "缺失的出场眼睑应回到中位，而非闭眼目标 0");
  }
  settle(driver);
  assert.equal(commitExpression(driver).ParamEyeLOpen, 1);
}

{
  const driver = new EmotionDriver({
    base: { shift: {} },
    side: { blend: 0.5, shift: { ParamEyeBallX: 0.5 } },
    brow: { blend: 0.5, shift: { ParamBrowLY: 0.5 } },
    ambient: { blend: 0.5, owns_gaze: false, shift: { ParamEyeBallY: -0.1 } },
  });
  driver.set("side");
  assert.equal(driver.ownsGaze, true, "带注视目标的表达应在淡入期间持有视线");
  settle(driver);
  assert.equal(driver.ownsGaze, false, "完成过渡后应释放注视锁");
  driver.set("brow");
  assert.equal(driver.ownsGaze, true, "淡出中的旧注视目标仍应平滑过渡");
  settle(driver);
  driver.set("ambient");
  assert.equal(driver.ownsGaze, false, "配置可让轻微视线偏移不独占注视");
}

{
  const store = new ParamStore();
  const rig = new Rigger();
  rig.register("expression", 20);
  rig.set("expression", "ParamPupil", 1.45);
  rig.set("expression", "ParamEyeLOpen", 1.15);
  rig.set("expression", "ParamEyeROpen", -3);
  rig.set("expression", "ParamEyeHighlight", 8);
  rig.set("expression", "ParamEyeLSmile", -8);
  rig.set("expression", "ParamCheek", -8);
  rig.set("expression", "ParamMouthOpenY", -8);
  rig.set("expression", "ParamBreath", -0.6);
  rig.set("expression", "ParamMouthRound", -0.7);
  rig.commit(store);
  assert.equal(store.target.ParamPupil, 1.45, "瞳孔倍率不能截到 1");
  assert.equal(store.target.ParamEyeLOpen, 1.15, "允许略微睁大眼睛");
  assert.equal(store.target.ParamEyeROpen, 0);
  assert.equal(store.target.ParamEyeHighlight, 1);
  for (const key of ["ParamEyeLSmile", "ParamCheek", "ParamMouthOpenY"]) assert.equal(store.target[key], 0);
  assert.equal(store.target.ParamBreath, -0.6, "保留已有的有符号呼吸波形");
  assert.equal(store.target.ParamMouthRound, -0.7, "保留已有的有符号嘴型语义");
}

{
  const store = new ParamStore({ ParamEyeLOpen: NaN, ParamPupil: Infinity }, { ParamEyeLOpen: NaN });
  const rig = new Rigger();
  rig.register("expression", 20);
  for (const key of ALL_PARAMS) {
    assert.ok(Number.isFinite(clampParam(key, NaN)), `${key} 不能被 NaN 污染`);
    assert.ok(Number.isFinite(clampParam(key, Infinity)));
    rig.set("expression", key, NaN);
    rig.add("expression", key, Infinity);
  }
  rig.commit(store);
  store.blend(1 / 60);
  assert.equal(clamp(NaN), 0);
  assert.equal(store.current.ParamEyeLOpen, 1);
  for (const key of ALL_PARAMS) assert.ok(Number.isFinite(store.current[key]), `${key} 输出应有限`);
}

console.log("PASS  眼部中位、绝对目标强度、过渡注视 ownership、范围和有限值回归检查");
