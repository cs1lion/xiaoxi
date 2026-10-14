// Energy VAD, not a semantic/backchannel classifier. Feed fixed 20ms mono frames.
export class EnergyVad {
  constructor({ rate = 16000, pauseMs = 900, onsetMs = 150, playbackOnsetMs = 300,
    preRollMs = 250, minSpeechMs = 200, maxMs = 29500 } = {}) {
    Object.assign(this, { rate, pauseMs, onsetMs, playbackOnsetMs, preRollMs, minSpeechMs, maxMs });
    this.noise = 0.003;
    this.reset();
  }
  reset({ cooldown = false } = {}) {
    this.active = false; this.onset = 0; this.speech = 0; this.silence = 0;
    this.elapsed = 0; this.pre = []; this.preSamples = 0; this.cooldown = cooldown;
  }
  adopt(frames, speechMs) {
    this.reset();
    this.active = true;
    this.elapsed = frames.reduce((sum, frame) => sum + frame.length, 0) / this.rate * 1000;
    this.speech = Math.max(this.minSpeechMs, Math.min(this.elapsed, speechMs));
  }
  push(frame, playback = false) {
    const ms = frame.length / this.rate * 1000;
    const rms = Math.sqrt(frame.reduce((sum, x) => sum + x * x, 0) / Math.max(1, frame.length));
    const threshold = Math.max(playback ? 0.035 : 0.012, this.noise * (playback ? 5 : 3));
    const voiced = rms >= threshold;
    // Learn background only outside a candidate; cap prevents speech poisoning the floor.
    if (!this.active && !this.onset && !playback && !voiced) this.noise += 0.025 * (Math.min(rms, 0.012) - this.noise);
    if (this.cooldown) {
      this.silence = voiced ? 0 : this.silence + ms;
      if (this.silence >= this.pauseMs) this.reset();
      return { rms };
    }
    if (!this.active) {
      this.pre.push(frame); this.preSamples += frame.length;
      // Preserve candidate speech as well as 250ms before its onset.
      const keep = Math.ceil((this.preRollMs + (playback ? this.playbackOnsetMs : this.onsetMs)) * this.rate / 1000);
      while (this.pre.length > 1 && this.preSamples - this.pre[0].length >= keep) this.preSamples -= this.pre.shift().length;
      this.onset = voiced ? this.onset + ms : 0;
      if (this.onset < (playback ? this.playbackOnsetMs : this.onsetMs)) return { rms };
      this.active = true; this.speech = this.onset; this.elapsed = this.preSamples / this.rate * 1000;
      const frames = this.pre; this.pre = []; this.preSamples = 0;
      return { rms, start: true, frames };
    }
    this.elapsed += ms;
    this.speech += voiced ? ms : 0;
    this.silence = voiced ? 0 : this.silence + ms;
    const limit = this.elapsed >= this.maxMs;
    const end = limit || this.silence >= this.pauseMs;
    const valid = this.speech >= this.minSpeechMs;
    if (end) this.reset({ cooldown: limit });
    return { rms, frames: [frame], end, valid, limit };
  }
}
