// Play in order; decode only the current chunk and one chunk ahead.
// Native decodeAudioData cannot be cancelled, so stale decodes still occupy a slot.
export class PlaybackQueue {
  constructor({ decode, play, onIdle = () => {}, onError = () => {}, maxItems = 64,
    maxPending = maxItems, maxDecode = 2, maxAudioBytes = 12 * 1024 * 1024,
    maxEncodedBytes = 12 * 1024 * 1024, maxPendingBytes = 24 * 1024 * 1024,
    maxDecodedBytes = 24 * 1024 * 1024, decodeTimeoutMs = 15000,
    playTimeoutMs = 120000, timers = globalThis }) {
    if (!Number.isInteger(maxPending) || maxPending < 1 || !Number.isInteger(maxDecode) || maxDecode < 1) {
      throw new RangeError('播放队列上限必须是正整数');
    }
    Object.assign(this, { decode, play, onIdle, onError, maxItems: maxPending, maxPending,
      maxDecode, maxAudioBytes, maxEncodedBytes, maxPendingBytes, maxDecodedBytes,
      decodeTimeoutMs, playTimeoutMs, timers });
    this.generation = 0; this.items = []; this.running = false; this.cancelWait = null;
    this.current = null; this.entries = new Set(); this.decodeDemand = new Set();
    this.decodeSlots = new Set(); this.pendingBytes = 0;
  }
  get busy() { return this.running || this.items.length > 0; }
  inputBytes(item) {
    const data = item?.data ?? item;
    if (typeof data === 'string') {
      if (data.length > this.maxEncodedBytes) throw new Error('音频片段过大，已跳过');
      // data is base64 in the browser adapter; the estimate also bounds generic strings.
      return Math.ceil(data.length * 3 / 4);
    }
    return Number.isFinite(data?.byteLength) ? data.byteLength : 0;
  }
  enqueue(item) {
    let bytes;
    try { bytes = this.inputBytes(item); }
    catch (error) { this.onError(error); return false; }
    if (bytes > this.maxAudioBytes) { this.onError(new Error('音频片段过大，已跳过')); return false; }
    if (this.items.length >= this.maxPending || this.pendingBytes + bytes > this.maxPendingBytes) {
      this.onError(new Error('播放队列已满，已跳过音频')); return false;
    }
    const entry = { item, bytes, generation: this.generation, phase: 'queued' };
    this.entries.add(entry); this.pendingBytes += bytes; this.items.push(entry);
    if (!this.running) void this.drain(this.generation);
    else this.prefetch();
    return true;
  }
  release(entry) {
    if (!this.entries.delete(entry)) return;
    this.pendingBytes -= entry.bytes; entry.item = null; entry.result = null;
  }
  stop() {
    this.generation++; this.items = []; this.running = false; this.current = null;
    const cancel = this.cancelWait; this.cancelWait = null; cancel?.();
    for (const entry of this.entries) {
      entry.finish?.({ cancelled: true }); this.release(entry);
    }
  }
  prepare(entry) {
    if (entry.promise) return entry.promise;
    entry.promise = new Promise(resolve => {
      let settled = false;
      entry.finish = result => {
        if (settled) return;
        settled = true; this.timers.clearTimeout(entry.timer); this.decodeDemand.delete(entry);
        entry.result = result; entry.phase = 'decoded'; resolve(result);
      };
      // Waiting for a native decode slot is also bounded, including across stop().
      entry.timer = this.timers.setTimeout(() => entry.finish({ error: new Error('音频处理超时，已跳过') }), this.decodeTimeoutMs);
      this.decodeDemand.add(entry);
    });
    this.pumpDecodes();
    return entry.promise;
  }
  pumpDecodes() {
    for (const entry of this.decodeDemand) {
      if (this.decodeSlots.size >= this.maxDecode) break;
      this.decodeDemand.delete(entry);
      if (entry.generation !== this.generation) { entry.finish({ cancelled: true }); continue; }
      entry.phase = 'decoding';
      const slot = {}; this.decodeSlots.add(slot);
      let operation;
      try { operation = this.decode(entry.item); }
      catch (error) { operation = Promise.reject(error); }
      Promise.resolve(operation).then(buffer => {
        const bytes = buffer?.length * buffer?.numberOfChannels * 4;
        entry.finish(bytes > this.maxDecodedBytes
          ? { error: new Error('解码后的音频片段过大，已跳过') } : { buffer });
      }, error => entry.finish({ error })).finally(() => {
        this.decodeSlots.delete(slot); this.pumpDecodes();
      });
    }
  }
  prefetch() {
    if (this.current?.phase === 'playing' && this.items.length) this.prepare(this.items[0]);
  }
  wait(start, timeout) {
    return new Promise((resolve, reject) => {
      let settled = false, cleanup, cancelled = false, cleaned = false;
      const clean = () => { if (!cleaned && cleanup) { cleaned = true; try { cleanup(); } catch (_) {} } };
      const finish = (error, value, cancel = false) => {
        if (settled) return; settled = true; cancelled = cancel; this.timers.clearTimeout(timer);
        if (cancel) clean();
        if (error) reject(error); else resolve(value);
      };
      const timer = this.timers.setTimeout(() => finish(new Error('音频处理超时，已跳过'), undefined, true), timeout);
      this.cancelWait = () => finish(null, undefined, true);
      try { cleanup = start(value => finish(null, value), error => finish(error)); if (cancelled) clean(); }
      catch (error) { finish(error); }
    });
  }
  async drain(generation) {
    this.running = true;
    while (generation === this.generation && this.items.length) {
      const entry = this.items.shift(); this.current = entry;
      try {
        const result = await this.prepare(entry);
        if (generation !== this.generation) return;
        if (result.error) throw result.error;
        const { buffer } = result; const item = entry.item;
        entry.phase = 'playing';
        const remaining = Math.max(0, (buffer?.duration || 0) - (item?.offset || 0));
        await this.wait(done => {
          const cleanup = this.play(buffer, item, done); this.prefetch(); return cleanup;
        }, Math.min(this.playTimeoutMs, Math.max(5000, remaining * 1000 + 5000)));
      } catch (error) { if (generation === this.generation) this.onError(error); }
      if (generation !== this.generation) return;
      this.cancelWait = null; this.release(entry); this.current = null;
    }
    if (generation === this.generation) { this.running = false; this.onIdle(); }
  }
}
