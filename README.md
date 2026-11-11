# AgentBoard

人与 Agent 共用的本地持久化黑板。依据 `doc/functional-specification.html` 和 `doc/detailed-functional-specification.html` 开发，详细规格 v0.2 为字段与行为基线。

## 启动

需要 Python 3.11 或以上，无第三方运行依赖。在项目目录执行：

```powershell
python -m agentboard serve
```

打开 [本地黑板](http://127.0.0.1:8765)。首次启动为空看板，可添加卡片或点击「添加 PID 协作示例」。停止服务使用 `Ctrl+C`。

```powershell
# 自定义端口和数据目录
python -m agentboard serve --port 8765 --data-dir ./data
```

默认数据目录固定为项目下的 `data/`，不受启动时当前目录影响。HTTP 和 MCP 必须使用同一个数据目录。

## 连接 Agent

提供 **MCP stdio** 接口，包含规格定义的 16 个工具。以下为支持 `mcpServers` 配置的客户端示例；其他客户端使用相同 command / args 配置，按各自格式填写。

```json
{
  "mcpServers": {
    "agentboard": {
      "command": "python",
      "args": [
        "-u",
        "C:\\Tools\\AgentBoard\\scripts\\mcp_server.py"
      ]
    }
  }
}
```

如果客户端找不到 Python，将 `command` 换成 Python 解释器的绝对路径。脚本入口可从任意工作目录启动，不需要客户端支持 `cwd`。可在 args 末尾添加 `--data-dir`、`--agent-id`、`--agent-name`；不同 Agent 使用不同 id，以隔离命令回执。

先调用 `list_boards` 确认看板（初始标识为 `board_local`），再用 `get_card_types` 获取字段定义。创建示例：

```json
{
  "name": "push_card",
  "arguments": {
    "board_id": "board_local",
    "command_id": "create-kp-001",
    "card": {
      "type": "slider",
      "type_version": "1.0",
      "title": "比例增益 Kp",
      "props": {"min": 0, "max": 10, "step": 0.1, "precision": 1},
      "state": {"value": 2.5}
    }
  }
}
```

编辑、删除与恢复需带卡片当前 `expected_version`。每个新写入意图使用新的 `command_id`；网络结果未知时通过 `get_operation_status` 查询，或用**原命令和完全相同的输入**重试。已确定的成功与业务失败均保存回执，不自动清理。

完整工具：`list_boards`、`get_board_info`、`get_card_types`、`get_card_list`、`read_card`、`get_board_snapshot`、`get_changes`、`push_card`、`edit_card`、`delete_card`、`restore_card`、`get_asset_sources`、`import_asset`、`get_asset_info`、`read_asset`、`get_operation_status`。目录包含 JSON Schema 输入、输出与中文用途说明。

协议适配依据 MCP 官方 [生命周期](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)、[stdio 传输](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)和 [Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) 规范，实现初始化、工具发现、结构化回执和协议／业务错误分离。当前以 `2025-11-25` 为默认协议版本，并支持 `2025-06-18`、`2025-03-26`、`2024-11-05` 协商；规格中提及的其他协议日期未作为本实现兼容承诺。

## 首版功能

- 文本（安全 Markdown 子集）、图片、文件、滑条、勾选框、文本／数值输入。
- 12 列网格，自动放置、把手拖动、边角缩放和键盘可用的布局表单；窄屏单列不改变共享坐标。
- 标题与完整标识搜索、属性面板、修改来源、最近 50 次变更。
- 整卡版本冲突检测、局部合并、原子保存、无变化不升版本、持久幂等回执。
- 用户锁定／显式解锁、软删除与恢复、位置被占用时自动重排恢复卡片。
- 不可变导入副本、已授权目录内受控引用、资源刷新、撤权、图片与 UTF-8 文本预览、文件下载。
- 固定时点快照／列表分页、按 revision 增量分页、10 分钟续读期限、资源版本绑定分片读取。
- 网页每 1.5 秒自动核对变化，保留本页草稿并展示外部版本；断线后先核对未确认命令。

## 资源和访问边界

网页只监听 `127.0.0.1`。浏览器写请求检查 Host、Origin 和本次服务会话令牌。用户身份由 HTTP 适配器生成，Agent 身份由本地 MCP 启动配置生成，工具参数不能自报身份。首版面向可信本机用户与其启动的 Agent，拥有此电脑文件访问权的其他进程不属于隔离边界；当前不提供公网部署、远程认证或多用户账户。

只有用户能在「看板设置」授权本地目录、撤销授权和调整 Agent 权限。MCP 只返回目录逻辑标识与标签，不返回绝对路径。资源路径会解析真实目标并检查目录边界，拒绝路径穿越、盘符和 NTFS 备用数据流；每次读取都重新核对引用状态。撤销目录权限后引用不可读，之前的导入副本仍可读取。

单资源最多 **20 MiB**，Agent inline 导入最多 **1 MiB**。图片支持 PNG、JPEG、WebP、GIF；UTF-8 文本可分段预览，其他格式下载原文件。HTML／SVG 不按网页执行；不抓取远程 URL，不运行文件、命令或仿真。

## 测试

```powershell
python -m unittest discover -v
node --check agentboard/web/app.js
```

Python 测试使用临时 SQLite 数据目录，覆盖版本冲突、同时写入、幂等重放、生命周期、跨页一致性、资源撤权与分片、正常重启、HTTP 来源校验、MCP 协议及真实 stdio 子进程。Node 仅用于开发时语法检查，运行产品不需要 Node。

人工页面验收记录与规格映射见 [doc/implementation-status.md](doc/implementation-status.md)。

## 数据与版本管理

SQLite 数据库为 `data/agentboard.sqlite3`，启用 WAL 与同步事务。资源副本也存放在数据库中。停止所有 HTTP／MCP 进程后，可复制整个数据目录作为备份；运行中不要只复制单个 `.sqlite3` 文件。删除卡片不会清理资源。当前保留所有卡片变更与回执，满足至少 30 天变化读取，但长期运行会增加数据库体积。

源码、测试与规格通过本地 Git 管理；`data/`、缓存与环境文件不进入版本库。未配置远程仓库。

## 项目结构

```text
agentboard/schema.py       字段契约、类型定义与合并后校验
agentboard/service.py      SQLite 事务、卡片、回执、快照与资源
agentboard/http.py         本地网页与用户管理 API
agentboard/mcp.py          MCP stdio 协议适配
agentboard/web/            中文网页与交互
scripts/mcp_server.py      不依赖 cwd 的 MCP 启动入口
tests/                    服务与协议集成测试
doc/                      原始规格与实现验收记录
```

本版本不包含 P1 的多看板、多人协作、完整历史回滚、批量编辑、主动调度、事件消费或任意脚本卡片。网页修改不会自动唤起 Agent 推理。
