# 模块级 `store = MemoryStore()` 影响面与改法（方案，未动手）

日期：2026-11-09 ｜ 基线：`fix/db-sentinel-no-real-write` @ `39198ea` + 本轮工作区根修复
状态：**只出方案，代码零改动**（用户要求「改错了会波及整个记忆层，先给方案」）。

## 1. 地雷本体

`server/memory/store.py:1724`

```python
store = MemoryStore()      # 模块导入即建库、即跑 _migrate()
```

`MemoryStore.__init__` 里做四件有副作用的事：`guard_sqlite_path` → `mkdir` 父目录 →
`sqlite3.connect` → `_migrate()`（含 `PRAGMA user_version = 5`）。所以

- **任何人 import `server.memory.store` 就会打开并可能升级真实 `data/memory.db`**，
  哪怕他只想拿 `Memory` 这个 dataclass 或 `SCHEMA_VERSION` 这个整数。
- 上一轮 `test_db_sentinel.py` 正是踩在这里：脚本需要版本号，`from server.memory.store
  import SCHEMA_VERSION` 一执行，真实库就被写成 v5。修复办法是"脚本干脆不 import
  server.*"，而不是让 import 变安全。
- `TEST_SENTINEL=1` 只是把它变成**报错**，不是变成"不打开"；没有哨兵的手工脚本
  （`python scripts/xxx.py`）依然直连真实库——这是本方案要解决的那一半。

派生单例同样是 eager 的：`manager.py:386 manager = MemoryManager()`、
`retriever.py:425 retriever = MemoryRetriever()`，两者构造时不传 store，走
`store or default_store` 拿到同一个实例。所以 `import server.memory.manager` 也会建库。

## 2. 影响面（实测计数，基线树）

| 类别 | 数量 | 位置 |
| --- | --- | --- |
| 定义点 | 3 | `store.py:1724`、`manager.py:386`、`retriever.py:425` |
| **按名字**依赖单例 `store` 的调用方 | server 8 处 / 7 文件 | `app.py:34`、`orchestrator.py:1912`、`:1918`、`manager.py:17`、`retriever.py:17`、`diary.py:59`、`evolution.py:89`、`loop.py:805` |
| 同上（脚本侧） | 5 文件 | `compare_models.py`、`eval_scenarios.py`、`inspect_extract.py`、`run_evals.py`、`test_core.py`（均 `import store`） |
| **只要 import 该模块**就触发建库 | server 12 文件 / 16 处 | `app`、`loop`、`orchestrator`、`diary`、`evolution`、`feedback`、`lessons`、`manager`、`retriever`、`seeds`、`snapshot`、`starmap` |
| 同上（脚本侧） | 44 文件 | 记忆/评测/探针/门禁脚本为主（`server.memory.store` 出现即算） |
| 依赖 `manager` / `retriever` 单例 | server 2 文件 + 脚本 6 文件 | `app.py:33`、`orchestrator.py:292`；`e2e_check`、`eval_scenarios`、`inspect_extract`、`run_evals`、`test_core`、`test_memory_fastloop` |
| `isinstance(x, MemoryStore)` 断言 | **0 处** | 这一项让"代理对象"方案少一个致命伤 |

结论：**"只 import 就建库"的暴露面是 12 个 server 文件 + 44 个脚本**；真正按名字取
单例的只有 8 + 5 处。所以「改成惰性是否需要动调用方」取决于选哪个方案。

## 3. 三个方案

### A. 惰性单例（PEP 562 模块 `__getattr__`）—— **推荐，零调用方改动**

```python
# store.py 尾部
_default: MemoryStore | None = None
_lock = threading.Lock()

def get_store() -> MemoryStore:
    global _default
    if _default is None:
        with _lock:
            if _default is None:
                _default = MemoryStore()
    return _default

def __getattr__(name):
    if name == "store":
        return get_store()
    raise AttributeError(name)
```

- `from server.memory.store import store` **照旧能用**：PEP 562 明确 `from ... import name`
  会回落到模块 `__getattr__`。`import store as default_store` 同理。
- 必须一起惰性化的还有 `manager.py:386` / `retriever.py:425` 两个派生单例（同样的
  `__getattr__`），否则 import `manager` 仍然建库；并顺手把 `manager.py:17` /
  `retriever.py:17` 顶层的 `store as default_store` 改到 `__init__` 里 `get_store()`。
- 唯一还会 eager 的是 `app.py:34` 的顶层 `from .memory.store import store`（服务进程
  本来就该建库，生产零变化；要连它也惰性，就把那行挪进使用点，1 处）。
- **改 3 个文件内部 + 可选 1 处挪行；调用方 0 改动**（那 8 + 5 处按名字取单例的地方原样跑）。
- 代价 / 风险：`__getattr__` 只在属性缺失时触发，若有人真的 `store_module.store = Fake`
  赋值仍可（赋值直接写模块字典，不经过 `__getattr__`）；需要给测试一个
  `reset_default_store()`；线程安全靠一把锁（`MemoryStore` 自己已有 `RLock`）。
- 收益：**import 零副作用**——拿 `Memory`/`SCHEMA_VERSION` 的工具不再升级真实库；
  这正是上一轮 `test_db_sentinel` 事故的根因。

### B. 代理对象（`store = _LazyStore()`，首次属性访问再实例化）

- 调用方 0 改动，连 `app.py:34` 都不用碰。
- 代价：`type()`/`isinstance()`/`repr`/序列化语义全变（本项目实测 0 处 isinstance，
  所以短期不会炸，但这是**隐性债务**）；`manager.py:56` 的 `store or default_store`
  依赖代理的 `__bool__`；真出问题时栈里多一层代理，排查成本高。
- 而且 `manager`/`retriever` 单例也得各做一个代理，等于把 A 的锁 + 门面再实现两遍。

### C. 显式注入（删掉所有模块级单例）

- 最干净：没有隐式全局，测试天然隔离，也不依赖哨兵兜底。
- 代价：**必须动调用方**。构造点 `manager.py:386`、`retriever.py:425`、`app.py`、
  `orchestrator.py:292/1912/1918`、`diary.py:59`、`evolution.py:89`、`loop.py:805`
  + 44 个脚本（其中多数只是 `from server.memory.store import Memory`，本不需要改，
  却会被"模块不再导出单例"的语义变化扫到）。
- 适合作为 A 之后的第二阶段（A 已让 import 无害，C 只是把隐式依赖显式化），
  单独一次做会一次改动 50+ 处，且无法靠一个门禁证明"没有漏改的隐式依赖"。

## 4. 无论选哪个，配套的先红用例

一条能跑出 pass/fail 的门禁（拟 `scripts/test_store_import_is_inert.py`）：

1. 无 `TEST_SENTINEL`、`DB_PATH=<临时>/probe.db`（不存在）的**子进程**里
   `import server.memory.store` 以及 `from server.memory.store import Memory, SCHEMA_VERSION`；
2. 断言 `probe.db` **没有被创建**、`user_version` 读不到（文件不存在），退出码 0；
3. 再断言显式 `get_store()`（或首次取 `store`）**才**创建该文件，且降到
   `SCHEMA_VERSION` —— 证明"惰性"没有把建库弄丢；
4. 反向用例：`TEST_SENTINEL=1` + 指向仓库 `data/` 的路径仍必须报错（哨兵不因惰性失效）。

在 A 方案下这条会先红（当前树第 2 步必然建文件），实现后转绿；B/C 同理。
