# 2026-11-12 · Fish Audio / 讯飞星火 / OpenRouter 能力探测

- 性质：只读探测。三个供应商的凭据来自用户当面提供，全部落在 `.env`（`.gitignore:15`，不入库）。
  **本目录不含任何真实凭据**（`fish_probe.py:144` 的 `sk-fish-invalid` 是错误路径测试用的假 key）。
- 结论文档：[`docs/FISH_XFYUN_CLOSED_LOOP_20261112.md`](../../FISH_XFYUN_CLOSED_LOOP_20261112.md)。
- 跑法：所有脚本从环境变量取凭据，不硬编码。

## 脚本

| 脚本 | 问什么 | 需要的 env |
| --- | --- | --- |
| `fish_probe.py` | Fish 基础能力、模型档位、情绪标签、prosody 生效性、逐字对齐、ASR、错误路径、余额 | `FISH_API_KEY` |
| `fish_persona_probe.py` | `persona.yaml` 的七档 `instruction` 直接放进方括号是否生效、确定性、长文本上限 | `FISH_API_KEY` |
| `fish_latency_profile.py` | 项目真实分片尺寸（14/26/45/60 字）下的延迟画像 × `latency` 三档 | `FISH_API_KEY` |
| `xfyun_contract_probe.py` | 讯飞端点与项目 `server/llm.py` 契约逐字段对齐、思考模式开关、抽取契约、错误路径 | `XF_KEY` |
| `xfyun_budget_probe.py` | 讯飞在项目默认 400 token 预算下会不会「思考吃满 → content 空」 | `XF_KEY` |
| `openrouter_contract_probe.py` | OpenRouter 契约对齐、流式、余额、免费路由落到哪些模型 | `OR_KEY` |
| `openrouter_models_probe.py` | 13 个免费模型逐个测人格 prompt，看谁能返回非空 content | `OR_KEY` |
| `verify_plan_citations.py` | 校验方案文档的每条 `file:line` 引用 | 无 |

## 跑法

```powershell
# 用 .env 里的凭据（脚本不读 .env，自己设 env）
$env:FISH_API_KEY = "..."; $env:XF_KEY = "..."; $env:OR_KEY = "..."
.\.venv\Scripts\python.exe docs\evidence\20261112-fish-xfyun-probe\fish_probe.py
.\.venv\Scripts\python.exe docs\evidence\20261112-fish-xfyun-probe\verify_plan_citations.py
```

各脚本的原始读数落在 `%TEMP%`：`fish_probe_results.json`、`fish_persona_probe.json`、
`fish_latency_profile.json`、`xfyun_contract.json`、`xfyun_budget.json`、
`openrouter_contract.json`、`openrouter_models_probe.json`。

## 三条最容易复发的结论

1. **Fish 的 `s2.1-pro-free` 是 $0/M UTF-8 bytes 的免费档**，但**无 TTFA 与 DPA 保证**，
   且**非确定性**（同文本同参数两次字节不同）。定位是开发与测试档。
2. **讯飞默认开思考**，`reasoning_content` 会吃掉 token 预算。
   项目 `server/llm.py:110-113` 警告过的「content 空 + finish=length」在
   **OpenRouter 免费路由上实测复现**，讯飞上未复现但耗时翻倍。
3. **OpenRouter 的 `openrouter/free` 不适合当主路**：13 个免费模型里只有 4 个在人格 prompt 下
   稳定返回完整内容，其余 429 或被 reasoning 吃满预算。

## 顺带发现的环境缺陷

本机 `faster-whisper 1.2.1` + `av 19.0.2` 组合下，`WhisperModel.transcribe("文件路径")`
会抛 `TypeError: open() got an unexpected keyword argument 'metadata_errors'`。
项目当前传 numpy 数组（`server/speech/asr.py:136`）不受影响，但 `requirements.txt` 只钉了
`faster-whisper>=1.0.3`、`av` 完全没钉。详见方案文档 §4.1。
