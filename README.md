# 小溪 (Xiaoxi)
![Python 3.11+](//img.shields.io/badge/Python-3.11%2B-blue) ![FastAPI Async](//img.shields.io/badge/FastAPI-Async-green) ![Protocol MCP](//img.shields.io/badge/Protocol-MCP-orange) ![Loop System 1 & 2](//img.shields.io/badge/Loop-System%201%20%26%202-purple)
个人中文语音 Agent 系统，基于 **台前语音快环 (System 1)** 与 **幕后 Agent 慢环 (System 2)** 双环异步协作架构设计。

## 核心架构 (Architecture Overview)

### 快慢双环解耦

```
+-------------------------------------------------------------------+
|                     System 1 (台前语音快环)                        |
|  用户语音 -> 流式 ASR -> 对话管理 -> 分级记忆检索 -> 流式 TTS     |
+-------------------------------------------------------------------+
                                 | (识别复杂任务/派单)
                                 v
+-------------------------------------------------------------------+
|                     System 2 (幕后 Agent 慢环)                     |
|  Orchestrator -> 任务规划 -> Worker 异步执行 -> 遥测观测与自进化   |
+-------------------------------------------------------------------+
```

- **台前快环 (System 1)**：流式 ASR -> 对话管理 -> 分级记忆检索 -> 流式生成 -> 情感 TTS。快环无阻塞调用，识别到复杂委派立即派单，异步卸载到慢环。
- **幕后慢环 (System 2)**：Orchestrator-Workers 模式，任务规划 -> Worker 异步执行 -> 遥测观测。支持鬼打墙熔断保护（同一步骤签名连续重复 3 次即熔断）。
- **置信度分级记忆检索**：检索结果按【明确说过】、【推测】、【不确定】三档确定性装配 System Prompt，设置负向约束防止预训练常识盲目补全；支持记忆硬删除与 Lessons 自进化周期蒸馏。
- **遥测熔断与 L1/L2 评估闭环**：结合 L1（查真实回执）与 L2（查登记判例）双层确定性评估，自动将反馈闭环归纳注入 Prompt，打回上限 2 次。

## 核心模块结构 (Directory Structure)

- `server/`: 核心后端服务
  - `assistant/`: 助理引擎、Worker 编排、遥测熔断 (`telemetry.py`) 与评估器 (`evaluator.py`)
  - `memory/`: 分级记忆管理、Lessons 经验蒸馏 (`lessons.py`) 与 SQLite 索引
  - `persona.py`: Persona 约束与 System Prompt 配置
- `config/`: 配置文件与 Worker 派发规则 (`agents_roster.yaml`)
- `docs/`: 接口协议与架构规范 (`PROTOCOL.md`)

## 快速启动 (Quick Start)

```bash
# 部署与启动后端服务
cd server
pip install -r requirements.txt
python main.py
```
