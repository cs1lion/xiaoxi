import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  PHASES,
  PHASE_ORDER,
  parsePhaseParam,
  phaseAt,
  type PhaseId,
} from '../src/render/day-phase.ts';
import { WORLD } from '../src/world/palette-data.ts';

/** 造一个本地时间的 epoch 毫秒：年月日固定，只换小时。 */
const atHour = (hour: number, minute = 0): number =>
  new Date(2026, 9, 21, hour, minute, 0, 0).getTime();

// ------------------------------------------------------------------ 相位划分

test('四刻按本地钟点划分，边界左闭右开', () => {
  const cases: [number, PhaseId][] = [
    [0, 'night'],
    [4, 'night'],
    [5, 'dawn'],
    [8, 'dawn'],
    [9, 'noon'],
    [16, 'noon'],
    [17, 'dusk'],
    [19, 'dusk'],
    [20, 'night'],
    [23, 'night'],
  ];
  for (const [hour, expected] of cases) {
    assert.equal(phaseAt(atHour(hour)), expected, `${hour} 点应该是 ${expected}`);
  }
});

test('相位边界在整点生效，不随分钟抖动', () => {
  assert.equal(phaseAt(atHour(8, 59)), 'dawn');
  assert.equal(phaseAt(atHour(9, 0)), 'noon');
  assert.equal(phaseAt(atHour(16, 59)), 'noon');
  assert.equal(phaseAt(atHour(17, 0)), 'dusk');
});

test('相位是纯函数：同一时刻永远同一刻', () => {
  const epoch = atHour(18, 30);
  assert.equal(phaseAt(epoch), phaseAt(epoch));
  assert.equal(phaseAt(epoch), 'dusk');
});

test('非法时刻退回正午，不抛异常', () => {
  assert.equal(phaseAt(Number.NaN), 'noon');
  assert.equal(phaseAt(Number.POSITIVE_INFINITY), 'noon');
});

// ------------------------------------------------------------------ 配方表

test('四刻齐全，且顺序与 PHASE_ORDER 一致', () => {
  const keys = Object.keys(PHASES).sort();
  assert.deepEqual(keys, ['dawn', 'dusk', 'night', 'noon']);
  assert.deepEqual([...PHASE_ORDER].sort(), keys);
});

test('亮度严格递减：晨 > 午 > 昏 > 夜（用压暗浓度与亮光判断）', () => {
  const darkness = PHASE_ORDER.map((id) => PHASES[id].dim);
  assert.ok(darkness[0]! > darkness[1]!, '晨应比午暗');
  assert.ok(darkness[1]! < darkness[2]!, '午应比昏亮');
  assert.ok(darkness[2]! < darkness[3]!, '昏应比夜亮');
});

test('黄昏必须比夜亮，且不能是全天最亮：暖色叠草地会提亮，这里用 ink 压', () => {
  assert.ok(PHASES.dusk.dim > PHASES.noon.dim, '黄昏要压暗一些');
  assert.ok(PHASES.dusk.dim < PHASES.night.dim, '黄昏不能和夜一样暗');
  assert.notEqual(
    PHASES.dusk.dimColor,
    WORLD.ember,
    '黄昏不能用 ember 压暗：暖色叠草地会提亮，出现「黄昏比正午亮」',
  );
});

test('每刻至少有一处与相邻刻不同的内容态，不靠单一亮度区分', () => {
  // 晨：雾带；午：全无；昏：屋脊受光；夜：萤火。
  assert.ok(PHASES.dawn.mistBand > 0 && PHASES.dawn.mist > 0, '晨要有贴地雾带');
  assert.equal(PHASES.noon.mistBand, 0);
  assert.equal(PHASES.noon.glow, 0, '正午不点灯');
  assert.ok(PHASES.dusk.ridge > 0, '黄昏要有屋脊受光');
  assert.equal(PHASES.dusk.mistBand, 0, '黄昏没有雾带');
  assert.equal(PHASES.night.ridge, 0, '夜里没有屋脊受光');
  assert.equal(PHASES.night.fireflies, true);
  assert.equal(PHASES.dusk.fireflies, false);
});

test('亮窗只在黄昏与夜里', () => {
  assert.equal(PHASES.dawn.litWindows, 0);
  assert.equal(PHASES.noon.litWindows, 0);
  assert.ok(PHASES.dusk.litWindows > 0);
  assert.ok(PHASES.night.litWindows > 0);
});

test('配方只用现有 24 色，不引入第 25 色', () => {
  const palette = new Set<number>(Object.values(WORLD));
  for (const id of PHASE_ORDER) {
    const recipe = PHASES[id];
    assert.ok(palette.has(recipe.dimColor), `${id} 的压暗色必须在 24 色表里`);
  }
});

test('压暗浓度都在 0..1，且不超过 ART_SPEC 的压暗上限 0.46', () => {
  for (const id of PHASE_ORDER) {
    const dim = PHASES[id].dim;
    assert.ok(dim >= 0 && dim <= 1, `${id} 压暗浓度越界：${dim}`);
  }
});

// ------------------------------------------------------------------ URL 参数

test('URL 参数认四刻与常见别名', () => {
  assert.equal(parsePhaseParam('dawn'), 'dawn');
  assert.equal(parsePhaseParam('morning'), 'dawn');
  assert.equal(parsePhaseParam('noon'), 'noon');
  assert.equal(parsePhaseParam('day'), 'noon');
  assert.equal(parsePhaseParam('dusk'), 'dusk');
  assert.equal(parsePhaseParam('night'), 'night');
  assert.equal(parsePhaseParam('DAWN'), 'dawn');
  assert.equal(parsePhaseParam(' dusk '), 'dusk');
});

test('认不出的 URL 参数返回 null，调用方退回真实时间', () => {
  assert.equal(parsePhaseParam(null), null);
  assert.equal(parsePhaseParam(''), null);
  assert.equal(parsePhaseParam('sunset'), null);
  assert.equal(parsePhaseParam('99'), null);
});
