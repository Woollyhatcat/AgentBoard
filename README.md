# AgentBoard

人与 Agent 共用的本地持久化黑板。依据 `doc/functional-specification.html` 和 `doc/detailed-functional-specification.html` 开发，当前自由画布、富文本与并行编辑以 [v0.3 补充规格](doc/free-canvas-specification.md) 为准。

## 启动

需要 Python 3.11 或以上；服务端仅使用标准库，前端渲染库已随项目打包。在项目目录执行：

```powershell
python -m agentboard serve
```

打开 [本地黑板](http://127.0.0.1:8765)。首次启动为空看板，可添加卡片或点击「添加 PID 协作示例」。停止服务使用 `Ctrl+C`。

使用 MCP 时，初始化连接会自动在后台启动网页；相同端口与数据目录已有 AgentBoard 时直接复用。聊天结束后网页仍保持可用。`get_board_info` 的 `web` 字段返回网页状态与地址；MCP 能读写数据不代表网页必然已就绪，请以此状态为准。

也可以手动管理后台网页服务：

```powershell
python -m agentboard start
python -m agentboard stop
```

`start` 会等到健康检查通过再返回，可重复执行；`stop` 只停止匹配当前数据目录的服务，不删除卡片。日志位于数据目录的 `web.log`。从其他工作目录恢复网页时可执行 `python C:/Tools/AgentBoard/scripts/start_board.py`。自定义数据目录或端口时，`start`、`stop`、MCP 使用相同的 `--data-dir`／`--port`；端口被其他服务或另一块看板占用时明确报错，不接管已有进程。

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

MCP 默认自动启动网页，使用 `--port` 可指定网页端口。仅需数据工具的无界面集成或测试，可传 `--no-web`。旧的已运行 MCP 进程需要重新连接才能加载新逻辑；已更新的 skill 也会在发现网页不可用时主动调用启动入口恢复页面。

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

编辑内容传 `expected_content_version`，编辑布局传 `expected_layout_version`，同时修改则同时传两个版本；也可用 `expected_version` 作整卡检查。删除与恢复仍需当前 `expected_version`。每个新写入意图使用新的 `command_id`；网络结果未知时通过 `get_operation_status` 查询，或用**原命令和完全相同的输入**重试。已确定的成功与业务失败均保存回执，不自动清理。

完整工具：`list_boards`、`get_board_info`、`get_card_types`、`get_card_list`、`read_card`、`get_board_snapshot`、`get_changes`、`push_card`、`edit_card`、`delete_card`、`restore_card`、`get_asset_sources`、`import_asset`、`get_asset_info`、`read_asset`、`get_operation_status`。目录包含 JSON Schema 输入、输出与中文用途说明。

配套的 Agent 使用 skill 位于 [skills/agentboard/SKILL.md](skills/agentboard/SKILL.md)。将 `agentboard` 文件夹复制到用户级 `~/.agents/skills/` 后，可用 `$agentboard` 显式调用，或按其描述自动选用。skill 提供使用指导，MCP 连接仍需按上述方式配置。用户级技能目录与加载方式见 [OpenAI 官方技能文档](https://learn.chatgpt.com/docs/build-skills)。

协议适配依据 MCP 官方 [生命周期](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle)、[stdio 传输](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)和 [Tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) 规范，实现初始化、工具发现、结构化回执和协议／业务错误分离。当前以 `2025-11-25` 为默认协议版本，并支持 `2025-06-18`、`2025-03-26`、`2024-11-05` 协商；规格中提及的其他协议日期未作为本实现兼容承诺。

## 当前功能

- 文本（Markdown 表格、嵌套列表、任务列表、代码块与 LaTeX）、图片、文件、滑条、勾选框、文本／数值输入。
- 文本源码编辑与实时预览；共享字号 12～48（默认 16），MCP 可通过 `props.font_size` 创建／修改。自动高度、完整展开与手动固定高度。
- 桌面自由画布：拖标题移动、右下角调整尺寸、允许重叠与负坐标；空白／空格拖动／中键平移，滚轮平移，Ctrl＋滚轮按指针缩放 10%～400%。
- 查看全部、原点、选中定位、共享置顶／置底和可选网格吸附。相机保存在本机，Agent 更新不改变用户视角。
- 本机明暗主题、主题色、背景、网格与紧凑模式。
- 标题与完整标识搜索、属性面板、修改来源、最近 50 次变更。
- 内容／布局分别检查并发版本，支持同时挪卡与改正文；局部合并、原子保存、无变化不升版本、持久幂等回执。
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
node --check agentboard/web/canvas.js
node --check agentboard/web/render.js
```

Python 测试使用临时 SQLite 数据目录，覆盖版本冲突、同时写入、幂等重放、生命周期、跨页一致性、资源撤权与分片、正常重启、HTTP 来源校验、MCP 协议及真实 stdio 子进程。Node 仅用于开发时语法检查，运行产品不需要 Node。

网页启动回归测试还覆盖：真实 MCP 初始化后网页可访问、MCP 退出后网页继续运行、重复／并发启动复用、停止后重启、数据目录冲突保护及停止操作的会话校验。

人工页面验收记录与规格映射见 [doc/implementation-status.md](doc/implementation-status.md)。

Markdown、公式和清洗库随项目本地提供，无需运行时 CDN；版本、来源和完整性记录见 `agentboard/web/vendor/manifest.json`，许可证保留在对应子目录。

## 数据与版本管理

首次加载旧版数据库时自动升级到 schema 2.0，并通过 SQLite 备份 API 保存完整旧库到 `data/backups/before-world-*.sqlite3`。卡片标识、正文和资源保持；旧网格坐标转换到世界坐标。迁移前的增量游标需要重新快照；旧进程的网格写入被拒绝，请重新连接 MCP 加载新契约。

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
