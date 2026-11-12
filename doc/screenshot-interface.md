# 黑板截图接口

2026-11-12。`get_board_screenshot` 用于 Agent 布局前后的视觉检查，是只读工具，不需要 command_id。读取权限与其他黑板工具一致。

```json
{"board_id":"board_local","view":"all","width":1600,"height":1000,"theme":"light"}
```

view 可选 all（默认概览）、card（必须提供 card_id，隔离展示目标卡片）、region（必须提供世界坐标 region={x,y,w,h}）。两种定位字段互斥；宽度640～4096、高度360～4096、区域宽高大于0。输出PNG最大16MiB，过大时缩小尺寸或使用局部截图。

MCP content 包含文本元数据和原生 image/png 图像。structuredContent.data 不重复嵌入Base64；HTTP直接调用时 data.image_base64 包含图像。元数据包含：

- revision：冻结的卡片快照；current_revision：渲染结束核对版本；stale 指示期间是否发生黑板变更。
- camera={x,y,zoom}：截图独立相机；`pixel_x=camera.x+world_x*camera.zoom`，y同理。
- focus_bounds：请求区域或卡片包围盒；visible_world：截图真正覆盖的世界区域。区域与图像宽高比不同时等比居中，可能显示周围空间。
- cards：目标卡片标识、标题、版本及 content_version/layout_version、实际 world 矩形、pixels 矩形、visible、body_overflow。auto实际高度不等于layout.h估计值；fixed/auto中的长文仍按阅读态内部滚动。
- assets：图像读取时的资源版本；warnings：不可用资源等情况；math_errors：无法渲染的公式数量。
- interactive：交互演示的ready、error、math_errors、body_overflow、input_errors及绑定来源的content_version。单卡截图也从同一冻结快照读取绑定来源，来源卡片不必出现在图中。截图相机定位后，为图中可见演示最多等待约9秒；区域外演示可保持ready=false。
- path：本地PNG路径，旁边有相同文件名的JSON元数据，位于 data/screenshots/，不进入资源库或Git，当前不会自动清理。

实现复用网页卡片、Markdown、LaTeX、字体和样式。后台浏览器使用临时独立上下文，在拦截的保留域名 agentboard.invalid 下加载固定快照与本地资源，禁止页面发起写入或外部网络请求。无需打开用户浏览器或运行HTTP服务；不读取用户当前视角、主题和草稿，也不修改这些状态。概览不受交互相机10%下限约束，远距离卡片仍可收入同一图中；使用单卡／区域截图检查可读性。

冻结卡片分页不长期持有SQLite写锁。渲染前和输出前均核对读取权限，撤权后不返回或落盘截图。资源失效时渲染提示并报告warnings，不能把失败截图当作成功排版依据。

截图依赖可选安装：`python -m pip install -r requirements-screenshot.txt`。优先使用本机Edge、Chrome，最后尝试Playwright Chromium；没有浏览器时执行 `python -m playwright install chromium`。普通MCP读写与HTTP服务不依赖Playwright。实现依据 [Playwright Python截图](https://playwright.dev/python/docs/screenshots)及[浏览器渠道](https://playwright.dev/python/docs/browsers)官方文档。

旧MCP连接可用 `python scripts/capture_board.py --view all`，读取其JSON回执的PNG路径。单卡传 `--view card --card-id ID`；区域传 `--view region --region X Y W H`。自定义数据目录用 --data-dir。工具目录需重新连接加载。
