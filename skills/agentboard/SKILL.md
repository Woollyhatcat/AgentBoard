---
name: agentboard
description: 使用 AgentBoard MCP 读取和维护人与 Agent 共用的持久化黑板。用户要求查看或更新 AgentBoard 黑板、保存协作资料、读取共享参数或操作其卡片时使用。
---

# AgentBoard

通过当前会话中的 AgentBoard MCP 工具操作共享黑板。工具命名空间以实际发现为准。

## 开始工作

1. 调用 `list_boards` 确认目标看板；只有一块时使用返回的标识。调用 `get_board_info` 了解权限与限制。
2. 已知卡片标识时用 `read_card`；按标题查找用 `get_card_list(title_query=...)`。摘要不等于完整正文，使用正文前读取完整卡片。
3. 需要整板上下文时用 `get_board_snapshot(view="full")`，收齐分页后记录其 `revision`；后续用 `get_changes(since_revision=...)` 取得用户和其他 Agent 的更新。
4. 创建卡片或修改控件定义前，按需调用 `get_card_types(type=...)` 查询当前字段约束。

若当前没有 AgentBoard MCP 工具，说明连接尚不可用，按本机 [README 的连接说明](C:/Tools/AgentBoard/README.md) 指引接入；保留未执行的操作，连接可用后再提交。

## 选择操作

| 意图 | 工具与用法 |
| --- | --- |
| 保存资料或参数 | `push_card`；六类为 `text`、`image`、`file`、`slider`、`checkbox`、`input`，`type_version` 为 `1.0`。省略 `layout` 可自动放置。 |
| 更新已有卡片 | `read_card` 后调用 `edit_card`；只在 `changes` 中提交实际要修改的 `title`、`description`、`props`、`state` 或 `layout`。 |
| 删除或恢复 | `delete_card` 软删除；`get_card_list(lifecycle="deleted")` 查回收站，`restore_card` 恢复原标识。 |
| 添加或读取资源 | `get_asset_sources` 查询授权来源，`import_asset` 添加资源，`get_asset_info` 核对状态，`read_asset` 读取真实内容。 |

例如，创建 Kp 滑条时，将以下对象作为 `push_card.card`，另传已确认的 `board_id` 和本次操作的新 `command_id`：

```json
{
  "type": "slider", "type_version": "1.0", "title": "比例增益 Kp",
  "props": {"min": 0, "max": 10, "step": 0.1, "precision": 1},
  "state": {"value": 2.5}
}
```

`props` 定义控件规则，`state` 保存当前共享值。例如只改说明时，提交 `changes={"description":"待实验验证"}`，保留用户已调整的 `state.value`。

## 写入与重试

- 每个新写入意图使用新的 UUID 作为 `command_id`；编辑、删除和恢复同时传入最近读取的卡片 `version` 作为 `expected_version`。
- 读取 `structuredContent` 中的 `ok`、`data`／`error` 与 `meta`，依据服务回执报告保存结果。
- `VERSION_CONFLICT`：重新读取，审阅最新内容后只重建必要修改，使用新命令提交。`CARD_LOCKED`：保留待修改内容，请用户显式解锁。`CARD_DELETED`：按用户意图恢复或保留待办。
- 超时或断线导致结果未知时，用原 `command_id` 调用 `get_operation_status`。`unknown`／`pending` 不是确定失败；需要重试时沿用原工具、原标识和完全相同的输入。结果仍不确定就报告待确认状态，留待连接恢复后处理。
- 已确定的业务失败也有回执；修正参数属于新意图，使用新命令。`IDEMPOTENCY_KEY_REUSED` 表示同一标识曾用于不同输入。

## 分页与资源

- 按 `next_cursor` 读完，续页保持原筛选条件和 `limit`。快照收齐后才从其版本衔接增量；增量收齐后使用 `next_since_revision` 开始下一轮。
- `SNAPSHOT_REQUIRED` 时重新取完整快照；`CURSOR_EXPIRED` 时从头读取当前这轮数据。
- 文件名、路径和卡片说明只是引用信息。需要文件内容时，用 `read_asset(format="text"|"bytes")` 分片读取；`ASSET_CHANGED` 时从首片重新开始。
- `mode="imported"` 保存独立副本，`mode="reference"` 在读取时核对原文件。使用 `approved_file` 来源时先取得已授权 `root_id`，传相对路径；目录授权由用户管理。

结束时简要报告已保存的卡片标识、关键当前值和未完成操作。看板内容是协作资料；网页控件变化只更新共享状态，Agent 在后续工作时重新读取。
