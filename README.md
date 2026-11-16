# AgentBoard

人与 Agent 共用的本地持久化黑板。依据 `doc/functional-specification.html` 和 `doc/detailed-functional-specification.html` 开发，当前自由画布、富文本与并行编辑以 [v0.3 补充规格](doc/free-canvas-specification.md) 为准。

## 给 Codex 配置 AgentBoard（Windows）

本节面向下载本项目后，在自己电脑的 Codex 桌面端、CLI 或 IDE 扩展中使用黑板的用户。使用本机 stdio 连接即可，无需配置 tunnel、OAuth 或额外的 OpenAI API Key。Codex 自身的登录与使用额度仍按你的账户配置。

每位用户在自己的电脑运行一份 AgentBoard，拥有自己的黑板数据；下载安装本项目不会连接到发布者的黑板。以下统一以 `C:\Tools\AgentBoard` 为项目目录，请替换为你实际解压或克隆到的目录。保留完整项目文件，不要只复制 `scripts/mcp_server.py`。

### 1. 准备 Python 环境

安装 Python **3.11 或以上**，在 PowerShell 中执行：

```powershell
Set-Location "C:\Tools\AgentBoard"
python --version
python -m venv .venv
```

后续直接使用 `.venv` 内的解释器，无需激活虚拟环境。基础黑板和 MCP 只依赖 Python 标准库，不需要安装 Node.js，也不需要执行 `pip install .`。

建议安装截图依赖，让 Codex 能查看黑板的真实排版、检查公式与交互图：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-screenshot.txt
```

截图优先使用本机 Edge／Chrome。如果没有受支持的浏览器，或截图提示浏览器不可用，再安装 Chromium：

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

不安装截图依赖仍可使用基础读写工具；`get_board_screenshot` 会返回依赖错误，无法辅助检查布局。

### 2. 添加 Codex MCP 配置

打开用户级配置文件 `%USERPROFILE%\.codex\config.toml`，不存在时创建。保留已有内容，新增下面的表；如果已有 `[mcp_servers.agentboard]`，直接修改该表，不要重复添加。

```toml
[mcp_servers.agentboard]
command = "C:/Tools/AgentBoard/.venv/Scripts/python.exe"
args = [
  "-u",
  "C:/Tools/AgentBoard/scripts/mcp_server.py",
  "--data-dir", "C:/Tools/AgentBoard/data",
  "--port", "8765",
  "--agent-id", "codex-local",
  "--agent-name", "Codex"
]
startup_timeout_sec = 30
tool_timeout_sec = 120
```

将所有 `C:/Tools/AgentBoard` 替换为你的实际目录。TOML 双引号字符串中使用 `/` 可避免 Windows 反斜杠转义问题。解释器和脚本使用绝对路径，因此可以从其他项目调用，不依赖 Codex 的当前工作目录。

如果已安装 Codex CLI，也可以执行以下命令添加连接，与手动配置二选一：

```powershell
codex mcp add agentboard -- "C:/Tools/AgentBoard/.venv/Scripts/python.exe" -u "C:/Tools/AgentBoard/scripts/mcp_server.py" --data-dir "C:/Tools/AgentBoard/data" --port 8765 --agent-id codex-local --agent-name Codex
```

CLI 添加后可在配置文件中补充上述两个超时设置。可用 `codex mcp list` 检查配置是否存在；这不代表工具已经成功调用。Codex 使用 `config.toml` 中的 `[mcp_servers.*]`，不要把下文其他 MCP 客户端的 `mcpServers` JSON 直接粘贴进去。

当前官方 OpenAI 文档说明：同一 Codex 主机上的桌面端、CLI 和 IDE 扩展共享 MCP 配置；也可使用受信任项目中的 `.codex/config.toml` 将连接限制到某个项目。这里采用用户级配置，便于在不同项目中使用同一块黑板。参见 [Codex MCP 配置](https://learn.chatgpt.com/docs/extend/mcp#connect-codex-to-an-mcp-server)。

### 3. 安装配套 skill（推荐）

MCP 提供黑板工具，skill 进一步指导 Codex 如何共同讨论、逐步板书、生成交互演示和检查布局。只配置 MCP 不会自动安装 skill。

将本项目的 `skills/agentboard` 文件夹复制到 `%USERPROFILE%\.agents\skills\agentboard`。在项目目录执行：

```powershell
New-Item -ItemType Directory -Force -Path "$env:USERPROFILE\.agents\skills" | Out-Null
Copy-Item -LiteralPath ".\skills\agentboard" -Destination "$env:USERPROFILE\.agents\skills\agentboard" -Recurse
```

如果目标已存在同名 skill，先检查已有内容，再合并更新，避免复制成两层 `agentboard/agentboard`。

**当前 skill 中仍有开发者电脑上的项目绝对路径，需要改成你的路径。** 打开复制后的 `SKILL.md`，将其中的 `C:/Tools/AgentBoard` 全部替换为你的项目目录（本例为 `C:/Tools/AgentBoard`）。这些路径用于启动服务、截图和读取交互卡片文档；只改 MCP 配置不会同时改好它们。保留项目中的 `doc/` 和 `scripts/`，供 skill 引用。

安装完成后可在支持 `$` 调用的 Codex 输入框中使用 `$agentboard`；也可直接说明要使用 AgentBoard。skill 的用户级安装位置及发现方式见 [官方技能文档](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills)。

### 4. 重连并验证

保存配置后重启 Codex，或重新连接该 MCP，再开始一段对话。CLI 用户可用 `/mcp` 查看当前连接。先发送只读验证请求：

```text
请使用 AgentBoard MCP，先调用 list_boards，再调用 get_board_info。
告诉我是否连接成功，以及返回的黑板网页地址。先不要创建卡片。
```

正常情况下 `get_board_info` 返回成功，`web.status` 为 `running`，地址为 [http://127.0.0.1:8765](http://127.0.0.1:8765)。在浏览器打开该地址即可查看黑板。服务会自动启动或复用，聊天结束后继续运行，无需单独开终端常驻。

读写验收可继续发送：

```text
请在 AgentBoard 创建一张标题为“连接测试”的文本卡片，正文写“Codex 已连接”，
然后读取这张卡片确认保存成功，并给我黑板链接。
```

浏览器应出现同一张卡片。安装了截图依赖后，还可以请 Codex 调用 `get_board_screenshot` 检查布局。实际工具回执与浏览器内容一致，才算完成连接验证。

### 常见问题与日常维护

| 现象 | 处理方法 |
| --- | --- |
| Codex 中没有 AgentBoard 工具 | 检查配置文件位置、TOML 格式、Python 与脚本绝对路径，然后重连。仅复制 skill 不会建立 MCP 连接。 |
| 启动失败或找不到 Python | 确认 `.venv/Scripts/python.exe` 已存在，配置中的目录与解压位置一致。项目移动后要同步修改配置和 skill 路径。 |
| 网页无法打开 | 读取 `get_board_info.web` 的具体错误；查看数据目录 `web.log`。可以执行下方手动启动命令诊断。 |
| 8765 被占用 | 停止冲突程序，或把 MCP 参数中的 `--port` 改为其他空闲端口，例如 8766；手动启停也必须使用该端口。不要停止不属于本项目的服务。 |
| 看板为空或与预期内容不一致 | 检查 `--data-dir` 是否指向原来的数据目录。相同机器上要共用一块黑板的入口必须使用相同目录和端口。 |
| 截图报缺少依赖／浏览器 | 用 MCP 配置中同一个 Python 安装 `requirements-screenshot.txt`，必要时安装 Chromium。 |
| skill 仍尝试访问发布者目录 | 检查用户级 `SKILL.md` 中的旧绝对路径是否已全部替换。 |
| 更新项目后仍是旧工具／旧行为 | 重启后台并重连 MCP，使后台和转发进程加载新代码。 |

手动启动或停止后台（示例参数与上面的 MCP 配置一致）：

```powershell
& "C:\Tools\AgentBoard\.venv\Scripts\python.exe" "C:\Tools\AgentBoard\scripts\start_board.py" --data-dir "C:\Tools\AgentBoard\data" --port 8765
& "C:\Tools\AgentBoard\.venv\Scripts\python.exe" -m agentboard stop --data-dir "C:\Tools\AgentBoard\data" --port 8765
```

其中 `-m agentboard stop` 需在项目根目录执行。停止后台不会删除卡片；仍然连接的 MCP 入口可能在后续初始化时再次启动后台。

数据保存在配置的 `data/` 中。升级时保留此目录；备份前停止后台和所有 MCP 入口，再复制整个数据目录。向别人分发项目时使用干净源码包，不要夹带自己的 `data/`、`.venv/`、密钥或本机 Codex 配置。每位接收者按本节重新配置路径并创建自己的数据。

## 启动

需要 Python 3.11 或以上；基础服务仅使用标准库，前端渲染库已随项目打包，截图功能有下文列出的可选依赖。在项目目录执行：

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

提供 **MCP stdio** 接口，包含卡片、资源、截图与黑板管理，共21个工具。以下为支持 `mcpServers` 配置的客户端示例；其他客户端使用相同 command / args 配置，按各自格式填写。

v0.6起，默认stdio入口是轻量转发器：启动或复用同一数据目录的常驻网页进程，将MCP请求转发至其 `/mcp` 端点。MCP协议处理、工具执行和数据库访问都在该常驻进程中完成，因此Codex与tunnel可沿用原command配置并共享同一后端；每个客户端仍有自己的转发进程。`--no-web`保留独立stdio执行模式，用于测试和无网页场景。

`http://127.0.0.1:8765/mcp` 同时提供JSON响应的Streamable HTTP接口。它只接受本机Host／Origin，并要求独立Bearer凭据，当前凭据位于对应数据目录的 `mcp-token`（后台重启时轮换）。该文件属于密钥，不能上传、写入板书或提交Git；它不是OpenAI隧道API Key，也不授予网页用户管理权。HTTP调用固定使用Agent角色，代理入口的Agent标识仅作为来源标签，非多人认证。旧协议客户端的会话分别隔离，30分钟无活动后失效，最多256个；新版请求独立处理，不依赖连接会话。GET事件流未开放（405），当前工具不声明订阅能力。

升级时先重启本地网页服务，再重启既有stdio入口／隧道MCP子进程。验证 `/api/health` 的pid及mcp_path，并用实际工具调用确认，不能仅以tunnel ready判断。若直接改用HTTP客户端，应独立配置该本机Bearer凭据；保留stdio转发配置则会自动从本机文件读取，无需在Codex或tunnel配置中复制密钥。

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

完整工具：`list_boards`、`create_board`、`rename_board`、`duplicate_board`、`delete_board`、`get_board_info`、`get_card_types`、`get_card_list`、`read_card`、`get_board_snapshot`、`get_board_screenshot`、`get_changes`、`push_card`、`edit_card`、`delete_card`、`restore_card`、`get_asset_sources`、`import_asset`、`get_asset_info`、`read_asset`、`get_operation_status`。目录包含 JSON Schema 输入、输出与中文用途说明。

Agent 布局时可调用 `get_board_screenshot` 获取原生PNG图像、实际卡片尺寸和像素／世界坐标映射。支持整板、单卡与指定区域截图，复用真实网页排版，不改变用户视角。截图依赖可选安装：`python -m pip install -r requirements-screenshot.txt`，使用本机Edge/Chrome；没有浏览器时执行 `python -m playwright install chromium`。当前电脑已安装依赖，可直接使用。旧连接尚无新工具时执行 `python scripts/capture_board.py --view all`，再打开回执的 `data.path`。详细参数、版本检查和依赖说明见 [截图接口](doc/screenshot-interface.md)。

配套的 Agent 使用 skill 位于 [skills/agentboard/SKILL.md](skills/agentboard/SKILL.md)。将 `agentboard` 文件夹复制到用户级 `~/.agents/skills/` 后，可用 `$agentboard` 显式调用，或按其描述自动选用。skill 提供使用指导，MCP 连接仍需按上述方式配置。用户级技能目录与加载方式见 [OpenAI 官方技能文档](https://learn.chatgpt.com/docs/build-skills)。

协议适配支持两种生命周期：旧客户端使用 `initialize`／`notifications/initialized`，默认 `2025-11-25`，兼容 `2025-06-18`、`2025-03-26`、`2024-11-05`；新版客户端可使用 `2026-07-28` 的无状态 [server/discover](https://modelcontextprotocol.io/specification/2026-07-28/server/discover)、逐请求协议元数据及工具调用。新版请求独立校验版本、clientInfo与clientCapabilities，结果携带resultType和服务身份；客户端元数据不授予用户权限。仅实现本项目声明的tools能力，原有版本冲突、权限与命令回执检查继续适用。

通过 Secure MCP Tunnel 使用本项目时，建议仅运行一个相同profile的客户端实例。旧协议握手可加 `--mcp.stdio-send-initialized-notification`；端口冲突可用 `--health.listen-addr 127.0.0.1:0`。需要代理时显式指定 `--control-plane.http-proxy`。检查 `/health/mcp` 中实际工具发现结果；客户端ready不等于MCP发现成功。更新程序后需重启对应MCP子进程，并在ChatGPT中重试或刷新连接。

## 当前功能

- 多黑板管理：侧边栏切换、新建、重命名、复制副本、整板软删除和删除保护；所有写入自动保存。
- 文本（Markdown 表格、嵌套列表、任务列表、代码块与 LaTeX）、图片、文件、滑条、勾选框、文本／数值输入。
- 通用交互卡片：Agent通过MCP生成HTML/CSS/JavaScript数学演示，卡内控件即时重绘并保存参数，可只读绑定其他卡片；自带复平面向量实验、停止／重置／重新运行和截图就绪检查。接口见 [交互卡片文档](doc/interactive-cards.md)。
- 文本源码编辑与实时预览；共享字号 12～48（默认 16），MCP 可通过 `props.font_size` 创建／修改。自动高度、完整展开与手动固定高度。
- 桌面自由画布：拖标题移动、右下角调整尺寸、允许重叠与负坐标；空白／空格拖动／中键平移，滚轮平移，Ctrl＋滚轮按指针缩放 10%～400%。
- 查看全部、原点、选中定位、共享置顶／置底和可选网格吸附。相机保存在本机，Agent 更新不改变用户视角。
- 本机明暗主题、主题色、背景、网格与紧凑模式。
- 工具栏从左到右为添加卡片、查看 Agent 更新、搜索；显示风格统一从看板设置进入，导入资源位于侧边栏。顶部可展开／隐藏侧边栏，并在本机记住状态。
- 标题与完整标识搜索、属性面板、修改来源、最近 50 次变更。
- 内容／布局分别检查并发版本，支持同时挪卡与改正文；局部合并、原子保存、无变化不升版本、持久幂等回执。
- 用户锁定／显式解锁、软删除与恢复、位置被占用时自动重排恢复卡片。
- 不可变导入副本、已授权目录内受控引用、资源刷新、撤权、图片与 UTF-8 文本预览、文件下载。
- 固定时点快照／列表分页、按 revision 增量分页、10 分钟续读期限、资源版本绑定分片读取。
- 网页每 1.5 秒自动核对变化，保留本页草稿并展示外部版本；断线后先核对未确认命令。

## 资源和访问边界

首次启动或升级到v0.7时，自动创建并授权**项目根目录下的 `workspace/`**。这是资源读取的默认工作目录，与 `--data-dir` 的位置无关，不会授权整个项目。文件放入该目录后可通过 `get_asset_sources` 返回的逻辑标识和相对路径导入／引用。`workspace/` 中的用户文件不进入Git。

「看板设置 → 已授权资源目录」列出目录名称、实际路径及取消授权按钮，也可添加其他目录。授权为应用级，所有黑板共用；用户取消后不会在重启或新建黑板时自动恢复，需要用户重新授权。实际路径仅在用户管理界面显示，MCP只获取逻辑标识。默认授权不提供MCP文件写入或命令执行能力。

网页只监听 `127.0.0.1`。浏览器写请求检查 Host、Origin 和本次服务会话令牌。用户身份由 HTTP 适配器生成，Agent 身份由本地 MCP 启动配置生成，工具参数不能自报身份。首版面向可信本机用户与其启动的 Agent，拥有此电脑文件访问权的其他进程不属于隔离边界；当前不提供公网部署、远程认证或多用户账户。

只有用户能在「看板设置」授权本地目录、撤销授权和调整 Agent 权限。MCP 只返回目录逻辑标识与标签，不返回绝对路径。资源路径会解析真实目标并检查目录边界，拒绝路径穿越、盘符和 NTFS 备用数据流；每次读取都重新核对引用状态。撤销目录权限后引用不可读，之前的导入副本仍可读取。

单资源最多 **20 MiB**，Agent inline 导入最多 **1 MiB**。图片支持 PNG、JPEG、WebP、GIF；UTF-8 文本可分段预览，其他格式下载原文件。导入的HTML／SVG不按网页执行。新增interactive类型只在隔离页面中执行其演示源码，提供自身参数保存与声明的只读输入，不提供宿主页、文件或命令接口；具体CSP与运行限制见交互卡片文档。

## 测试

```powershell
python -m unittest discover -v
node --check agentboard/web/app.js
node --check agentboard/web/canvas.js
node --check agentboard/web/render.js
node --check agentboard/web/interactive.js
node --check agentboard/web/interactive-sdk.js
```

Python 测试使用临时 SQLite 数据目录，覆盖版本冲突、同时写入、幂等重放、生命周期、跨页一致性、资源撤权与分片、正常重启、HTTP 来源校验、MCP 协议及真实 stdio 子进程。Node 仅用于开发时语法检查，运行产品不需要 Node。

网页启动回归测试还覆盖：真实 MCP 初始化后网页可访问、MCP 退出后网页继续运行、重复／并发启动复用、停止后重启、数据目录冲突保护及停止操作的会话校验。

人工页面验收记录与规格映射见 [doc/implementation-status.md](doc/implementation-status.md)。

Markdown、公式和清洗库随项目本地提供，无需运行时 CDN；版本、来源和完整性记录见 `agentboard/web/vendor/manifest.json`，许可证保留在对应子目录。

## 数据与版本管理

### 多黑板与自动保存（v0.7）

侧边栏的「新建黑板」创建独立空间，下拉框切换当前黑板，「管理黑板」提供重命名、复制副本、锁定／解除保护和删除。浏览器记住上次选择，每块黑板单独保存相机。存在未保存草稿、未确认写入或拖动操作时先保留当前黑板，处理完后再切换；不同浏览器标签可以查看不同黑板。

卡片和管理操作沿用SQLite事务自动保存，界面底部显示保存状态，无需额外点击“保存黑板”。卡片、资源记录、卡片回收站、版本与增量按黑板隔离，授权目录共用。Agent使用 `list_boards` 选择目标，不应假定始终使用 `board_local`；用户当前浏览器选择不自动改变Agent的目标。

| MCP方法 | 输入与行为 |
| --- | --- |
| `create_board` | `name`、`command_id`，可选`description`；不传`board_id`，返回新黑板。查询其操作回执时同样不传`board_id`。 |
| `rename_board` | `board_id`、`name`、`expected_version`、`command_id`，使用`get_board_info.version`。 |
| `duplicate_board` | `board_id`、副本`name`、`command_id`；事务中复制全部卡片（含回收站）、资源记录、卡片锁定和黑板权限／删除保护，返回新黑板及卡片、资源标识映射。内部绑定与资源链接自动重映射；历史回执不复制。 |
| `delete_board` | `board_id`、`expected_version`、`command_id`；软删除后从列表移除，界面暂不提供整板恢复入口，不会删除磁盘原文件。 |

副本的内容与导入资源可以独立修改，**受控文件引用仍指向同一个外部原文件**，不会自动复制原文件。黑板管理版本`version`随内容／布局或管理变动递增，过期的重命名、锁定、删除请求会返回`VERSION_CONFLICT`。

锁定黑板代表**整板删除保护**：锁定后用户与Agent均不能直接删除整块黑板；只有用户能在管理界面显式解除保护，`set_board_lock`不向MCP公开。锁定不阻止日常卡片编辑／删除；如需保护某张卡片，使用原有单卡锁定。Agent删除黑板还需拥有该黑板的删除权限。

旧单黑板数据库会在启动时自动升级，原`board_local`及资料保留；已有事件的数据库升级前备份至`data/backups/before-multiboard-*.sqlite3`。升级后重启后台并重连MCP加载21个工具，无需重新配置command。

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

本版本不包含多用户账户、完整历史回滚、批量编辑、主动调度、事件消费或服务端任意脚本执行。交互演示在浏览器中计算；网页修改不会自动唤起 Agent 推理。
