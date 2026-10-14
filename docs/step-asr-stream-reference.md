> ## Documentation Index
> Fetch the complete documentation index at: https://platform.stepfun.com/docs/llms.txt
> Use this file to discover all available pages before exploring further.

# 流式语音识别（双向流式）

基于 WebSocket 的双向流式语音识别：实时发送音频分片并持续接收增量与最终识别结果，支持服务端 VAD 语音活动检测，适合实时对话、语音助手、会议字幕等场景。推荐使用模型 `stepaudio-2.5-asr-stream`。

<Info>
  如需基于 HTTP + SSE 一次性提交音频并接收流式识别结果，请参考 [语音识别（流式返回文本）](/docs/zh/api-reference/audio/asr-sse)。
</Info>

## 服务地址

通过 WebSocket 连接：`wss://api.stepfun.com/v1/realtime/asr/stream`

## 鉴权

* `Authorization` `string` ***required***<br />认证令牌，格式为 `Bearer $STEPFUN_API_KEY`。

## 客户端消息

### 会话更新（session.update）

更新会话配置，包括音频格式、识别参数等。

```json theme={"theme":{"light":"light-plus","dark":"dark-plus"}}
{
  "event_id": "event_123",
  "type": "session.update",
  "session": {
    "audio": {
      "input": {
        "format": {
          "type": "pcm",
          "codec": "pcm_s16le",
          "rate": 16000,
          "bits": 16,
          "channel": 1
        },
        "transcription": {
          "model": "stepaudio-2.5-asr-stream",
          "language": "zh",
          "prompt": "请记录下你所听到的语音内容。",
          "full_rerun_on_commit": true,
          "enable_itn": true
        },
        "turn_detection": {
          "type": "server_vad",
          "silence_duration_ms": 800,
          "threshold": 0.6
        }
      }
    }
  }
}
```

* `event_id` `string` ***required***<br />当前消息事件 ID。

* `type` `string` ***required***<br />消息类型，固定为 `session.update`。

* `session.audio.input.format` `object`<br />音频格式。

  <Expandable>
    * `type` `string`<br />音频容器格式，支持 `pcm`、`ogg`。
    * `codec` `string`<br />音频编码格式；当 `type=pcm` 时传 `pcm_s16le`。
    * `rate` `integer`<br />音频采样率。
    * `bits` `integer`<br />音频位深。
    * `channel` `integer`<br />音频通道数。
  </Expandable>

* `session.audio.input.transcription` `object`<br />识别配置。

  <Expandable>
    * `model` `string`<br />转录模型名称，支持 `stepaudio-2.5-asr-stream`（推荐）、`step-asr-1.1-stream`（逐步废弃）。
    * `language` `string`<br />识别语言，如 `zh`。
    * `prompt` `string`<br />提示词，用于补充上下文或专业术语。
    * `full_rerun_on_commit` `boolean`<br />是否在提交后进行二遍识别纠错，默认 `false`。
    * `enable_itn` `boolean`<br />是否开启 ITN 文本规范化。
    * `enable_timestamp_align` `boolean`<br />仅 `stepaudio-2.5-asr-stream` 生效。是否开启字级时间戳对齐，默认 `false`；开启后 `completed` 事件的 `words` 返回更精确的字级时间戳。
  </Expandable>

* `session.audio.input.turn_detection` `object`<br />语音活动检测（VAD）配置。

  <Expandable>
    * `type` `string`<br />检测类型，支持 `server_vad`。
    * `silence_duration_ms` `integer`<br />静音阈值，超过该时长后认为一句话结束。
    * `threshold` `number`<br />VAD 检测阈值，值越大越严格。
  </Expandable>

补充说明：

* 当前支持 `stepaudio-2.5-asr-stream`、`step-asr-1.1-stream`。
* 如果不传 `turn_detection.type=server_vad`，服务端不会自动做 VAD，此时客户端需主动发送 `input_audio_buffer.commit`。

### 追加音频（input\_audio\_buffer.append）

发送音频数据进行实时识别。

```json theme={"theme":{"light":"light-plus","dark":"dark-plus"}}
{
  "event_id": "event_124",
  "type": "input_audio_buffer.append",
  "audio": "base64_encoded_audio_data"
}
```

* `event_id` `string` ***required***<br />事件唯一标识。
* `audio` `string` ***required***<br />base64 编码的音频数据（WAV 格式）。

### 提交缓冲区（input\_audio\_buffer.commit）

仅在关闭 `server_vad` 时需要发送该消息，要求服务端提交音频缓冲区、触发转录处理。

```json theme={"theme":{"light":"light-plus","dark":"dark-plus"}}
{
  "event_id": "event_125",
  "type": "input_audio_buffer.commit"
}
```

* `event_id` `string` ***required***<br />事件唯一标识。

## 服务端消息

### 会话创建确认（session.created）

确认会话创建成功。

```json theme={"theme":{"light":"light-plus","dark":"dark-plus"}}
{
    "event_id": "319612d9-aede-4456-a1fd-bb9c21493cd9",
    "type": "session.created",
    "meta": {
        "session_id": "test_trace_1766470129914296764",
        "timestamp": 1766468589040
    },
    "session": {
        "audio": {
            "input": {
                "transcription": {
                    "language": "zh"
                },
                "format": {
                    "type": "pcm",
                    "codec": "pcm_s16le",
                    "rate": 16000,
                    "bits": 16,
                    "channel": 1
                },
                "turn_detection": {
                    "type": "server_vad",
                    "silence_duration_ms": 800
                }
            }
        }
    }
}
```

### 会话更新确认（session.updated）

确认会话配置更新成功。

```json theme={"theme":{"light":"light-plus","dark":"dark-plus"}}
{
    "event_id": "319612d9-aede-4456-a1fd-bb9c21493cd9",
    "type": "session.updated",
    "meta": {
        "session_id": "test_trace_1766470129914296764",
        "timestamp": 1766468589040
    },
    "session": {
        "audio": {
            "input": {
                "transcription": {
                    "language": "zh"
                },
                "format": {
                    "type": "pcm",
                    "codec": "pcm_s16le",
                    "rate": 16000,
                    "bits": 16,
                    "channel": 1
                },
                "turn_detection": {
                    "type": "server_vad",
                    "silence_duration_ms": 800
                }
            }
        }
    }
}
```

### 语音开始（input\_audio\_buffer.speech\_started）

仅在开启 `server_vad` 时返回，表示服务端检测到语音开始。

```json theme={"theme":{"light":"light-plus","dark":"dark-plus"}}
{
    "event_id": "319612d9-aede-4456-a1fd-bb9c21493cd9",
    "type": "input_audio_buffer.speech_started",
    "meta": {
        "session_id": "test_trace_1766470129914296764",
        "timestamp": 1766470145337
    },
    "audio_start_ms": 15240,
    "item_id": "item_26cbd6a7-16e8-44c9-b6b4-d4d3b3c87b41"
}
```

### 语音结束（input\_audio\_buffer.speech\_stopped）

仅在开启 `server_vad` 时返回，表示服务端检测到语音结束。

```json theme={"theme":{"light":"light-plus","dark":"dark-plus"}}
{
    "event_id": "406d719a-3392-4ca4-88b3-9f12d8189b74",
    "type": "input_audio_buffer.speech_stopped",
    "meta": {
        "session_id": "test_trace_1766470129914296764",
        "timestamp": 1766470143512
    },
    "audio_start_ms": 13480,
    "item_id": "item_8228b4a6-f127-4a60-8f33-20fcb7d9ff7d"
}
```

### 缓冲区提交确认（input\_audio\_buffer.committed）

确认音频缓冲区已提交。

```json theme={"theme":{"light":"light-plus","dark":"dark-plus"}}
{
  "event_id": "event_xxx",
  "type": "input_audio_buffer.committed",
  "meta": {
    "session_id": "sess_xxx",
    "timestamp": 1642694400000
  },
  "item_id": "item_xxx",
  "previous_item_id": "item_yyy"
}
```

### 对话项创建（conversation.item.created）

新的对话项（转录结果）已创建。

```json theme={"theme":{"light":"light-plus","dark":"dark-plus"}}
{
  "event_id": "event_xxx",
  "type": "conversation.item.created",
  "meta": {
    "session_id": "sess_xxx",
    "timestamp": 1642694400000
  },
  "previous_item_id": "item_yyy",
  "item": {
    "id": "item_xxx",
    "object": "realtime.item",
    "type": "message",
    "status": "in_progress",
    "role": "user",
    "content": [
      {
        "type": "input_audio"
      }
    ]
  }
}
```

### 转录增量（conversation.item.input\_audio\_transcription.delta）

返回转录增量结果（流式输出）。

#### `stepaudio-2.5-asr-stream`（推荐）

```json theme={"theme":{"light":"light-plus","dark":"dark-plus"}}
{
  "event_id": "event_xxx",
  "type": "conversation.item.input_audio_transcription.delta",
  "meta": {
    "session_id": "sess_xxx",
    "timestamp": 1642694400000
  },
  "item_id": "item_xxx",
  "content_index": 0,
  "text": "你好请问有什么可以帮助您的",
  "stash": "退款",
  "words": [
    { "word": "你", "start": 0.00, "end": 0.20, "final": true },
    { "word": "好", "start": 0.20, "end": 0.45, "final": true },
    { "word": "退", "start": 1.80, "end": 1.95, "final": false },
    { "word": "款", "start": 1.95, "end": 2.10, "final": false }
  ]
}
```

* `item_id` `string`<br />对话项 ID。
* `content_index` `int`<br />内容索引。
* `text` `string`<br />截止当前的累计全量文本（含对前文的纠错）。客户端应整体替换展示，不要再追加拼接。
* `stash` `string`<br />可纠错的尾部文本（末尾若干字，后续可能被改写）。建议与 `text` 区分样式展示。
* `words` `list`<br />逐字时间数组，元素为 `{ "word", "start", "end", "final" }`。`start` / `end` 单位为秒（float）；`final=false` 表示该字在 `stash` 区、时间为临时估计。

#### `step-asr-1.1-stream`（逐步废弃）

```json theme={"theme":{"light":"light-plus","dark":"dark-plus"}}
{
  "event_id": "event_xxx",
  "type": "conversation.item.input_audio_transcription.delta",
  "meta": {
    "session_id": "sess_xxx",
    "timestamp": 1642694400000
  },
  "item_id": "item_xxx",
  "content_index": 0,
  "text": "你好",
  "start_time": 0,
  "end_time": 500
}
```

* `item_id` `string`<br />对话项 ID。
* `content_index` `int`<br />内容索引。
* `text` `string`<br />增量转录文本。
* `start_time` `int64`<br />开始时间（毫秒）。
* `end_time` `int64`<br />结束时间（毫秒）。

### 转录完成（conversation.item.input\_audio\_transcription.completed）

返回完整转录结果。

#### `stepaudio-2.5-asr-stream`（推荐）

```json theme={"theme":{"light":"light-plus","dark":"dark-plus"}}
{
  "event_id": "event_xxx",
  "type": "conversation.item.input_audio_transcription.completed",
  "meta": {
    "session_id": "sess_xxx",
    "timestamp": 1642694400000
  },
  "item_id": "item_xxx",
  "content_index": 0,
  "transcript": "你好，世界",
  "words": [
    { "word": "你", "start": 0.00, "end": 0.20, "final": true },
    { "word": "好", "start": 0.20, "end": 0.45, "final": true }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 10,
    "total_tokens": 10
  }
}
```

* `item_id` `string`<br />对话项 ID。
* `content_index` `int`<br />内容索引。
* `transcript` `string`<br />完整转录文本。
* `words` `list`<br />该段全部逐字时间，结构同 `delta`；此处 `final` 均为 `true`。开启 `enable_timestamp_align=true` 后为精确字级时间戳。
* `usage` `object`<br />使用统计信息。

#### `step-asr-1.1-stream`（逐步废弃）

```json theme={"theme":{"light":"light-plus","dark":"dark-plus"}}
{
  "event_id": "event_xxx",
  "type": "conversation.item.input_audio_transcription.completed",
  "meta": {
    "session_id": "sess_xxx",
    "timestamp": 1642694400000
  },
  "item_id": "item_xxx",
  "content_index": 0,
  "transcript": "你好，世界",
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 10,
    "total_tokens": 10
  }
}
```

* `item_id` `string`<br />对话项 ID。
* `content_index` `int`<br />内容索引。
* `transcript` `string`<br />完整转录文本。
* `usage` `object`<br />使用统计信息。

### 错误消息（error）

返回错误信息。

```json theme={"theme":{"light":"light-plus","dark":"dark-plus"}}
{
  "event_id": "event_xxx",
  "type": "error",
  "meta": {
    "session_id": "sess_xxx",
    "timestamp": 1642694400000
  },
  "error": {
    "type": "invalid_request_error",
    "code": "invalid_value",
    "message": "错误描述",
    "param": "audio",
    "event_id": "event_xxx"
  }
}
```

错误类型：

| 类型 | 描述 |
| - | - |
| `invalid_request_error` | 请求参数错误 |
| `internal_error` | 服务器内部错误 |
| `risk` | 内容安全风险 |

错误码：

| 错误码 | 描述 |
| - | - |
| `invalid_value` | 无效参数值 |
| `missing_param` | 缺少必需参数 |
| `internal_error` | 内部错误 |
| `max_idle_timeout` | 空闲超时 |
| `pong_timeout` | 心跳超时 |
| `risk_blocked` | 内容被拦截 |

## 故障排除

* 连接失败：检查服务是否可达、地址与鉴权是否正确。
* 音频无识别结果：确认音频格式、编码与质量。
* 识别准确性低：尝试使用 `prompt` 提示词或更换模型。
* 延迟过高：减小音频分块大小。

完整错误码参见 [错误码](/docs/zh/api-reference/error-codes)。


This documentation is built and hosted on [Mintlify](https://mintlify.com), a developer documentation platform.