# 心流插件（Heartflow）

基于双模型架构的 AstrBot 群聊主动回复插件。**模拟真人“隔一段时间翻看未读群聊”的节奏**，批量阅读并评估未读消息，只在遇到合适且非群友一对一私聊的公开话题时，围绕该话题主动接话一轮。

## 核心特点

- **批量看群节拍（定时 + 定量 + 防抖）**：
  - 不再逐条消息调用模型；新消息到达后先进入未读队列与防抖静默期，攒满一定条数或间隔到达时由后台任务统一翻看未读。
  - 默认看群间隔 `900s`（15 分钟）、攒满 `20` 条提前看、防抖 `5s`，兼顾拟人感与低 Token / 低 RPM 消耗。
- **定向对话识别与过滤（避免打断别人说话）**：
  - 自动解析消息中的 `@`、引用回复（`Reply`）、分享卡片、纯表情/图片，以及短时间内的两人一对一接龙对话（`reply_in_flow`）。
  - 如果本批未读全部是群友之间定向交流或无意义水群卡片，直接推进已读水位并跳过小模型。
  - 小模型评估时也会显式标注每条消息的发言对象，严禁选取针对他人的消息作为回复焦点，后端二次校验兜底。
- **话题焦点锚定与精准引用**：
  - 小模型一次性阅读未读并挑选最值得聊的一条焦点消息（`focus_index`），由主会话模型围绕该话题自然接话。
  - 当所选话题已被后续几条新消息刷走时，可自动引用（`Reply`）对应的目标消息；紧邻末尾话题则直接发言不刻意 `@` 人。
- **防刷屏与状态衔接**：
  - 内置精力系统、近 15 分钟机器人发言占比上限（`self_share_limit`）、每群判断冷却、全局每分钟 RPM 上限与最短主动回复间隔。
  - 直接通过主模型生成并通过 `context.send_message` 发送，不重新往消息总线注入伪事件，避免与其他大型群聊陪伴插件（如 `astrbot_plugin_private_companion`）产生循环监听或上下文污染。

## 兼容性

- AstrBot `>=4.27,<5`（已在 v4.29.1 管线与消息组件模型上验证）
- 插件本身没有额外的第三方依赖

AstrBot 内置了概率式群聊主动回复；使用本插件时请关闭 AstrBot 配置中的 `provider_ltm_settings.active_reply.enable`。如果同时安装了 `astrbot_plugin_private_companion`，建议关闭它的群聊主动插话（`enable_group_interjection`），将群聊主动插话统一交给心流插件处理，其余陪伴功能可正常共存。

## 安装

推荐直接在 AstrBot WebUI 的插件管理页面通过仓库地址安装：

```text
https://github.com/Mizhiawa/Astrbot_plugin_Heartflow_m1zVer
```

手动安装时，将仓库克隆到 AstrBot 的 `data/plugins/`，重启 AstrBot 或在插件管理页面重载插件。

## 必要配置

1. 在 AstrBot 中配置一个成本较低、响应较快的聊天模型提供商。
2. 将 `judge_provider_name` 设置为该提供商的 ID。
3. 打开 `enable_heartflow`。
4. 按你的群活跃度微调 `look_interval_seconds`（如 `600~1800` 秒）与 `look_unread_threshold`（如 `15~30` 条）。

## 主要配置

| 配置 | 默认值 | 说明 |
| --- | ---: | --- |
| `enable_heartflow` | `false` | 是否启用心流主动回复 |
| `judge_provider_name` | 空 | 用于看群评估的小模型提供商 ID |
| `look_interval_seconds` | `900` | 有未读消息时定时看群间隔（秒），范围 `60-3600` |
| `look_unread_threshold` | `20` | 未读消息累计达到该条数时不等定时直接看群，`0` 为仅定时 |
| `look_debounce_seconds` | `5` | 收到消息后静默等待多少秒再看群，避免截断连发 |
| `look_max_unread_messages` | `40` | 单次看群最多读取的未读消息数 |
| `reply_threshold` | `0.62` | 综合评分达到该值才触发主动回复 |
| `filter_directed_messages` | `true` | 过滤 `@他人`、引用他人及群友短时间一对一接龙 |
| `allow_quote_reply` | `true` | 话题被刷过去几条时是否允许引用对应焦点消息 |
| `self_share_limit` | `0.25` | 近 15 分钟内机器人发言轮次占比达到该值时暂停插嘴 |
| `min_reply_interval_seconds` | `300` | 两次主动回复之间的最短间隔秒数 |
| `judge_cooldown_seconds` | `60` | 同一群聊两次小模型看群之间的最短冷却秒数 |
| `judge_max_requests_per_minute` | `20` | 所有群聊合计每分钟最多调用小模型的次数 |
| `energy_decay_rate` | `0.1` | 每次主动回复后消耗的精力 |
| `energy_recovery_rate` | `0.03` | 看群未回复以及每经过 5 分钟恢复的精力 |
| `judge_context_count` | `10` | 在未读消息前附带的已读历史消息条数 |
| `judge_timeout_seconds` | `30` | 小模型评估（含重试）超时秒数 |
| `reply_timeout_seconds` | `45` | 主大模型生成回复超时秒数 |
| `whitelist_enabled` | `false` | 是否仅处理白名单群聊 |
| `chat_whitelist` | `[]` | 通过 `/sid` 获取并填写完整会话 ID |

五项评分权重默认分别为：内容价值度 30%、回复意愿 25%、社交适宜性 20%、时机与状态 15%、对话连贯性 10%。权重不必手动保证总和为 1，插件会自动归一化；全部为 0 时会回退到默认权重。

## 管理命令

以下命令仅限 AstrBot 管理员：

- `/heartflow`：查看当前群聊的精力、待看未读数、发言占比与配置
- `/heartflow_reset`：重置当前群聊状态、定时任务与本地消息缓冲
- `/heartflow_cache`：查看人格提示词摘要缓存
- `/heartflow_cache_clear`：清空人格提示词摘要缓存

## 工作方式

1. **只记不阻塞**：`on_group_message` 收到群消息后解析 `@`、`Reply`、分享卡片与对话指向，写入缓冲区并通知后台看群 Worker，不阻塞其他插件。
2. **节奏触发**：当满足“防抖静默 + 到达定时看群间隔（或未读攒够阈值）”时，Worker 取出当前未读批次。
3. **规则门控**：检查最短回复间隔、近期发言占比与是否全为定向他人对话；若全部是 `@他人`/接龙/分享卡片，直接推进已读水位跳过小模型。
4. **批量看群评估**：小模型带着精简人设、历史背景和标注了指向的未读流水打分，并选定 `focus_index`（禁选定向他人消息）。
5. **主模型接话与发送**：通过主会话模型生成自然口语回复（若主模型觉得不合适可输出 `[SKIP]` 放弃），校验期间没有新 `@Bot` 冲突后发送并写回上下文。

## 本地检查

```bash
python -m unittest discover -s tests -v
```
