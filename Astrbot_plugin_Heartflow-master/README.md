# 心流插件（Heartflow）

基于双模型架构的 AstrBot 群聊主动回复插件。小模型负责判断当前消息是否值得参与，大模型继续使用 AstrBot 当前会话的提供商和人格生成回复。

## 功能

- 使用独立的小模型从内容、意愿、社交、时机和连贯性五个维度评分
- 按群隔离上下文、精力、冷却和统计状态
- 保存普通群聊和直接 @ 机器人的对话，自动排除命令消息
- 使用每群异步锁，避免并发消息同时突破冷却限制
- 小模型判断带每群冷却与全局每分钟上限，避免逐条消息调用导致 RPM 超限
- 将判断规则放在系统提示词中，并严格校验模型返回的 JSON
- 支持群聊白名单、评分权重、上下文数量、判断超时和最短回复间隔
- 限制群聊状态与人格摘要缓存，避免长期运行后无界占用内存

## 兼容性

- AstrBot `>=4.27,<5`
- 插件本身没有额外的第三方依赖

AstrBot 4.27 已经提供概率式群聊主动回复。如果使用本插件，请关闭 AstrBot 的 `provider_ltm_settings.active_reply.enable`，避免两套触发策略同时生效；插件检测到冲突时也会在日志中警告一次。

## 安装

推荐直接在 AstrBot WebUI 的插件管理页面通过仓库地址安装：

```text
https://github.com/Mizhiawa/Astrbot_plugin_Heartflow_m1zVer
```

手动安装时，将仓库克隆到 AstrBot 的 `data/plugins/`：

```bash
cd AstrBot/data/plugins
git clone https://github.com/Mizhiawa/Astrbot_plugin_Heartflow_m1zVer.git
```

重启 AstrBot 或在插件管理页面重载插件。

## 必要配置

1. 在 AstrBot 中配置一个成本较低、响应较快的聊天模型提供商。
2. 将 `judge_provider_name` 设置为该提供商的 ID。
3. 打开 `enable_heartflow`。
4. 建议先设置 `judge_cooldown_seconds`（判断冷却）和 `min_reply_interval_seconds`，再逐步调整回复阈值。

## 主要配置

| 配置 | 默认值 | 说明 |
| --- | ---: | --- |
| `enable_heartflow` | `false` | 是否启用心流主动回复 |
| `judge_provider_name` | 空 | 用于评分的小模型提供商 ID |
| `reply_threshold` | `0.6` | 综合评分达到该值才触发回复 |
| `energy_decay_rate` | `0.1` | 每次成功生成主动回复后消耗的精力 |
| `energy_recovery_rate` | `0.02` | 未回复以及每经过 5 分钟恢复的精力 |
| `context_messages_count` | `5` | 本地上下文缓冲基数 |
| `judge_context_count` | `10` | 实际传给判断模型的最近消息数 |
| `judge_timeout_seconds` | `30` | 整轮判断（含格式重试）或人格总结的超时秒数，范围 `5-120` |
| `min_reply_interval_seconds` | `0` | 两次主动触发的最短间隔；`0` 表示不限制 |
| `judge_cooldown_seconds` | `15` | 同一群聊两次小模型判断之间的最短间隔秒数；`0` 表示不限制 |
| `judge_max_requests_per_minute` | `30` | 所有群聊合计每分钟最多调用小模型的次数；`0` 表示不限制 |
| `whitelist_enabled` | `false` | 是否仅处理白名单群聊 |
| `chat_whitelist` | `[]` | 通过 `/sid` 获取并填写完整会话 ID |
| `judge_max_retries` | `3` | 判断结果格式错误时的重试次数，范围 `0-5` |
| `max_tracked_chats` | `1000` | 内存中最多保留的群聊状态与消息缓冲数 |
| `max_persona_cache` | `100` | 内存中最多保留的人格摘要数 |

五项评分权重默认分别为：内容相关度 25%、回复意愿 20%、社交适宜性 20%、时机 15%、连贯性 20%。权重不必手动保证总和为 1，插件会自动归一化；全部为 0 时会回退到默认权重。

## 管理命令

以下命令仅限 AstrBot 管理员：

- `/heartflow`：查看当前群聊的精力、回复统计和配置
- `/heartflow_reset`：重置当前群聊状态和本地消息缓冲
- `/heartflow_cache`：查看人格提示词摘要缓存
- `/heartflow_cache_clear`：清空人格提示词摘要缓存

## 工作方式

1. 插件记录当前群聊消息；斜杠命令不会进入上下文。
2. 消息通过冷却与限频检查后，才交给判断模型评分；冷却期内的消息只入库、不调用小模型。
3. 综合评分超过阈值且不在冷却期时，插件唤醒 AstrBot 的正常大模型回复流程。
4. 只有大模型成功返回内容后，插件才扣除精力并增加回复次数；生成失败会释放本次冷却预留。
5. AstrBot 发出的普通或流式 LLM 最终回复会写回本地上下文，命令和其他非 LLM 输出不会污染记录。

人格设定、历史消息和当前消息均作为不可信数据传给判断模型；评分规则只放在系统提示词中。模型返回的五项分数必须是 JSON 数字且位于 `0-10`，否则会在总超时预算内重试。

## 调用频率控制

群聊活跃时逐条消息调用小模型会迅速耗尽提供商的 RPM 配额，因此插件对小模型调用做了两层限制：

- **每群判断冷却（`judge_cooldown_seconds`，默认 15 秒）**：同一群聊在冷却期内收到的新消息只写入本地上下文，不会调用小模型；冷却结束后，下一条消息会带着这期间累积的上下文一起判断。冷却按群独立计算，不影响其他群聊。
- **全局每分钟上限（`judge_max_requests_per_minute`，默认 30 次/分钟）**：所有群聊共享一个 60 秒滑动窗口，达到上限后暂停判断，避免机器人在多个活跃群中同时判断而突破提供商配额。判断结果的格式重试同样占用该额度。

`min_reply_interval_seconds` 控制的是**主动回复**的频率（只在机器人真正回复或触发后才计时），与上述判断频率限制相互独立。若判断很少通过，仅靠它是无法限制小模型请求量的。

以上限制都会在日志中记录（冷却为 `debug` 级别，触及全局上限为 `warning` 级别），`/heartflow` 会显示当前群的判断次数和跳过次数。全部设置为 `0` 即可关闭限频，恢复逐条消息判断。

所有内存状态会在插件重载或 AstrBot 重启后清空。

## 本地检查

```bash
python -m unittest discover -s tests -v
python -m ruff check main.py tests
python -m ruff format --check main.py tests
```

在 AstrBot 4.27 源码目录中，可使用 uv 隔离环境执行真实类型和事件钩子集成测试：

```bash
uv run --isolated --project . python ../Astrbot_plugin_Heartflow/tests/astrbot_e2e.py --plugin-root ../Astrbot_plugin_Heartflow
```

## 许可证

本插件遵循仓库中的许可证文件。
