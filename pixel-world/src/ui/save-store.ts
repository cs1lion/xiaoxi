/** 单槽存档 I/O：读/写/清 + 坏档备份 + 配额降级（ADR_SAVE §3/§4，final）。
 *
 * 只碰 localStorage 与本模块的类型；不 import Simulation，方便用假存储测试。
 */

import {
  assertSaveV1,
  CURRENT_SCHEMA_VERSION,
  migrateSave,
  SAVE_STORAGE_KEY,
  SCHEMA_V1_FINGERPRINT,
  SETTLED_WARN_THRESHOLD,
  saveFingerprint,
  type RawSave,
  type WorldSaveV1,
} from '../world/save.ts';

export interface StorageLike {
  readonly length: number;
  key(index: number): string | null;
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export type SaveReadResult =
  | { readonly ok: true; readonly save: WorldSaveV1 }
  | {
      readonly ok: false;
      readonly reason: 'empty' | 'corrupt' | 'version' | 'storage';
      readonly message: string;
      /** true = 不要写主存档（高版本只读 / 存储读失败）。 */
      readonly paused: boolean;
    };

export interface SaveWriteResult {
  readonly ok: boolean;
  readonly paused: boolean;
  readonly message: string;
}

export interface SaveStoreOptions {
  readonly storage?: StorageLike;
  readonly wallClock?: () => number;
  readonly onWarning?: (message: string) => void;
}

export const CORRUPT_PREFIX = `${SAVE_STORAGE_KEY}.corrupt.`;
/** 坏档备份只保留最近 3 份。 */
export const BACKUP_LIMIT = 3;

const STORAGE_HINT = '读不到本地存档：可能是来源(origin/端口)变了。';
const CORRUPT_MESSAGE = '存档损坏，已备份原档，用新世界启动。';
const QUOTA_MESSAGE = '本地存储已满：自动存档已暂停，世界继续运行。';

export class SaveStore {
  private readonly storage: StorageLike;
  private readonly wallClock: () => number;
  private readonly onWarning: (message: string) => void;
  private autoSave = true;
  private readonly warned = new Set<string>();

  constructor(options: SaveStoreOptions = {}) {
    this.storage = options.storage ?? (globalThis.localStorage as StorageLike);
    this.wallClock = options.wallClock ?? (() => Date.now());
    this.onWarning = options.onWarning ?? (() => {});
  }

  isAutoSaveEnabled(): boolean {
    return this.autoSave;
  }

  enableAutoSave(): void {
    this.autoSave = true;
  }

  disableAutoSave(): void {
    this.autoSave = false;
  }

  /** 读档；任何异常都降级为可读结果，不抛。 */
  read(): SaveReadResult {
    let text: string | null;
    try {
      text = this.storage.getItem(SAVE_STORAGE_KEY);
    } catch {
      this.autoSave = false;
      this.warnOnce('storage', STORAGE_HINT);
      return { ok: false, reason: 'storage', message: STORAGE_HINT, paused: true };
    }
    if (text === null) return { ok: false, reason: 'empty', message: '没有存档。', paused: false };

    let raw: RawSave;
    try {
      const parsed: unknown = JSON.parse(text);
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        throw new Error('存档不是对象');
      }
      raw = parsed as RawSave;
    } catch {
      this.backupCorrupt(text);
      this.warnOnce('corrupt', CORRUPT_MESSAGE);
      return { ok: false, reason: 'corrupt', message: CORRUPT_MESSAGE, paused: false };
    }

    const version = typeof raw.schemaVersion === 'number' ? raw.schemaVersion : 0;
    if (version > CURRENT_SCHEMA_VERSION) {
      this.autoSave = false;
      const message = `存档版本 v${version} 高于当前 v${CURRENT_SCHEMA_VERSION}：只读启动，自动存档已暂停。`;
      this.warnOnce('version', message);
      return { ok: false, reason: 'version', message, paused: true };
    }
    try {
      const migrated = migrateSave(raw);
      assertSaveV1(migrated);
      return { ok: true, save: migrated };
    } catch {
      this.backupCorrupt(text);
      this.warnOnce('corrupt', CORRUPT_MESSAGE);
      return { ok: false, reason: 'corrupt', message: CORRUPT_MESSAGE, paused: false };
    }
  }

  /** 写主存档；配额/安全异常 → 关自动存档、提示一次、不抛。 */
  write(save: WorldSaveV1): SaveWriteResult {
    if (!this.autoSave) {
      return { ok: false, paused: true, message: '自动存档已暂停。' };
    }
    if (save.world.settled.length > SETTLED_WARN_THRESHOLD) {
      this.warnOnce(
        'settled',
        `已结算记录超过 ${SETTLED_WARN_THRESHOLD} 条（${save.world.settled.length}），存档可能偏大。`,
      );
    }
    const text = JSON.stringify(save);
    if (!this.writeRaw(text)) {
      // 配额紧张：先删最旧备份，再重试主存档（备份不得拖垮主存档）。
      this.pruneOldestBackup();
      if (!this.writeRaw(text)) {
        this.autoSave = false;
        this.warnOnce('quota', QUOTA_MESSAGE);
        return { ok: false, paused: true, message: QUOTA_MESSAGE };
      }
    }
    return { ok: true, paused: false, message: '存档已写入。' };
  }

  clear(): void {
    try {
      this.storage.removeItem(SAVE_STORAGE_KEY);
    } catch {
      // 清档失败不抛；下次再清。
    }
  }

  /** 坏档原样备份，带时间戳，保留最近 3 份；备份失败不影响主存档。 */
  backupCorrupt(text: string): void {
    const key = `${CORRUPT_PREFIX}${this.wallClock()}`;
    try {
      this.storage.setItem(key, text);
    } catch {
      // 配额紧张：先腾掉最旧备份再试一次。
      this.pruneOldestBackup();
      try {
        this.storage.setItem(key, text);
      } catch {
        return;
      }
    }
    this.pruneBackups();
  }

  listBackups(): string[] {
    const keys: string[] = [];
    try {
      for (let index = 0; index < this.storage.length; index += 1) {
        const key = this.storage.key(index);
        if (key !== null && key.startsWith(CORRUPT_PREFIX)) keys.push(key);
      }
    } catch {
      return [];
    }
    return keys.sort(
      (a, b) => Number(a.slice(CORRUPT_PREFIX.length)) - Number(b.slice(CORRUPT_PREFIX.length)),
    );
  }

  private writeRaw(text: string): boolean {
    try {
      this.storage.setItem(SAVE_STORAGE_KEY, text);
      return true;
    } catch {
      return false;
    }
  }

  private pruneOldestBackup(): void {
    const backups = this.listBackups();
    const oldest = backups[0];
    if (oldest === undefined) return;
    try {
      this.storage.removeItem(oldest);
    } catch {
      // 删不掉就算了，交给上层降级。
    }
  }

  private pruneBackups(): void {
    const backups = this.listBackups();
    while (backups.length > BACKUP_LIMIT) {
      const oldest = backups.shift();
      if (oldest === undefined) return;
      try {
        this.storage.removeItem(oldest);
      } catch {
        return;
      }
    }
  }

  private warnOnce(key: string, message: string): void {
    if (this.warned.has(key)) return;
    this.warned.add(key);
    this.onWarning(message);
  }
}

export { SAVE_STORAGE_KEY, SCHEMA_V1_FINGERPRINT, saveFingerprint };
