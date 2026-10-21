# Decision / ADR Log

重大决策用一项一文件或追加到 `docs/DECISIONS.md`，最少记录：编号、日期、状态（proposed/accepted/deprecated）、背景、选项、决定、理由、影响、验证证据。

已记录的设计方向（需核验实际仓库）：
- Phaser 3 + TypeScript + Vite。
- 轻量数据层/系统函数，不使用重型 ECS。
- 像素完美优先，首期伪光照。
- LLM 只提供结构化高层意图；由程序验证和应用。
- Mock-first；真实 Agent 接入后置。
- 用户最新明确：无语音陪伴/TTS；语音仅作为用户输入方式。MVP 1.0 当前按源文档先不包含语音输入。
