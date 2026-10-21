/** 哇啦哇啦：把文字变成一串短促方波，不依赖任何音频素材。
 *
 * 用 Web Audio 现场合成：每个字符一个 40ms 左右的音节，音高按字符码哈希偏移。
 * 不读 TTS、不联网、没有可访问性风险——所有"说过的话"同时也以文字出现在 DOM 上。
 */

export interface Animalese {
  readonly speak: (text: string) => void;
  readonly stop: () => void;
  readonly dispose: () => void;
}

const PITCH_BASE = 440;
const SYLLABLE_SECONDS = 0.042;

/** 可测试的音节表：同一句话永远生成同一串音高。 */
export function syllablePlan(text: string): readonly number[] {
  const plan: number[] = [];
  for (const char of text) {
    const code = char.codePointAt(0) ?? 0;
    if (code <= 32) continue;
    // 标点不发音，给个短停顿。
    if (isPunctuation(char)) {
      plan.push(0);
      continue;
    }
    const offset = ((code * 2654435761) % 1000) / 1000;
    plan.push(0.85 + offset * 0.4);
  }
  return plan;
}

function isPunctuation(char: string): boolean {
  return '，。！？、；：,.!?;:'.includes(char);
}

export function createAnimalese(context: AudioContext): Animalese {
  let active: OscillatorNode | null = null;

  const stop = (): void => {
    if (active === null) return;
    try {
      active.stop();
    } catch {
      // 已经停过，忽略。
    }
    active = null;
  };

  return {
    speak(text) {
      stop();
      if (text.length === 0) return;
      const plan = syllablePlan(text);
      const gain = context.createGain();
      gain.gain.value = 0.045;
      gain.connect(context.destination);

      let at = context.currentTime;
      const osc = context.createOscillator();
      osc.type = 'square';
      osc.connect(gain);
      for (const step of plan) {
        if (step === 0) {
          at += SYLLABLE_SECONDS;
          continue;
        }
        osc.frequency.setValueAtTime(PITCH_BASE * step, at);
        at += SYLLABLE_SECONDS;
      }
      osc.start(context.currentTime);
      osc.stop(at + 0.01);
      active = osc;
      osc.onended = () => {
        if (active === osc) active = null;
        gain.disconnect();
      };
    },
    stop,
    dispose() {
      stop();
      void context.close();
    },
  };
}

/** 浏览器不允许未交互就开音频；第一次点击时再建。 */
export function createLazyAnimalese(): Animalese {
  let context: AudioContext | null = null;
  let impl: Animalese | null = null;
  const ensure = (): Animalese | null => {
    if (impl !== null) return impl;
    const Ctor = window.AudioContext;
    if (Ctor === undefined) return null;
    context = new Ctor();
    impl = createAnimalese(context);
    return impl;
  };
  return {
    speak(text) {
      ensure()?.speak(text);
    },
    stop() {
      impl?.stop();
    },
    dispose() {
      impl?.dispose();
      context = null;
      impl = null;
    },
  };
}
