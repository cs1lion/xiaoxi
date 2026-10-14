const GLOBAL_TYPES = new Set(['ready', 'pong', 'config', 'config_ack']);
export class TurnGate {
  constructor() { this.id = null; this.done = false; this.retired = new Set(); }
  begin(id) { this.clear(); this.id = id; this.done = false; }
  retire(id) {
    this.retired.add(id);
    if (this.retired.size > 256) this.retired.delete(this.retired.values().next().value);
  }
  clear() {
    if (this.id) this.retire(this.id);
    this.id = null; this.done = false;
  }
  accept(msg, canAcceptProactive = false) {
    if (msg.type === 'turn_started') {
      if (!msg.turn_id || this.retired.has(msg.turn_id)) return false;
      if (msg.turn_id === this.id) return true;
      if (!this.id && canAcceptProactive) { this.begin(msg.turn_id); return true; }
      this.retire(msg.turn_id);
      return false;
    }
    if (msg.turn_id != null) return msg.turn_id === this.id;
    return GLOBAL_TYPES.has(msg.type) || (msg.type === 'error' && !this.id);
  }
}
