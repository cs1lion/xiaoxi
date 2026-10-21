/** 天气：只认两种，切换要渐变，不能秒切。
 *
 * 世界法则（神谕说下雨）和自动退场都走这里。退场用时长而不是"下一拍就停"，
 * 否则雨幕会像开关灯一样跳。
 */

import type { Weather } from './types.ts';

/** 0..1 的浓淡，渲染层拿它插值雨幕。 */
export interface WeatherState {
  readonly kind: Weather;
  readonly intensity: number;
  /** 距离下一次自动变化还有多久；null 表示不会自己变。 */
  readonly remainingMs: number | null;
}

export const WEATHER_FADE_MS = 1800;
export const RAIN_DURATION_MS = 45_000;

export function clearWeather(): WeatherState {
  return { kind: 'clear', intensity: 0, remainingMs: null };
}

export function rainWeather(now: number): WeatherState {
  void now;
  return { kind: 'rain', intensity: 0, remainingMs: RAIN_DURATION_MS };
}

/** 推进浓淡与倒计时。天气自己到期就回到晴。 */
export function stepWeather(
  state: WeatherState,
  deltaMs: number,
): WeatherState {
  const target = state.kind === 'rain' ? 1 : 0;
  const step = deltaMs / WEATHER_FADE_MS;
  const intensity =
    state.intensity < target
      ? Math.min(target, state.intensity + step)
      : Math.max(target, state.intensity - step);

  if (state.remainingMs === null) return { ...state, intensity };

  const remaining = state.remainingMs - deltaMs;
  if (remaining > 0) return { ...state, intensity, remainingMs: remaining };
  return { kind: 'clear', intensity, remainingMs: null };
}

/** 渲染层判断"此刻还在下雨"用浓淡，不用 kind，避免刚转晴就立刻停雨。 */
export function isVisiblyRaining(state: WeatherState): boolean {
  return state.intensity > 0.05;
}
