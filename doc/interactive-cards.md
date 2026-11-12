# 通用交互卡片 v1

2026-11-12。新增 `type="interactive"`、`type_version="1.0"`，通过现有 `push_card`／`edit_card` 创建和修改。原六类卡片及世界坐标契约继续适用，无需迁移已有卡片。网页“添加卡片 → 交互演示”预填一个复平面向量实验，源码也可以全部替换。

## 卡片数据

```json
{
  "type":"interactive", "type_version":"1.0", "title":"可调参数的数学实验",
  "props":{
    "source":"<output id='value'></output><input id='x' type='range' min='0' max='10' step='0.1'><script>document.getElementById('x').oninput=e=>Board.setState({x:Number(e.target.value)});Board.onChange(({state,readOnly})=>{document.getElementById('x').value=state.x;document.getElementById('x').disabled=readOnly;document.getElementById('value').textContent=state.x*state.x;Board.rendered();});</script>",
    "initial_state":{"x":2},
    "bindings":{}
  },
  "state":{"values":{"x":2}}
}
```

创建时可将 state 设为 `{}`，由 initial_state 初始化 values。源码是完整HTML或HTML片段，可包含内联CSS、JavaScript、SVG、Canvas及原生控件，最多200000字符。初始参数和当前参数分别为最多64KiB、嵌套16层的JSON对象，支持数字、文字、布尔、null、数组与普通对象；不接受NaN、Infinity、Date、函数、TypedArray等非JSON状态，保留的原型属性名不可使用。

默认布局720×700、fixed高度；可自由移动、缩放卡片尺寸和调整层级。改变高度不重新加载程序。`props.source`保存演示定义，`state.values`保存共享参数，绘图结果无需逐帧入库。

## Board 接口

无需引入SDK，宿主在程序运行前提供 `window.Board`，协议版本1.0。无需外网依赖，可直接使用DOM、SVG、Canvas、Math和内置KaTeX。

| 方法／属性 | 用途 |
| --- | --- |
| `Board.onChange(fn)` | 接收当前快照，首次初始化与参数／绑定／只读状态／主题变化时调用；返回取消订阅函数。 |
| `Board.ready` | 初始化Promise，解析为当前快照；可用于一次性异步初始化。 |
| `Board.getState()`、`Board.getInputs()` | 读取参数／绑定值的副本。 |
| `Board.setState(patch)` | 浅合并自身参数并立即通知本卡监听器；宿主合并高频事件并在约350ms静止后保存。 |
| `Board.reset()` | 恢复自身 initial_state，不修改外部绑定来源。网页“重置参数”按钮等价。 |
| `Board.rendered()` | 在实际绘图完成后报告就绪；等待字体及两次布局调度，画面外卡片使用定时回退。 |
| `Board.deferReady()` | 异步绘图时先调用，关闭同步HTML的自动就绪；完成后必须调用rendered。 |
| `Board.math(element, latex, options)` | 内置KaTeX公式渲染；options可含displayMode等，trust始终为false。 |

onChange快照为 `{state, inputs, inputErrors, initialState, readOnly, theme, mode}`。theme为light/dark；mode为live/screenshot。使用CSS变量 `--board-bg`、`--board-ink` 和当前theme保证明暗可读。程序应根据readOnly禁用写控件；截图、锁定、离线或有未保存的属性编辑时为只读。SDK及宿主也拒绝相应的参数写入。

在onChange中绘图，在控件事件中调用setState；避免在每次onChange中无条件setState，造成反馈循环。动画可以在浏览器中使用requestAnimationFrame；在screenshot模式显示确定的一帧并调用rendered。异步初始化示例：

```javascript
Board.deferReady();
Board.onChange(async ({state,inputs,mode}) => {
  await prepareLocalGeometry();
  draw(state,inputs);
  Board.rendered();
});
```

代码报错、Promise拒绝或前台可见期间累计超过10秒未就绪，会显示错误和重新运行入口。卡片在相机范围外或页面在后台时暂停超时计数。停止按钮移除当前程序页面；重新运行加载最新已保存的代码和参数，有未保存参数时先提示处理草稿。源码更新会在无参数草稿时自动加载；单纯保存参数、移动卡片和调整宽高保留当前程序页面。

## 跨卡片绑定

```json
{"omega":{"card_id":"card_真实标识","path":"/state/value"}}
```

bindings最多16项，别名为字母开头、最多32字符的标识符。path使用JSON Pointer，仅能读取来源卡片state下的字段，支持数组和 `~0`／`~1` 转义。例如 `/state/checked`、`/state/values/matrix/0/1`。创建／改变绑定时核对来源和路径，并拒绝自引用或依赖环。删除来源不级联删除演示；路径失效时也不自动改成0，宿主显示输入错误并隐藏过期图形，恢复来源后继续显示。

绑定是只读输入，不自动覆盖自身state。程序可以使用 `{...state,...inputs}` 让绑定覆盖同名参数；应禁用对应卡内控件。要修改外部参数，用户操作来源卡片，Agent使用正常的edit_card权限与版本检查。来源的本页滑条草稿会立即驱动演示；其他页面看到已保存值，仍经现有1.5秒同步更新。

MCP修改 `changes.state.values` 时应先读取并合并需要保留的参数，再提交完整values对象。它是state的一个字段，服务端不会对values内部再次深合并。只更新source或bindings则保留当前values。程序保存使用expected_content_version；移动使用expected_layout_version，互不覆盖。内容版本冲突时保留本页参数草稿并暂停自动保存，重新运行可采用最新版本；正常断线的未知结果继续走原command_id回执核对。

## 截图与协作

get_board_screenshot复用同一演示运行环境，先定位截图相机，再为图中可见演示最多等待约9秒就绪。单卡截图只展示目标卡片，但会携带同一冻结快照中的绑定来源，读取期间不写入参数。data.interactive包含各演示的ready、error、math_errors、body_overflow、input_errors和sources的内容版本；warnings列出无法完成的演示。区域外的演示可保持ready=false；对图中演示未就绪的截图先修正程序或数据来源，再讨论图形结果。

讨论中用MCP读取state.values与绑定来源的实际值，而不是仅根据截图猜参数。拖动只触发浏览器计算和共享参数保存，仍不会自动唤起Agent推理。Agent创建演示后应截图检查就绪、公式、标签、范围和奇异点；数学上需连续相角时明确分支约定，零向量应显示相角无定义。

## 运行范围

程序运行于带 `sandbox="allow-scripts"` 的独立iframe，不提供allow-same-origin、宿主页DOM／存储、会话令牌、MCP调用或本地文件接口。通过校验来源窗口与每实例随机标识的消息通道，仅交换本卡参数和声明的只读输入。CSP阻止fetch、外部脚本／样式／图片、嵌套frame、worker与表单提交；KaTeX及字体由宿主内联提供。导入HTML文件继续作为普通文件下载，只有interactive类型的source在该环境中执行。

此环境用于可信用户及其Agent生成的数学演示，不是敌对代码的操作系统隔离器：第一版没有严格CPU／内存配额，iframe沙箱也不能当作网络防火墙。页面自行导航后会停止该实例，但导航请求可能已经发出。截图浏览器另有请求拦截，禁止非本地来源。复杂或外部来源代码应先审阅再使用。

已有MCP进程需重新连接加载第七种类型；程序只依赖现有网页库，不新增运行安装项。类型与约束以get_card_types(type="interactive")为准。
