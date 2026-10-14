// Local audio-only continuation. Never replays tools or infers text/audio alignment.
export class PlaybackCheckpoint {
  constructor({ ttlMs = 90000, minRemaining = .15, clock = () => performance.now(), onExpire = () => {} } = {}) {
    Object.assign(this, { ttlMs, minRemaining, clock, onExpire }); this.saved = null; this.timer = null;
  }
  save({ buffer, offset = 0, text = '', emotion = '' }) {
    this.clear();
    if (!buffer || !Number.isFinite(buffer.duration) || !Number.isFinite(offset)) return false;
    offset = Math.max(0, Math.min(buffer.duration, offset));
    if (buffer.duration - offset < this.minRemaining) return false;
    this.saved = { buffer, offset, text, emotion, expires: this.clock() + this.ttlMs };
    this.timer = setTimeout(() => { this.clear(); this.onExpire(); }, this.ttlMs);
    this.timer.unref?.();
    return true;
  }
  get available() {
    if (this.saved && this.clock() >= this.saved.expires) this.clear();
    return !!this.saved;
  }
  take() {
    if (!this.available) return null;
    const value = this.saved; this.clear(); return value;
  }
  clear() { clearTimeout(this.timer); this.timer = null; this.saved = null; }
}

export class VoiceMetrics {
  constructor(limit = 50) { this.limit = limit; this.reset(); }
  reset() { this.counts = Object.create(null); this.timings = Object.create(null); }
  count(name) { this.counts[name] = (this.counts[name] || 0) + 1; }
  timing(name, value) {
    if (!Number.isFinite(value) || value < 0) return;
    const list = this.timings[name] ||= []; list.push(Math.round(value));
    if (list.length > this.limit) list.shift();
  }
  snapshot() {
    const summary = {};
    for (const [name, list] of Object.entries(this.timings)) {
      const sorted = [...list].sort((a,b) => a-b);
      summary[name] = { n: sorted.length, p50: sorted[Math.ceil(sorted.length*.5)-1], p95: sorted[Math.ceil(sorted.length*.95)-1] };
    }
    return { counts: {...this.counts}, timings: summary };
  }
}
