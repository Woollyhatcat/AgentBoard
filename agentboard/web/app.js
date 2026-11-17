"use strict";

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const clone = value => structuredClone(value);
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const TYPES = {
  text: {label: "文本", icon: "▤", hint: "目标、笔记与阶段结论"},
  image: {label: "图片", icon: "▧", hint: "让图像和说明留在眼前"},
  file: {label: "文件", icon: "▱", hint: "资料副本与工作文件引用"},
  slider: {label: "滑条", icon: "☷", hint: "直观调整一个共享参数"},
  checkbox: {label: "勾选框", icon: "☑", hint: "明确表达确认与验证状态"},
  input: {label: "输入框", icon: "⌨", hint: "补充文字或精确数值"},
  interactive: {label:"交互演示",icon:"◇",hint:"可生成的图形、动画与数学实验"},
};
const DEFAULTS = {
  text: {title: "任务说明", props: {format: "markdown", font_size: 16}, state: {text: ""}},
  image: {title: "参考图片", props: {fit: "contain", alt: ""}, state: {asset_id: "", caption: ""}},
  file: {title: "参考文件", props: {preview: "auto"}, state: {asset_id: "", note: ""}},
  slider: {title: "共享参数", props: {min: 0, max: 10, step: 0.1, unit: "", precision: 2}, state: {value: 2.5}},
  checkbox: {title: "验证状态", props: {label: "已通过实验验证"}, state: {checked: false}},
  input: {title: "补充信息", props: {mode: "text", placeholder: "在这里输入…", required: false, max_length: 500}, state: {value: null}},
  interactive: {title:"复平面向量实验",props:{source:"",initial_state:{omega:0,sigma:-2,beta:0},bindings:{}},state:{values:{omega:0,sigma:-2,beta:0}}},
};
const app = {
  boardId: "board_local", session: null, cards: new Map(), assets: new Map(), info: null,
  revision: 0, connected: false, loaded: false, syncing: false, editor: null,
  pending: new Map(), inline: new Map(), selected: null, query: "", events: [], lastError: null, modalDirty: false,
  typeDefs: new Map(), dragging: null, suppressClickUntil: 0,
  boards: [], switching: false,
};
function setSidebarHidden(hidden) {
  $("#workspace-sidebar").hidden = hidden;
  document.body.classList.toggle("sidebar-hidden", hidden);
  const label = hidden ? "展开侧边栏" : "隐藏侧边栏";
  $("#sidebar-toggle").setAttribute("aria-expanded", String(!hidden));
  $("#sidebar-toggle").title = label;
  $("#sidebar-toggle-label").textContent = label;
  try { localStorage.setItem("agentboard:sidebar:hidden", String(hidden)); } catch { /* Optional storage. */ }
}
try { setSidebarHidden(localStorage.getItem("agentboard:sidebar:hidden") === "true"); } catch { setSidebarHidden(false); }
$("#sidebar-toggle").addEventListener("click", () => setSidebarHidden(!$("#workspace-sidebar").hidden));

const space = new FreeCanvas($(".canvas-wrapper"), $("#canvas"),
  () => [...app.cards.values()].filter(card => card.lifecycle === "active"), () => app.selected);
const interactive = new InteractiveCards({
  getCards:() => app.bindingCards || app.cards,
  getLiveState:card => {
    const own = interactive.records.get(card.id);
    if (card.type === "interactive" && own?.dirty) return {values:own.values};
    const draft = app.inline.get(card.id);
    return draft ? {...card.state,[card.type === "checkbox" ? "checked" : "value"]:draft.value} : card.state;
  },
  capture:() => location.pathname === "/__capture__",
  readOnly:card => location.pathname === "/__capture__" || !app.connected || card.locked ||
    !!(app.editor?.base.id === card.id && app.editor.dirty) || (cardBusy(card.id) && !interactive.isSaving(card.id)),
  isBusy:id => cardBusy(id),
  save:(id,values,version,accept) => mutate("edit_card",{card_id:id,expected_content_version:version,changes:{state:{values}}},
    {cardId:id,onSuccess:data => accept(data.card)}),
  changed:() => updateSaveStatus(),
});
const updateButton = $("#agent-updates-btn");
updateButton.addEventListener("click", () => {
  if (app.lastUpdated) locate(app.lastUpdated);
  updateButton.disabled = true; updateButton.title = "暂无待查看的 Agent 更新";
});

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "data") Object.assign(node.dataset, value);
    else if (key in node) node[key] = value;
    else node.setAttribute(key, value);
  }
  for (const child of children.flat(Infinity)) {
    if (child !== null && child !== undefined && child !== false) node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

function toast(message, error = false) {
  const node = el("div", {class: "toast" + (error ? " error" : ""), text: message});
  $("#toasts").append(node);
  setTimeout(() => node.remove(), error ? 9000 : 4500);
}

function date(value) {
  return new Intl.DateTimeFormat("zh-CN", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"}).format(new Date(value));
}
function size(value) { return value == null ? "大小未知" : value < 1024 ? value + " B" : value < 1048576 ? (value / 1024).toFixed(1) + " KB" : (value / 1048576).toFixed(1) + " MB"; }
function cardBusy(id) { return [...app.pending.values()].some(item => item.cardId === id); }
function blocked(card) { return !app.connected || card.locked || cardBusy(card.id) || !!(app.editor?.base.id === card.id && app.editor.dirty); }

async function rpc(tool, args = {}) {
  const response = await fetch("/api/tools/" + tool, {
    method: "POST", headers: {"Content-Type": "application/json", "X-AgentBoard-Session": app.session},
    body: JSON.stringify({...(["list_boards","create_board"].includes(tool) ? {} : {board_id: app.boardId}), ...args}),
    signal: AbortSignal.timeout(35000),
  });
  if (!response.ok) {
    const error = new Error("服务连接异常（" + response.status + "）。");
    error.sessionInvalid = response.status === 403;
    throw error;
  }
  const result = await response.json();
  if (!result.ok) {
    const error = new Error(result.error.message);
    error.business = result.error;
    error.result = result;
    throw error;
  }
  return result.data;
}

async function allPages(tool, args = {}) {
  let cursor, result, items = [];
  do {
    result = await rpc(tool, {...args, limit: 100, ...(cursor ? {cursor} : {})});
    items.push(...result.items);
    cursor = result.next_cursor;
  } while (cursor);
  return {...result, items};
}

function setConnection(value) {
  app.connected = value;
  $("#connection").classList.toggle("offline", !value);
  $("#connection").textContent = value ? "已连接 · 本地服务" : "离线 · 草稿保留中";
  $("#offline-banner").hidden = value;
  for (const id of ["add-btn","import-btn","settings-btn","trash-btn","activity-btn","first-card-btn","example-btn"]) $("#"+id).disabled = !value || !app.boardId;
  if (app.boardId) {
    $("#empty h2").textContent = "从第一张卡片开始";
    $("#empty p").textContent = "记下目标、放入资料，或用控件表达你的想法。连接 Agent 后，一起继续讨论。";
  }
  for (const card of app.cards.values()) updateCardDisabled(card);
  if (app.editor) refreshConflict();
  updateSaveStatus();
}

function updateSaveStatus() {
  const dirty = app.editor?.dirty || app.inline.size || app.modalDirty || interactive.hasDraft();
  const conflict = app.editor?.dirty && editorStale();
  const value = !app.connected ? "离线 · 已保存内容可能过时" : conflict ? "存在冲突 · 草稿已保留" : app.pending.size ? "操作结果确认中…" : app.lastError ? "保存失败 · 待处理" : dirty ? "有未保存的草稿" : "所有修改已保存";
  $("#save-status").textContent = value;
  $("#revision").textContent = "revision " + app.revision;
}

async function reconcilePending() {
  for (const [command, pending] of [...app.pending]) {
    const statusArgs = {board_id:pending.args.board_id ?? null,command_id:command};
    if (statusArgs.board_id === null) delete statusArgs.board_id;
    const status = await rpc("get_operation_status", {...statusArgs,...(["create_board","import_board"].includes(pending.tool) ? {board_id:undefined} : {})});
    if (status.status === "pending") continue;
    if (status.status === "unknown") {
      // Reuse the original command and input. Never guess with a new command.
      try { if (pending.retry) await pending.retry(); else await rpc(pending.tool, pending.args); }
      catch (error) { if (!error.business) throw error; }
    }
    const confirmed = status.status === "unknown" ? await rpc("get_operation_status", {...statusArgs,...(["create_board","import_board"].includes(pending.tool) ? {board_id:undefined} : {})}) : status;
    if (confirmed.status === "succeeded" || confirmed.status === "failed") {
      app.pending.delete(command);
      const result = confirmed.result;
      if (result.ok) {
        if (result.data.card) applyCard(result.data.card);
        if (pending.cardId) app.inline.delete(pending.cardId);
        pending.onSuccess?.(result.data);
        toast("已核对先前操作：保存成功。");
      } else {
        app.lastError = result.error.message;
        const draft = app.inline.get(pending.cardId);
        if (draft) draft.error = result.error.message;
        toast("已核对先前操作：" + result.error.message, true);
      }
    }
  }
}

async function mutate(tool, args, {cardId = null, onSuccess = null} = {}) {
  if (!app.connected) throw new Error("当前离线，请保留草稿并等待重新连接。");
  if (cardId && cardBusy(cardId)) throw new Error("此卡片的上一操作仍在确认中。");
  if (app.switching) throw new Error("正在切换黑板，请稍后操作。");
  if (!cardId && [...app.pending.values()].some(item => item.tool === tool)) throw new Error("同类操作的结果仍待确认，请等待原命令核对完成。");
  const request = {...(tool === "create_board" ? {} : {board_id:app.boardId}),command_id: crypto.randomUUID(), ...args};
  const pending = {tool, args: request, cardId, onSuccess};
  app.pending.set(request.command_id, pending);
  app.lastError = null;
  if (cardId) updateCardDisabled(app.cards.get(cardId));
  updateSaveStatus();
  try {
    const data = await rpc(tool, request);
    app.pending.delete(request.command_id);
    if (data.card) applyCard(data.card);
    if (data.asset) app.assets.set(data.asset.id, data.asset);
    onSuccess?.(data);
    return data;
  } catch (error) {
    if (error.business) {
      app.pending.delete(request.command_id);
      app.lastError = error.message;
    } else {
      setConnection(false);
      error.message = "连接中断，结果待确认。草稿已保留；重连后将核对原命令。";
    }
    throw error;
  } finally {
    if (cardId) updateCardDisabled(app.cards.get(cardId));
    updateSaveStatus();
  }
}

async function sync() {
  if (app.syncing || app.switching) return;
  app.syncing = true;
  app.syncDone = new Promise(resolve => { app.resolveSync = resolve; });
  try {
    if (!app.session || !app.connected) {
      const response = await fetch("/api/session", {signal: AbortSignal.timeout(5000)});
      if (!response.ok) throw new Error("服务连接失败。");
      const session = await response.json();
      app.session = session.session;
      if (!app.started) {
        let saved;
        try { saved = localStorage.getItem("agentboard:current-board"); } catch { /* Optional storage. */ }
        app.boardId = new URL(location.href).searchParams.get("board_id") || saved || session.board_id;
        space.switchBoard(app.boardId); app.started = true;
      }
    }
    const recovering = !app.connected;
    await reconcilePending();
    app.boards = (await allPages("list_boards")).items;
    renderBoardNavigation();
    if (!app.boards.some(board => board.id === app.boardId)) {
      if (hasBoardDrafts()) {
        setConnection(false); toast("当前黑板已删除，草稿保留在此页面。请复制需要的内容后再切换。",true); return;
      }
      resetBoard(app.boards[0]?.id || null);
    }
    if (!app.boardId) {
      setConnection(true); renderNoBoards(); return;
    }
    app.info = await rpc("get_board_info");
    $("#current-board-name").textContent = app.info.name + (app.info.locked ? " · 删除保护" : "");
    $("#copy-board").textContent = app.boardId + " ⧉";
    document.title = app.info.name + " · AgentBoard";
    $("#trash-count").textContent = app.info.deleted_card_count;
    if (!app.loaded) {
      const snapshot = await allPages("get_board_snapshot", {view: "full"});
      const definitions = await rpc("get_card_types");
      app.typeDefs = new Map(definitions.types.map(item => [item.type, item]));
      app.cards = new Map(snapshot.items.map(card => [card.id, card]));
      app.revision = snapshot.revision;
      app.loaded = true;
      const assets = await allPages("list_assets");
      assets.items.forEach(asset => app.assets.set(asset.id, asset));
      renderGrid();
      await loadRecentEvents();
      const target = new URL(location.href).searchParams.get("card_id");
      const requestedBoard = new URL(location.href).searchParams.get("board_id");
      if (requestedBoard && requestedBoard !== app.boardId) toast("找不到请求的看板，已保留当前看板。", true);
      if (target) locate(target);
    }
    const changes = await allPages("get_changes", {since_revision: app.revision});
    for (const event of changes.items) {
      if (!event.card_after) { app.events.push(event); continue; }
      applyCard(event.card_after, true);
      app.events.push(event);
      if (event.actor.kind === "agent") {
        app.lastUpdated = event.card_id; updateButton.disabled = false; updateButton.title = "定位 Agent 最近更新的卡片";
        toast(`Agent ${event.kind === "created" ? "添加" : "更新"}了「${event.card_after.title}」，可点击“查看 Agent 更新”定位。`);
      }
    }
    app.events = app.events.slice(-50);
    app.revision = changes.to_revision;
    setConnection(true);
    if (recovering && app.editor?.dirty) refreshConflict();
  } catch (error) {
    if (error.business?.code === "SNAPSHOT_REQUIRED" || error.business?.code === "CURSOR_EXPIRED") {
      app.loaded = false;
      // Preserve the editor and inline drafts while obtaining a fresh authoritative collection.
    } else {
      if (error.sessionInvalid) app.session = null;
      setConnection(false);
    }
  } finally {
    app.syncing = false;
    app.resolveSync?.();
    updateSaveStatus();
    if (app.recoveredImport && !hasBoardDrafts()) {
      const id = app.recoveredImport; app.recoveredImport = null;
      setTimeout(() => switchBoard(id).catch(error => toast(error.message,true)),0);
    }
  }
}

function hasBoardDrafts() {
  return !!(app.editor?.dirty || app.inline.size || app.modalDirty || interactive.hasDraft() || app.pending.size || app.dragging);
}

function renderBoardNavigation() {
  const select = $("#board-select");
  const options = app.boards.map(board => ({value:board.id,text:board.name+(board.locked ? " · 已保护" : "")}));
  if (!equal([...select.options].map(option => ({value:option.value,text:option.text})),options)) {
    select.replaceChildren(...options.map(option => el("option",option)));
  }
  select.value = app.boardId || "";
  select.disabled = !app.boards.length;
}

function resetBoard(id) {
  closeEditor();
  for (const record of interactive.records.values()) { clearTimeout(record.timer); interactive.halt(record); }
  interactive.records.clear();
  app.boardId = id; app.cards.clear(); app.assets.clear(); app.inline.clear(); app.events = [];
  app.info = null; app.loaded = false; app.revision = 0; app.query = ""; app.lastUpdated = null; app.lastError = null;
  $("#search").value = ""; updateButton.disabled = true;
  $("#canvas").replaceChildren();
  space.switchBoard(id || "none");
  try {
    if (id) localStorage.setItem("agentboard:current-board",id);
    else localStorage.removeItem("agentboard:current-board");
  } catch { /* Optional storage. */ }
  const url = new URL(location.href);
  if (id) url.searchParams.set("board_id",id); else url.searchParams.delete("board_id");
  url.searchParams.delete("card_id"); history.replaceState(null,"",url);
  renderBoardNavigation(); updateCounts();
}

function renderNoBoards() {
  $("#current-board-name").textContent = "尚无黑板";
  $("#copy-board").textContent = "尚无黑板";
  $("#add-btn").disabled = true; $("#import-btn").disabled = true;
  $("#settings-btn").disabled = true; $("#example-btn").disabled = true; $("#first-card-btn").disabled = true;
  $("#empty h2").textContent = "从新建一块黑板开始";
  $("#empty p").textContent = "在侧边栏点击“新建黑板”，创建独立的讨论空间。";
}

async function switchBoard(id) {
  if (id === app.boardId) return true;
  if (hasBoardDrafts()) { toast("请先保存或取消当前草稿，并等待操作确认后再切换黑板。",true); renderBoardNavigation(); return false; }
  app.switching = true;
  try {
    if (app.syncing) await app.syncDone;
    if (hasBoardDrafts()) { toast("仍有未保存的操作，请先处理后切换。",true); return false; }
    resetBoard(id); closeModal();
  } finally { app.switching = false; }
  await sync(); return true;
}

async function loadRecentEvents() {
  try {
    const result = await allPages("get_changes", {since_revision: Math.max(0, app.revision - 50)});
    app.events = result.items.slice(-50);
  } catch { /* The current snapshot remains usable if earlier changes have expired. */ }
}

function applyCard(card, external = false) {
  const current = app.cards.get(card.id);
  if (current && current.version > card.version) return;
  app.cards.set(card.id, card);
  // Only a fully consumed snapshot/change round advances the sync cursor.
  // A write receipt can be newer than unseen changes on other cards.
  if (app.dragging !== card.id) renderCard(card, external);
  updateCounts();
  space.layers();
  if (app.editor?.base.id === card.id) {
    if (app.editor.dirty) refreshConflict();
    else openEditor(card, true);
  }
  interactive.refresh();
}

function updateCounts() {
  const count = [...app.cards.values()].filter(card => card.lifecycle === "active").length;
  $("#nav-count").textContent = count;
  const visible = [...app.cards.values()].filter(matches);
  $("#empty").hidden = count !== 0;
  $("#no-results").hidden = !(count > 0 && visible.length === 0);
  $("#canvas").hidden = visible.length === 0;
}

function matches(card) {
  return card.lifecycle === "active" && (!app.query || card.id === app.query || card.title.toLocaleLowerCase().includes(app.query.toLocaleLowerCase()));
}
function renderGrid() {
  $("#canvas").replaceChildren();
  [...app.cards.values()].sort((a, b) => a.layout.y - b.layout.y || a.layout.x - b.layout.x || a.id.localeCompare(b.id)).forEach(card => renderCard(card));
  updateCounts();
  space.layers(); space.initialFit();
  if (app.query) space.fit([...app.cards.values()].filter(matches));
}

function cardElement(id) { return document.getElementById(id); }
function updateCardDisabled(card) {
  if (!card) return;
  const node = cardElement(card.id);
  if (!node) return;
  const disabled = blocked(card);
  $$('[data-edit-control="true"]', node).forEach(control => { control.disabled = disabled; });
  node.classList.toggle("pending", cardBusy(card.id));
  const foot = $(".card-status", node);
  if (foot) foot.textContent = cardBusy(card.id) ? "保存确认中…" : app.inline.has(card.id) ? "草稿未保存" : card.locked ? "已锁定" : "已保存";
  if (card.type === "interactive") interactive.updateCard(card);
}

function renderCard(card, flash = false) {
  const old = cardElement(card.id);
  if (!matches(card)) { old?.remove(); updateCounts(); return; }
  if (old && card.type === "interactive") {
    Object.assign(old.style,{left:card.layout.x+"px",top:card.layout.y+"px",width:card.layout.w+"px",height:card.layout.h+"px"});
    $(".card-head h2",old).textContent = card.title;
    $(".card-head h2",old).title = card.title;
    $(".card-foot>span",old).textContent = `${TYPES[card.type].label} · ${card.updated_by.display_name} · ${date(card.updated_at)}`;
    old.setAttribute("aria-label",card.title+"，交互演示");
    const description = $(".card-description",old);
    if (description && !card.description) description.remove();
    else if (description) description.textContent = card.description;
    else if (card.description) $(".card-body",old).prepend(el("p",{class:"card-description",text:card.description}));
    const lock = $(".locked-label",old);
    if (card.locked && !lock) $(".card-head",old).insertBefore(el("span",{class:"locked-label",text:"锁定"}),$(".card-head .icon-button",old));
    else if (!card.locked) lock?.remove();
    updateCardDisabled(card); space.layers(); return;
  }
  const focus = old?.contains(document.activeElement) ? document.activeElement : null;
  const focusKey = focus?.dataset.focusKey;
  const selection = focus && typeof focus.selectionStart === "number" ? [focus.selectionStart, focus.selectionEnd] : null;
  const type = TYPES[card.type] || {label: card.type, icon: "◇"};
  const node = el("article", {id: card.id, class: "card" + (app.selected === card.id ? " selected" : "") + (flash ? " flash" : ""),
    tabIndex: 0, data: {type: card.type}, "aria-label": card.title + "，" + type.label,
    onClick: event => { if (Date.now() > app.suppressClickUntil && !event.target.closest("button,input,select,textarea,a,label")) selectCard(card.id); },
    onKeydown: event => { if (event.target === node && event.key === "Enter") { event.preventDefault(); selectCard(card.id); } },
  });
  Object.assign(node.style, {left: card.layout.x + "px", top: card.layout.y + "px", width: card.layout.w + "px",
    height: card.layout.height_mode === "fixed" ? card.layout.h + "px" : "auto"});
  node.dataset.heightMode = card.layout.height_mode;
  const handle = el("button", {class: "drag-handle", text: "⠿", title: "拖动卡片；键盘用户可在属性中修改布局", "aria-label": "拖动「" + card.title + "」", data: {editControl: "true"}});
  handle.addEventListener("pointerdown", event => startDrag(event, app.cards.get(card.id), false));
  const head = el("header", {class: "card-head"}, handle, el("span", {class: "type-icon", text: type.icon}),
    el("h2", {title: card.title, text: card.title}), card.locked ? el("span", {class: "locked-label", text: "锁定"}) : null,
    el("button", {class: "icon-button", text: "···", "aria-label": "「" + card.title + "」卡片菜单", onClick: () => cardMenu(card.id)}));
  head.addEventListener("pointerdown", event => {
    if (!event.target.closest("button") || event.target.closest(".drag-handle")) startDrag(event, app.cards.get(card.id), false);
  });
  node.append(head);
  const body = el("div", {class: "card-body"});
  renderContent(body, card);
  if (card.type === "text") body.style.fontSize = card.props.font_size + "px";
  node.append(body, el("footer", {class: "card-foot"}, el("span", {text: `${type.label} · ${card.updated_by.display_name} · ${date(card.updated_at)}`, title: card.id + " · v" + card.version}), el("span", {class: "card-status", text: "已保存"})));
  const resize = el("button", {class: "resize-handle", "aria-label": "调整「" + card.title + "」尺寸", title: "调整尺寸", data: {editControl: "true"}});
  resize.addEventListener("pointerdown", event => startDrag(event, app.cards.get(card.id), true));
  node.append(resize);
  if (old) old.replaceWith(node); else $("#canvas").append(node);
  updateCardDisabled(card);
  space.layers();
  if (focusKey) {
    const replacement = $$('[data-focus-key]', node).find(control => control.dataset.focusKey === focusKey);
    replacement?.focus({preventScroll: true});
    if (selection && replacement?.setSelectionRange) replacement.setSelectionRange(...selection);
  }
}

function markdown(text) { return BoardText.render(text); }

function renderContent(body, card) {
  const p = card.props, s = card.state, draft = app.inline.get(card.id);
  if (card.description) body.append(el("p", {class: "card-description", text: card.description}));
  if (card.type === "text") body.append(p.format === "markdown" ? markdown(s.text) : el("div", {class: "plain-text", text: s.text || "暂无正文，点击编辑添加。"}));
  else if (card.type === "interactive") interactive.mount(body,card);
  else if (card.type === "slider") {
    const value = draft ? draft.value : s.value;
    const display = el("strong", {text: value});
    const slider = el("input", {type: "range", min: p.min, max: p.max, step: p.step, value,
      "aria-label": card.title, data: {editControl: "true", focusKey: "range"}});
    const remember = () => {
      const previous = app.inline.get(card.id);
      app.inline.set(card.id, {value: Number(slider.value), version: previous?.version ?? card.content_version, error: null});
      display.textContent = slider.value; updateSaveStatus(); interactive.refresh();
    };
    slider.addEventListener("input", remember);
    let timer;
    slider.addEventListener("pointerup", () => commitInline(card.id));
    slider.addEventListener("keydown", event => { if (["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"].includes(event.key)) { clearTimeout(timer); timer = setTimeout(() => commitInline(card.id), 300); } });
    slider.addEventListener("blur", () => { clearTimeout(timer); commitInline(card.id); });
    const exact = el("input", {type: "number", class: "inline-input number", value, step: p.step,
      "aria-label": card.title + "精确数值", data: {editControl: "true", focusKey: "exact"}});
    bindInlineInput(exact, card, true);
    body.append(el("div", {class: "value-row"}, display, el("span", {text: p.unit || "当前值"}), exact), slider,
      el("div", {class: "range-labels"}, el("span", {text: p.min}), el("span", {text: p.max})));
  } else if (card.type === "checkbox") {
    const control = el("input", {type: "checkbox", checked: draft ? draft.value : s.checked,
      data: {editControl: "true", focusKey: "checkbox"}, onChange: event => {
        app.inline.set(card.id, {value: event.target.checked, version: card.content_version}); interactive.refresh(); commitInline(card.id);
      }});
    body.append(el("label", {class: "check-label"}, control, p.label));
  } else if (card.type === "input") {
    const control = el("input", {class: "inline-input" + (p.mode === "number" ? " number" : ""),
      type: p.mode === "number" ? "number" : "text", value: draft ? draft.raw ?? draft.value ?? "" : s.value ?? "",
      placeholder: p.placeholder, "aria-label": card.title, data: {editControl: "true", focusKey: "input"}});
    if (p.mode === "text") control.maxLength = p.max_length * 2;
    bindInlineInput(control, card, p.mode === "number");
    body.append(control, el("span", {class: "asset-meta", text: p.mode === "number" ? p.unit : p.required ? "必填" : "可选 · 留空表示未填写"}));
  } else if (card.type === "image") {
    const image = el("img", {class: "image-preview", src: "/api/assets/" + encodeURIComponent(s.asset_id), alt: p.alt || card.title,
      onClick: () => assetViewer(s.asset_id), onError: () => { image.hidden = true; const note = $(".asset-unavailable", body); if (note) note.hidden = false; }});
    image.style.objectFit = p.fit;
    body.append(image, el("p", {class: "inline-error asset-unavailable", hidden: true, text: "图片资源不可用，请查看资源状态或重新指定资源。"}), el("p", {class: "asset-caption", text: s.caption}), el("button", {class: "text-button", text: "查看资源 ↗", onClick: () => assetViewer(s.asset_id)}));
  } else if (card.type === "file") {
    const asset = app.assets.get(s.asset_id);
    body.append(el("div", {class: "file-preview"}, el("span", {class: "file-symbol", text: "▱"}),
      el("div", {}, el("p", {class: "asset-name", text: asset?.name || s.asset_id}),
        el("p", {class: "asset-meta", text: `${asset?.mode === "reference" ? "受控引用" : "导入副本"} · ${size(asset?.size_bytes)}`}))),
      s.note ? el("p", {class: "asset-caption", text: s.note}) : null,
      el("div", {class: "file-actions"}, el("button", {text: p.preview === "none" ? "查看资源信息" : "查看文件", onClick: () => assetViewer(s.asset_id, p.preview === "none")}),
        el("a", {text: "下载原文件 ↗", href: "/api/assets/" + encodeURIComponent(s.asset_id) + "/download", download: ""})));
  } else body.append(el("p", {text: "此卡片类型暂不支持展示，基础信息已保留。"}));
  if (draft?.error) body.append(el("p", {class: "inline-error", text: draft.error}),
    el("p", {class: "inline-notice", text: "最后已保存值：" + String(s.value ?? s.checked ?? "未填写")}),
    el("button", {class: "text-button", text: "读取最新状态并审阅草稿 →", onClick: () => selectCard(card.id, true)}));
}

function bindInlineInput(control, card, numeric) {
  control.addEventListener("input", () => {
    const old = app.inline.get(card.id);
    app.inline.set(card.id, {raw: control.value, value: numeric ? (control.value === "" ? null : Number(control.value)) : control.value,
      version: old?.version ?? card.content_version, error: null});
    updateSaveStatus(); interactive.refresh();
  });
  control.addEventListener("keydown", event => {
    if (event.key === "Enter") { event.preventDefault(); commitInline(card.id); }
    if (event.key === "Escape") { app.inline.delete(card.id); renderCard(app.cards.get(card.id)); updateSaveStatus(); }
  });
  control.addEventListener("blur", () => commitInline(card.id));
}

async function commitInline(id) {
  const card = app.cards.get(id), draft = app.inline.get(id);
  if (!draft || blocked(card) || cardBusy(id)) return;
  const key = card.type === "checkbox" ? "checked" : "value";
  let value = draft.value;
  if (card.type === "input" && card.props.mode === "text" && value === "") value = null;
  if (equal(value, card.state[key]) && draft.version === card.content_version) {
    app.inline.delete(id); updateSaveStatus(); return;
  }
  try {
    if (typeof value === "number" && !Number.isFinite(value)) throw new Error("请输入有效数值。");
    if (card.type === "slider" && value === null) throw new Error("滑条数值不能为空。");
    await mutate("edit_card", {card_id: id, expected_content_version: draft.version, changes: {state: {[key]: value}}}, {cardId: id,
      onSuccess: () => { app.inline.delete(id); renderCard(app.cards.get(id)); }});
  } catch (error) {
    draft.error = error.message;
    app.inline.set(id, draft);
    renderCard(app.cards.get(id));
    toast(error.message, true);
  }
}

let modalOnClose = null;
function showModal(title, content, eyebrow = "AGENTBOARD", close = null) {
  app.modalDirty = false;
  modalOnClose = close;
  $("#modal-title").textContent = title;
  $("#modal-eyebrow").textContent = eyebrow;
  $("#modal-body").replaceChildren(content);
  if (!$("#modal").open) $("#modal").showModal();
}
function closeModal() {
  if (modalOnClose && modalOnClose() === false) return;
  $("#modal").close(); modalOnClose = null; app.modalDirty = false; updateSaveStatus();
}
$("#modal-close").addEventListener("click", closeModal);
$("#modal").addEventListener("cancel", event => { event.preventDefault(); closeModal(); });

function chooseType() {
  const grid = el("div", {class: "type-grid"});
  for (const [type, info] of Object.entries(TYPES)) grid.append(el("button", {class: "type-choice", onClick: () => createCardForm(type)}, el("span", {text: info.icon}), el("div", {}, el("b", {text: info.label}), el("p", {text: info.hint}))));
  showModal("把想法放上黑板", el("div", {}, el("p", {class: "dialog-description", text: "选择一类卡片。创建后，你与 Agent 可以继续修改同一份内容。"}), grid), "ADD A CARD");
}

function field(label, path, value, options = {}) {
  let input;
  const attrs = {name: path, data: {path, kind: options.kind || "string"}};
  if (options.choices) {
    input = el("select", attrs, options.choices.map(item => el("option", {value: typeof item === "string" ? item : item[0], text: typeof item === "string" ? item : item[1]})));
    input.value = value ?? "";
  } else if (options.kind === "boolean") input = el("input", {...attrs, type: "checkbox", checked: value});
  else if (options.multiline) input = el("textarea", {...attrs, class: options.tall ? "tall" : "", value: value ?? "", rows: options.tall ? 7 : 3});
  else input = el("input", {...attrs, type: options.kind === "number" ? "number" : "text", step: "any", value: value ?? ""});
  const node = el("label", {class: "field"}, label, input);
  if (options.min !== undefined) input.min = options.min;
  if (options.max !== undefined) input.max = options.max;
  if (options.hint) node.append(el("small", {text: options.hint}));
  return node;
}

function cardFields(card, editing = false) {
  const root = el("div");
  const p = card.props, s = card.state, type = card.type;
  root.append(el("span", {class: "section-label", text: "内容 / CONTENT"}), field("标题", "title", card.title), field("描述与用途", "description", card.description || "", {multiline: true}));
  if (type === "text") {
    root.append(field("完整正文（Markdown / LaTeX 源码）", "state.text", s.text, {multiline: true, tall: true}),
      el("div", {class: "field-grid"},
        field("正文基准字号", "props.font_size", p.font_size ?? 16, {kind: "number", min: 12, max: 48}),
        field("正文格式", "props.format", p.format, {choices: [["markdown", "Markdown + LaTeX"], ["plain", "纯文本"]]})));
    const preview = el("div", {class: "live-preview", "aria-label": "正文实时预览"});
    root.append(el("span", {class: "section-label", text: "实时预览 / PREVIEW"}), preview);
    const paintPreview = () => {
      const value = $('[name="state.text"]', root).value, format = $('[name="props.format"]', root).value;
      preview.style.fontSize = Math.max(12, Math.min(48, Number($('[name="props.font_size"]', root).value) || 16)) + "px";
      preview.replaceChildren(BoardText.render(value, format));
    };
    let previewTimer;
    root.addEventListener("input", () => { clearTimeout(previewTimer); previewTimer = setTimeout(paintPreview, 120); });
    root.addEventListener("change", paintPreview); paintPreview();
  }
  if (type === "interactive") {
    root.append(field("演示源码（HTML / CSS / JavaScript）","props.source",p.source,{multiline:true,tall:true,
      hint:"保存后运行。使用 Board.onChange 接收参数，Board.setState 保存自身参数，Board.rendered 报告绘图就绪。"}),
      field("当前参数（JSON）","state.values",JSON.stringify(s.values,null,2),{kind:"json",multiline:true}),
      field("初始参数（JSON）","props.initial_state",JSON.stringify(p.initial_state,null,2),{kind:"json",multiline:true}),
      field("输入绑定（JSON）","props.bindings",JSON.stringify(p.bindings,null,2),{kind:"json",multiline:true,
        hint:'示例：{"omega":{"card_id":"卡片ID","path":"/state/value"}}。绑定只读；缺失来源会显示错误。'}));
  }
  if (["image", "file"].includes(type)) {
    const assets = [...app.assets.values()].filter(asset => type !== "image" || asset.preview_kind === "image");
    const choices = [["", "请选择已添加的资源"], ...assets.map(asset => [asset.id, `${asset.name} · ${asset.mode === "reference" ? "引用" : "副本"}`])];
    if (s.asset_id && !assets.some(asset => asset.id === s.asset_id)) choices.push([s.asset_id, s.asset_id + "（状态待核对）"]);
    root.append(field("资源", "state.asset_id", s.asset_id, {choices, hint: "先从工具栏导入资源，再在此选择；原关联在成功保存前保持不变。"}));
    root.append(field(type === "image" ? "图片说明" : "文件说明", type === "image" ? "state.caption" : "state.note", type === "image" ? s.caption : s.note, {multiline: true}));
    if (type === "image") root.append(field("替代文字", "props.alt", p.alt), field("显示方式", "props.fit", p.fit, {choices: [["contain", "完整显示"], ["cover", "填满区域"]]}));
    else root.append(field("预览方式", "props.preview", p.preview, {choices: [["auto", "自动（支持时）"], ["none", "不预览"]]}));
  }
  if (type === "slider") {
    root.append(field("当前精确值", "state.value", s.value, {kind: "number"}), el("span", {class: "section-label", text: "控件定义 / DEFINITION"}),
      el("div", {class: "field-grid"}, field("最小值", "props.min", p.min, {kind: "number"}), field("最大值", "props.max", p.max, {kind: "number"}), field("步长", "props.step", p.step, {kind: "number"}), field("显示精度（0–6）", "props.precision", p.precision, {kind: "number"})), field("单位", "props.unit", p.unit));
  }
  if (type === "checkbox") root.append(field("勾选的含义", "props.label", p.label), field("当前已勾选", "state.checked", s.checked, {kind: "boolean"}));
  if (type === "input") {
    root.append(el("span", {class: "section-label", text: "控件定义 / DEFINITION"}));
    if (editing) root.append(el("p", {class: "dialog-description", text: "输入模式：" + (p.mode === "number" ? "数值" : "文本") + "（创建后固定）"}));
    else root.append(field("输入模式", "props.mode", p.mode, {choices: [["text", "文本"], ["number", "数值"]]}));
    root.append(field("当前值", "state.value", s.value, {kind: p.mode === "number" ? "number" : "string", hint: "留空保存为 null；0 是有效数值。"}), field("输入提示", "props.placeholder", p.placeholder), field("必填", "props.required", p.required, {kind: "boolean"}));
    if (p.mode === "text") root.append(field("最长文字（1–2000）", "props.max_length", p.max_length, {kind: "number"}));
    else root.append(el("div", {class: "field-grid"}, field("最小值（可空）", "props.min", p.min, {kind: "number"}), field("最大值（可空）", "props.max", p.max, {kind: "number"}), field("显示精度（0–6）", "props.precision", p.precision, {kind: "number"}), field("单位", "props.unit", p.unit)));
  }
  if (editing) {
    root.append(el("span", {class: "section-label", text: "共享布局 / WORLD"}),
      el("div", {class: "field-grid"}, ...["x", "y", "w", "h", "z"].map(key => field(
        {x: "横坐标 x", y: "纵坐标 y", w: "宽度", h: "固定／估计高度", z: "共享层级"}[key],
        "layout." + key, card.layout[key], {kind: "number"}))));
    if (type === "text") root.append(field("正文高度模式", "layout.height_mode", card.layout.height_mode,
      {choices: [["auto", "内容自适应（长文限高）"], ["expanded", "完整展开正文"], ["fixed", "固定高度，内部滚动"]]}));
    root.append(el("p", {class: "dialog-description", text: "100% 时按画布像素计量，可为负坐标、允许重叠。自动高度不推挤其他卡片。"}));
  }
  return root;
}

function collectFields(root, base) {
  const card = clone(base);
  for (const input of $$('[data-path]', root)) {
    const path = input.dataset.path.split(".");
    let value = input.dataset.kind === "boolean" ? input.checked : input.value;
    if (input.dataset.kind === "number") {
      value = input.value === "" ? null : Number(input.value);
      if (value !== null && !Number.isFinite(value)) throw new Error("请输入有效数值。");
    }
    if (input.dataset.kind === "json") {
      try { value = JSON.parse(input.value); }
      catch { throw new Error(input.closest("label").firstChild.textContent+"不是有效JSON。"); }
    }
    if (path.length === 2) card[path[0]][path[1]] = value;
    else card[path[0]] = value;
  }
  if (card.type === "input" && card.state.value === "") card.state.value = null;
  return card;
}

function diffCard(base, next) {
  const changes = {};
  for (const key of ["title", "description", "props", "state", "layout"]) {
    if (["props", "state", "layout"].includes(key)) {
      const patch = {};
      for (const [name, value] of Object.entries(next[key] || {})) if (!equal(base[key]?.[name], value)) patch[name] = value;
      if (Object.keys(patch).length) changes[key] = key === "layout" ? {space:"world", ...patch} : patch;
    } else if (base[key] !== next[key]) changes[key] = next[key];
  }
  return changes;
}
function mergeCard(base, patch) {
  const card = clone(base);
  for (const [key, value] of Object.entries(patch)) card[key] = ["props", "state", "layout"].includes(key) ? {...card[key], ...value} : value;
  return card;
}

async function createCardForm(type, initial = null) {
  if (!app.connected) { toast("当前离线，请等待重新连接。", true); return; }
  if (type === "interactive" && !initial && !DEFAULTS.interactive.props.source) {
    try {
      const response = await fetch("/examples/complex-angle.html");
      if (!response.ok) throw new Error("演示示例加载失败。");
      DEFAULTS.interactive.props.source = await response.text();
    } catch (error) { toast(error.message,true); return; }
  }
  if (["image", "file"].includes(type)) {
    try { (await allPages("list_assets")).items.forEach(asset => app.assets.set(asset.id, asset)); } catch (error) { toast(error.message, true); }
    if (![...app.assets.values()].some(asset => type !== "image" || asset.preview_kind === "image")) {
      importDialog(asset => createCardForm(type, {...DEFAULTS[type], state: {...DEFAULTS[type].state, asset_id: asset.id}}));
      return;
    }
  }
  const card = {type, type_version: "1.0", description: "", ...clone(initial || DEFAULTS[type])};
  const form = el("form"), fields = cardFields(card), errorBox = el("p", {class: "field-error", role: "alert"});
  const submit = el("button", {class: "primary", text: "创建卡片", type: "submit"});
  let dirty = false, pending = false;
  form.append(fields, errorBox, el("div", {class: "modal-actions"}, el("button", {class: "secondary", text: "返回类型选择", type: "button", onClick: () => { if (!dirty || confirm("放弃此卡片的未保存草稿？")) chooseType(); }}), submit));
  form.addEventListener("input", () => { dirty = true; app.modalDirty = true; updateSaveStatus(); });
  form.addEventListener("change", () => { dirty = true; app.modalDirty = true; updateSaveStatus(); });
  if (type === "input") $('[name="props.mode"]', form)?.addEventListener("change", event => {
    const next = collectFields(form, card), mode = event.target.value;
    next.props = mode === "text" ? {mode, placeholder: next.props.placeholder, required: next.props.required, max_length: 500} : {mode, placeholder: next.props.placeholder, required: next.props.required, min: null, max: null, unit: "", precision: 2};
    next.state = {value: null};
    createCardForm(type, next);
  });
  form.addEventListener("submit", async event => {
    event.preventDefault(); if (pending) return;
    pending = true; submit.disabled = true; errorBox.textContent = "";
    try {
      const next = collectFields(form, card);
      next.layout = space.placement(type, app.typeDefs.get(type)?.default_layout || {w:432,h:336});
      await mutate("push_card", {card: next}, {onSuccess: data => {
        dirty = false; app.modalDirty = false; modalOnClose = null; $("#modal").close(); locate(data.card.id); toast("卡片已创建，与 Agent 共享。");
      }});
    } catch (error) { errorBox.textContent = describeError(error); showFieldErrors(form, error); }
    finally { pending = false; submit.disabled = false; }
  });
  showModal("添加" + TYPES[type].label + "卡片", form, "NEW CARD", () => !dirty || confirm("放弃此卡片的未保存草稿？"));
}

function describeError(error) {
  return error.message + (error.business?.field_errors?.length ? "\n" + error.business.field_errors.map(item => `${item.path}：${item.message}`).join("\n") : "");
}

function showFieldErrors(root, error) {
  $$(".field-validation", root).forEach(node => node.remove());
  $$('[data-path]', root).forEach(input => input.removeAttribute("aria-invalid"));
  for (const item of error.business?.field_errors || []) {
    const path = item.path.replace(/^card\./, "").replace(/^changes\./, "");
    const input = $$('[data-path]', root).find(control => control.dataset.path === path);
    if (input) {
      input.setAttribute("aria-invalid", "true");
      const message = el("span", {class: "inline-error field-validation", text: item.message, id: "validation-" + crypto.randomUUID()});
      input.setAttribute("aria-describedby", message.id); input.closest(".field").append(message);
    }
  }
}

async function guardEditor(action) {
  if (!app.editor?.dirty) { action(); return; }
  const content = el("div", {}, el("p", {class: "dialog-description", text: "当前卡片有未保存的修改，请先处理草稿。"}),
    el("div", {class: "modal-actions"}, el("button", {class: "secondary", text: "继续编辑", onClick: closeModal}),
      el("button", {class: "secondary", text: "放弃草稿", onClick: () => { closeModal(); app.editor.dirty = false; action(); }}),
      el("button", {class: "primary", text: "保存后继续", onClick: async () => { closeModal(); if (await saveEditor()) action(); }})));
  showModal("还有未保存的修改", content);
}

async function selectCard(id, withInline = false) {
  if (app.editor?.base.id === id && !withInline) return;
  guardEditor(() => {
    const card = app.cards.get(id); if (!card) return;
    const draft = app.inline.get(id);
    if (draft && !withInline && !confirm("此卡片有内联草稿。将保留草稿并转到属性面板审阅？")) return;
    app.selected = id; space.layers();
    $$(".card.selected").forEach(node => node.classList.remove("selected")); cardElement(id)?.classList.add("selected");
    openEditor(card);
    if (draft) {
      const key = card.type === "checkbox" ? "state.checked" : "state.value";
      const input = $(`[name="${key}"]`, $("#inspector"));
      if (input) { if (input.type === "checkbox") input.checked = draft.value; else input.value = draft.raw ?? draft.value ?? ""; }
      app.editor.dirty = true;
      app.editor.base.content_version = draft.version;
      app.inline.delete(id);
      refreshConflict(); updateCardDisabled(card);
    }
  });
}

function openEditor(card, refreshing = false, initial = null) {
  const root = $("#inspector");
  const model = initial || card;
  app.editor = {base: clone(card), dirty: !!initial};
  root.hidden = false; root.classList.remove("has-conflict");
  const header = el("header", {class: "inspector-header"}, el("div", {}, el("span", {class: "eyebrow", text: TYPES[card.type]?.label + " / CARD PROPERTIES"}), el("h2", {text: "卡片属性"})), el("button", {class: "icon-button", text: "×", "aria-label": "关闭属性面板", onClick: () => guardEditor(closeEditor)}));
  const form = el("form", {class: "inspector-body", id: "edit-form"});
  const conflict = el("div", {id: "conflict", class: "banner", hidden: true});
  const fields = cardFields(model, true);
  form.append(conflict, fields, el("p", {id: "editor-error", class: "field-error", role: "alert"}), el("span", {class: "section-label", text: "身份与来源 / DETAILS"}),
    el("div", {class: "readonly"}, el("div", {}, "标识 ", el("code", {text: card.id})), el("div", {text: `版本 ${card.version} · revision ${card.last_revision}`}), el("div", {text: `${card.updated_by.display_name} · ${date(card.updated_at)}`}),
      el("button", {class: "text-button", text: "复制卡片标识 ⧉", type: "button", onClick: () => copy(card.id)})));
  const save = el("button", {class: "primary", text: "保存修改", id: "save-editor", type: "button", onClick: saveEditor});
  root.replaceChildren(header, form, el("footer", {class: "inspector-foot"}, el("button", {class: "secondary", text: "取消", onClick: () => guardEditor(closeEditor)}), save));
  form.addEventListener("input", () => { app.editor.dirty = true; app.lastError = null; updateCardDisabled(card); updateSaveStatus(); });
  form.addEventListener("change", () => { app.editor.dirty = true; updateCardDisabled(card); updateSaveStatus(); });
  form.addEventListener("submit", event => { event.preventDefault(); saveEditor(); });
  if (card.locked || card.lifecycle === "deleted") {
    $$('[data-path]', form).forEach(input => { input.disabled = true; });
    save.disabled = true;
    conflict.hidden = false; conflict.textContent = card.locked ? "卡片已锁定。请先在卡片菜单中显式解锁。" : "卡片已删除。可从回收站恢复后继续编辑。";
  }
  if (!refreshing) $(".inspector-header button", root).focus({preventScroll: true});
}

function closeEditor() {
  const id = app.editor?.base.id;
  app.editor = null; app.selected = null; app.lastError = null; $("#inspector").hidden = true;
  if (id) { cardElement(id)?.classList.remove("selected"); updateCardDisabled(app.cards.get(id)); }
  space.layers();
  updateSaveStatus();
}

function editExpectations(base, changes) {
  return {...(Object.keys(changes).some(key => key !== "layout") ? {expected_content_version: base.content_version} : {}),
    ...(changes.layout ? {expected_layout_version: base.layout_version} : {})};
}

function editorStale() {
  if (!app.editor) return false;
  const base = app.editor.base, current = app.cards.get(base.id);
  if (!current) return true;
  try {
    const patch = diffCard(base, collectFields($("#edit-form"), base));
    return (Object.keys(patch).some(key => key !== "layout") && current.content_version !== base.content_version) ||
      (!!patch.layout && current.layout_version !== base.layout_version);
  } catch { return false; }
}

function refreshConflict() {
  if (!app.editor) return;
  const base = app.editor.base, current = app.cards.get(base.id);
  const banner = $("#conflict"); if (!banner || !current) return;
  const stale = editorStale();
  const stop = current.locked || current.lifecycle === "deleted";
  banner.hidden = !stale && !stop;
  $("#save-editor").disabled = stale || stop || cardBusy(base.id) || !app.connected;
  $("#inspector").classList.toggle("has-conflict", stale || stop);
  if (!stale && !stop) return;
  let patch;
  try { patch = diffCard(base, collectFields($("#edit-form"), base)); } catch { patch = {}; }
  banner.replaceChildren(el("strong", {text: stop ? current.locked ? "卡片已锁定，草稿保留。" : "卡片已删除，草稿保留。" : "已有新版本，本地草稿未被覆盖。"}),
    el("p", {text: "最新已保存内容（v" + current.version + "）"}), el("pre", {text: JSON.stringify({title: current.title, props: current.props, state: current.state, layout: current.layout}, null, 2)}),
    el("p", {text: "本地待保存改动"}), el("pre", {text: JSON.stringify(patch, null, 2)}),
    el("button", {class: "secondary", type: "button", text: "采用最新版本", onClick: () => { openEditor(current); updateCardDisabled(current); updateSaveStatus(); }}));
  if (!stop) banner.append(el("button", {class: "secondary", type: "button", text: "保留草稿，基于最新版本重编", onClick: () => {
    openEditor(current, false, mergeCard(current, patch));
    $("#editor-error").textContent = "已基于最新版本保留本地改动。请审阅后再次点击保存。";
    updateCardDisabled(current); updateSaveStatus();
  }}));
}

async function saveEditor() {
  if (!app.editor || cardBusy(app.editor.base.id)) return false;
  const editor = app.editor, base = editor.base;
  try {
    const latest = app.cards.get(base.id);
    if (editorStale() || latest.locked || latest.lifecycle === "deleted") { refreshConflict(); return false; }
    const changes = diffCard(base, collectFields($("#edit-form"), base));
    if (!Object.keys(changes).length) { editor.dirty = false; updateCardDisabled(latest); updateSaveStatus(); return true; }
    $("#save-editor").disabled = true;
    await mutate("edit_card", {card_id: base.id, ...editExpectations(base, changes), changes}, {cardId: base.id,
      onSuccess: data => { if (app.editor?.base.id === base.id) openEditor(data.card); updateCardDisabled(data.card); toast("修改已保存。"); }});
    return true;
  } catch (error) {
    if ($("#editor-error")) $("#editor-error").textContent = describeError(error);
    if ($("#edit-form")) showFieldErrors($("#edit-form"), error);
    if (error.business?.code === "VERSION_CONFLICT") await sync();
    refreshConflict(); return false;
  } finally {
    if (app.editor?.base.id === base.id) refreshConflict();
    updateSaveStatus();
  }
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("标识已复制。"); }
  catch { showModal("复制标识", el("div", {class: "code-block", text})); }
}
function locate(id) {
  const card = app.cards.get(id);
  if (!card) { toast("找不到此卡片。", true); return; }
  if (card.lifecycle === "deleted") { trashDialog(id); return; }
  app.query = ""; $("#search").value = ""; renderGrid();
  space.focus(card);
  cardElement(id)?.classList.add("flash"); selectCard(id);
}

async function changeLayout(id, patch) {
  const card = app.cards.get(id);
  try {
    await mutate("edit_card", {card_id:id, expected_layout_version:card.layout_version,
      changes:{layout:{space:"world",...patch}}}, {cardId:id});
    closeModal(); toast("布局已保存。");
  } catch(error) { toast(error.message,true); }
}

function cardMenu(id) {
  const card = app.cards.get(id);
  if (!card) return;
  const root = el("div", {}, el("p", {class: "dialog-description", text: `${TYPES[card.type]?.label || card.type} · v${card.version} · ${card.id}`}));
  for (const [label, action, disabled] of [
    ["编辑卡片", () => { closeModal(); selectCard(id); }, card.locked],
    ["复制卡片标识", () => copy(id), false],
    ["定位卡片", () => { closeModal(); locate(id); }, false],
    ["置于最上层", () => changeLayout(id, {z: Math.max(0, ...space.getCards().map(item => item.layout.z || 0)) + 1}), blocked(card)],
    ["置于最下层", () => changeLayout(id, {z: Math.min(0, ...space.getCards().map(item => item.layout.z || 0)) - 1}), blocked(card)],
    ...(card.type === "text" ? [
      ["正文按内容自适应", () => changeLayout(id, {height_mode: "auto"}), blocked(card)],
      ["完整展开正文", () => changeLayout(id, {height_mode: "expanded"}), blocked(card)],
      ["固定当前高度", () => changeLayout(id, {height_mode: "fixed", h: cardElement(id).offsetHeight}), blocked(card)],
    ] : []),
    [card.locked ? "显式解锁卡片" : "锁定卡片", () => guardEditor(async () => {
      try { await mutate("set_card_lock", {card_id: id, expected_version: card.version, locked: !card.locked}, {cardId: id}); closeModal(); toast(card.locked ? "卡片已解锁。" : "卡片已锁定，双方仍可读取。"); }
      catch (error) { toast(error.message, true); }
    }), !app.connected || cardBusy(id)],
    ["删除（可从回收站恢复）", () => deleteDialog(id), card.locked || !app.connected || cardBusy(id)],
  ]) root.append(el("button", {class: "nav-item", text: label, disabled, onClick: action}));
  showModal(card.title, root, "CARD ACTIONS");
}

function deleteDialog(id) {
  guardEditor(() => {
    const card = app.cards.get(id), errorBox = el("p", {class: "field-error", role: "alert"});
    const submit = el("button", {class: "danger", text: "移至回收站", onClick: async () => {
      submit.disabled = true;
      try { await mutate("delete_card", {card_id: id, expected_version: card.version}, {cardId: id,
        onSuccess: () => { if (app.editor?.base.id === id) closeEditor(); closeModal(); toast("已移至回收站，原文件和资源均保留。"); }}); }
      catch (error) { errorBox.textContent = error.message; submit.disabled = false; }
    }});
    showModal("删除「" + card.title + "」？", el("div", {}, el("p", {class: "dialog-description", text: "卡片将移出当前看板，可随时从回收站恢复。删除卡片不会删除原文件或共享资源。"}), errorBox, el("div", {class: "modal-actions"}, el("button", {class: "secondary", text: "取消", onClick: closeModal}), submit)));
  });
}

async function trashDialog(highlight = null) {
  const root = el("div", {}, el("p", {class: "dialog-description", text: "删除的卡片保留原标识、内容和资源关联，可恢复到当前看板。"}));
  const search = el("input", {class: "inline-input", type: "search", placeholder: "搜索回收站标题或完整标识", "aria-label": "搜索回收站"});
  const list = el("div"), errorBox = el("p", {class: "field-error", role: "alert"});
  root.append(search, list, errorBox);
  showModal("回收站", root, "RECYCLE BIN");
  try {
    const result = await allPages("get_card_list", {lifecycle: "deleted"});
    $("#trash-count").textContent = result.items.length;
    const render = () => {
      list.replaceChildren();
      const items = result.items.filter(card => !search.value || card.id === search.value || card.title.toLowerCase().includes(search.value.toLowerCase()));
      if (!items.length) list.append(el("p", {class: "dialog-description", text: result.items.length ? "没有匹配的已删除卡片。" : "回收站还是空的。"}));
      for (const card of items) list.append(el("div", {class: "list-item"}, el("div", {}, el("strong", {text: card.title}), el("p", {text: `${TYPES[card.type]?.label} · ${card.updated_by.display_name} · ${date(card.updated_at)}`})),
        el("div", {class: "actions"}, el("button", {class: "secondary", text: "查看", onClick: async () => {
          try { const data = await rpc("read_card", {card_id: card.id, include_deleted: true});
            showModal(card.title + "（已删除）", el("div", {}, el("pre", {class: "code-block", text: JSON.stringify(data.card, null, 2)}), el("button", {class: "secondary", text: "返回回收站", onClick: () => trashDialog()})));
          } catch (error) { errorBox.textContent = error.message; }
        }}), el("button", {class: "secondary", text: "恢复", disabled: !app.connected, onClick: async event => {
          event.target.disabled = true;
          try { await mutate("restore_card", {card_id: card.id, expected_version: card.version}, {cardId: card.id,
            onSuccess: data => { closeModal(); locate(data.card.id); toast(data.layout_adjusted ? "卡片已恢复，原位置被占用，已自动安排新位置。" : "卡片已恢复到原位置。"); }}); }
          catch (error) { errorBox.textContent = error.message; event.target.disabled = false; }
        }}))));
    };
    search.addEventListener("input", render); render();
    if (highlight) { search.value = highlight; render(); }
  } catch (error) { errorBox.textContent = error.message; }
}

async function activityDialog() {
  await loadRecentEvents();
  const root = el("div", {}, el("p", {class: "dialog-description", text: "最近 50 次已保存的卡片变更。"}));
  const labels = {created: "添加", updated: "修改", deleted: "删除", restored: "恢复", locked: "锁定", unlocked: "解锁",
    rename_board:"重命名黑板",delete_board:"删除黑板",set_board_lock:"更改删除保护",settings_updated:"修改看板设置"};
  for (const event of [...app.events].reverse()) root.append(el("div", {class: "list-item"}, el("div", {},
    el("strong", {text: `${event.actor.display_name} ${labels[event.kind] || event.kind}${event.card_after ? "了「"+event.card_after.title+"」" : ""}`}), el("p", {text: `${date(event.occurred_at)} · revision ${event.revision}`})),
    event.card_id ? el("button", {class: "secondary", text: "定位", onClick: () => { closeModal(); locate(event.card_id); }}) : null));
  if (!app.events.length) root.append(el("p", {class: "dialog-description", text: "还没有卡片变更，添加第一张卡片后会出现在这里。"}));
  showModal("最近变更", root, "RECENT ACTIVITY");
}

async function assetViewer(id, noPreview = false) {
  const root = el("div"), errorBox = el("p", {class: "field-error", role: "alert"});
  root.append(el("p", {class: "dialog-description", text: "正在核对资源…"}));
  showModal("资源详情", root, "ASSET");
  const refresh = async () => {
    try {
      const {asset} = await rpc("get_asset_info", {asset_id: id}); app.assets.set(id, asset);
      root.replaceChildren(el("p", {class: "dialog-description", text: `${asset.name} · ${asset.mode === "reference" ? "受控引用" : "导入副本"} · ${size(asset.size_bytes)}\n状态：${asset.status} · 核对于 ${date(asset.checked_at)}`}));
      if (asset.status === "available") {
        if (!noPreview && asset.preview_kind === "image") {
          const image = el("img", {class: "viewer-img", src: "/api/assets/" + encodeURIComponent(id) + "?v=" + encodeURIComponent(asset.resource_version), alt: asset.name});
          const zoom = el("input", {type: "range", min: 50, max: 250, value: 100, "aria-label": "图片缩放比例", onInput: event => { image.style.width = event.target.value + "%"; image.style.maxHeight = "none"; }});
          root.append(el("div", {class: "viewer-text"}, image), el("label", {class: "field"}, "图片缩放（50%–250%）", zoom));
        } else if (!noPreview && asset.preview_kind === "text") {
          let cursor = null;
          const text = el("pre", {class: "viewer-text"});
          const more = el("button", {class: "secondary", text: "继续读取", onClick: async () => {
            more.disabled = true;
            try { const data = await rpc("read_asset", {asset_id: id, format: "text", resource_version: asset.resource_version, ...(cursor ? {cursor} : {})}); text.append(data.content); cursor = data.next_cursor; more.hidden = data.complete; }
            catch (error) { errorBox.textContent = error.message; }
            finally { more.disabled = false; }
          }});
          root.append(text, more); more.click();
        } else root.append(el("p", {class: "dialog-description", text: noPreview ? "此卡片已关闭内容预览，可下载原文件。" : "此格式暂不支持预览，可下载原文件。"}));
        root.append(el("div", {class: "modal-actions"}, el("a", {class: "secondary", href: "/api/assets/" + encodeURIComponent(id) + "/download", download: "", text: "下载原文件 ↗"})));
      } else root.append(el("p", {class: "inline-error", text: "资源当前不可用，卡片与说明仍被保留。请恢复原文件、调整授权或替换资源。"}));
      root.append(el("button", {class: "text-button", text: "刷新资源状态 ↻", onClick: refresh}), errorBox);
    } catch (error) { root.replaceChildren(el("p", {class: "field-error", text: error.message})); }
  };
  await refresh();
}

async function importDialog(onImported = null) {
  const root = el("div"), tabs = el("div", {class: "tabs"}), content = el("div"), errorBox = el("p", {class: "field-error", role: "alert"});
  let pending = false;
  const finish = asset => {
    app.assets.set(asset.id, asset);
    toast("资源已添加，原文件未被修改。");
    if (onImported) { modalOnClose = null; $("#modal").close(); onImported(asset); }
    else {
      content.replaceChildren(el("p", {class: "dialog-description", text: `「${asset.name}」已添加，可被多张卡片复用。`}), el("button", {class: "primary", text: "为此资源创建卡片", onClick: () => {
        const type = asset.preview_kind === "image" ? "image" : "file";
        createCardForm(type, {...DEFAULTS[type], title: asset.name, state: {...DEFAULTS[type].state, asset_id: asset.id}});
      }}), el("button", {class: "text-button", text: "完成，稍后再用", onClick: closeModal}));
    }
  };
  const upload = () => {
    const input = el("input", {type: "file", "aria-label": "选择要导入的本地文件"});
    const submit = el("button", {class: "primary", text: "导入副本", onClick: async () => {
      if (pending) return;
      const file = input.files[0];
      if (!file) { errorBox.textContent = "请先选择一个文件。"; return; }
      if (file.size > 20 * 1048576) { errorBox.textContent = "文件不能超过 20 MiB。"; return; }
      pending = true; submit.disabled = true; errorBox.textContent = "";
      try {
        const bytes = new Uint8Array(await file.arrayBuffer());
        let binary = "";
        for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768));
        await mutate("upload_asset", {name: file.name, data_base64: btoa(binary)}, {onSuccess: data => finish(data.asset)});
      } catch (error) { errorBox.textContent = describeError(error); }
      finally { pending = false; submit.disabled = false; }
    }});
    content.replaceChildren(el("p", {class: "dialog-description", text: "保存一份独立副本，原文件之后的修改不会影响它。支持任意文件，单个文件最多 20 MiB。"}), el("div", {class: "upload-zone"}, input), submit);
  };
  const reference = async () => {
    content.replaceChildren(el("p", {text: "正在读取授权目录…"}));
    try {
      const {roots} = await rpc("get_asset_sources");
      if (!roots.length) { content.replaceChildren(el("p", {class: "dialog-description", text: "还没有授权目录。请在看板设置中由用户添加允许访问的本地目录。Agent 无法自行授权。"}), el("button", {class: "secondary", text: "配置授权目录", onClick: settingsDialog})); return; }
      const form = el("form"), submit = el("button", {class: "primary", text: "添加受控资源", type: "submit"});
      form.append(field("授权目录", "root", roots[0].root_id, {choices: roots.map(item => [item.root_id, item.label])}), field("相对路径", "relative_path", "", {hint: "相对于所选目录，例如 reports/result.png；不允许越出目录。"}), field("资源名称", "name", ""), field("使用方式", "mode", "reference", {choices: [["reference", "受控引用（读取时核对原文件）"], ["imported", "导入独立副本"]]}), submit);
      form.addEventListener("submit", async event => {
        event.preventDefault(); if (pending) return;
        pending = true; submit.disabled = true;
        try { await mutate("import_asset", {name: $('[name="name"]', form).value, mode: $('[name="mode"]', form).value, source: {kind: "approved_file", root_id: $('[name="root"]', form).value, relative_path: $('[name="relative_path"]', form).value}}, {onSuccess: data => finish(data.asset)}); }
        catch (error) { errorBox.textContent = describeError(error); }
        finally { pending = false; submit.disabled = false; }
      });
      content.replaceChildren(form);
    } catch (error) { errorBox.textContent = error.message; }
  };
  const existing = async () => {
    try {
      const result = await allPages("list_assets"); content.replaceChildren();
      for (const asset of result.items) {
        app.assets.set(asset.id, asset);
        content.append(el("div", {class: "list-item"}, el("div", {}, el("strong", {text: asset.name}), el("p", {text: `${asset.mode === "reference" ? "受控引用" : "导入副本"} · ${size(asset.size_bytes)} · ${asset.status}`})), el("button", {class: "secondary", text: onImported ? "选择" : "建卡", onClick: () => finish(asset)})));
      }
      if (!result.items.length) content.append(el("p", {class: "dialog-description", text: "尚未添加资源。"}));
    } catch (error) { errorBox.textContent = error.message; }
  };
  for (const [index, [label, action]] of [["导入副本", upload], ["受控引用", reference], ["已有资源", existing]].entries()) tabs.append(el("button", {class: index === 0 ? "active" : "", text: label, onClick: event => { if (pending) return; $$("button", tabs).forEach(button => button.classList.remove("active")); event.target.classList.add("active"); errorBox.textContent = ""; action(); }}));
  root.append(tabs, content, errorBox); showModal("添加图片或文件", root, "IMPORT ASSETS"); upload();
}

function boardNameDialog(mode, board = null) {
  if (hasBoardDrafts()) { toast("请先保存或取消当前草稿，并等待操作确认。",true); return; }
  const titles = {create:"新建黑板",rename:"重命名黑板",duplicate:"复制黑板副本"};
  const form = el("form"), errorBox = el("p",{class:"field-error",role:"alert"});
  const defaultName = mode === "duplicate" ? (board.name.slice(0,76)+" 副本") : board?.name || "新黑板";
  const name = field("黑板名称","name",defaultName);
  const submit = el("button",{class:"primary",type:"submit",text:titles[mode]});
  form.append(name,el("p",{class:"dialog-description",text:mode === "duplicate" ?
    "复制所有卡片与资源记录，保留内部绑定、回收站及删除保护。独立副本自动保存；受控文件引用仍读取同一个原文件。" : "修改即时保存，不需要手动保存整块黑板。"}),errorBox,submit);
  let busy = false;
  form.addEventListener("submit",async event => {
    event.preventDefault(); if (busy) return;
    busy = true; submit.disabled = true; errorBox.textContent = "";
    try {
      const value = $('[name="name"]',form).value.trim();
      if (!value || value.length > 80) throw new Error("名称需为1～80个字符。");
      const tool = {create:"create_board",rename:"rename_board",duplicate:"duplicate_board"}[mode];
      const data = await mutate(tool,{name:value,...(board ? {board_id:board.id} : {}),
        ...(mode === "rename" ? {expected_version:board.version} : {})});
      busy = false;
      closeModal();
      app.boards = (await allPages("list_boards")).items; renderBoardNavigation();
      if (mode !== "rename") await switchBoard(data.board.id); else await sync();
      toast(titles[mode]+"成功，已自动保存。");
    } catch (error) { errorBox.textContent = describeError(error); }
    finally { busy = false; submit.disabled = false; }
  });
  showModal(titles[mode],form,"MANAGE BOARDS",() => !busy);
  $('[name="name"]',form).select();
}

const BOARD_FILE_TYPES = [{description:"AgentBoard黑板数据",accept:{"application/octet-stream":[".agentboard"]}}];
const MAX_BOARD_FILE_BYTES = 256*1024*1024;

async function boardFileRequest(path, body, command = null) {
  const binary = body instanceof Blob;
  const response = await fetch(path,{method:"POST",headers:{
    "Content-Type":binary ? "application/octet-stream" : "application/json","X-AgentBoard-Session":app.session,
    ...(command ? {"X-AgentBoard-Command":command} : {})},body:binary ? body : JSON.stringify(body),
    signal:AbortSignal.timeout(path.startsWith("/api/board-files/") ? 360000 : 120000)});
  if (!response.ok) {
    let result;
    try { result = await response.json(); } catch { /* Network/server errors may have no JSON body. */ }
    const error = new Error(result?.error?.message || "文件操作失败（"+response.status+"）。");
    if (result?.error?.code) error.business = result.error;
    error.sessionInvalid = response.status === 403;
    throw error;
  }
  return response;
}

async function chooseBoardFile() {
  if (typeof window.showOpenFilePicker === "function") {
    try {
      const [handle] = await window.showOpenFilePicker({types:BOARD_FILE_TYPES,multiple:false});
      return await handle.getFile();
    } catch (error) {
      if (error.name === "AbortError") return null;
      if (!["SecurityError","NotSupportedError"].includes(error.name)) throw error;
    }
  }
  const response = await boardFileRequest("/api/board-files/open",{});
  return response.status === 204 ? null : response.blob();
}

async function exportBoardFile(board) {
  let handle = null;
  if (typeof window.showSaveFilePicker === "function") {
    try {
      let stem = board.name.replace(/[<>:"/\\|?*\x00-\x1f]/g,"_").replace(/[ .]+$/g,"").slice(0,80) || "AgentBoard";
      if (/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(stem)) stem = "AgentBoard_"+stem;
      handle = await window.showSaveFilePicker({suggestedName:stem+".agentboard",types:BOARD_FILE_TYPES});
    } catch (error) {
      if (error.name === "AbortError") return;
      if (!["SecurityError","NotSupportedError"].includes(error.name)) throw error;
    }
  }
  if (!handle) {
    const result = await (await boardFileRequest("/api/board-files/export",{board_id:board.id})).json();
    if (!result.data.cancelled) toast("黑板已导出为「"+result.data.filename+"」。");
    return;
  }
  const response = await boardFileRequest("/api/boards/export",{board_id:board.id});
  const blob = await response.blob();
  const writable = await handle.createWritable();
  try { await writable.write(blob); await writable.close(); }
  catch (error) { try { await writable.abort(); } catch { /* Already closed. */ } throw error; }
  toast("黑板已导出，可复制到其他电脑导入。");
}

async function importBoardFile(file) {
  if (!file.size || file.size > MAX_BOARD_FILE_BYTES) throw new Error("请选择不超过256 MiB的AgentBoard数据文件。");
  // Keep immutable bytes for an identical retry even if the selected disk file changes.
  const archive = new Blob([await file.arrayBuffer()],{type:"application/octet-stream"});
  if (hasBoardDrafts()) throw new Error("请先处理当前草稿和未确认操作，再导入黑板。");
  const command = crypto.randomUUID();
  const upload = async () => {
    const result = await (await boardFileRequest("/api/boards/import",archive,command)).json();
    if (!result.ok) { const error = new Error(result.error.message); error.business = result.error; throw error; }
    return result.data;
  };
  app.pending.set(command,{tool:"import_board",args:{command_id:command},cardId:null,retry:upload,
    onSuccess:data => { app.recoveredImport = data.board.id; }});
  app.lastError = null; updateSaveStatus();
  try {
    const data = await upload(); app.pending.delete(command);
    toast(`已导入「${data.board.name}」：${data.card_count}张卡片，${data.asset_count}项资源。`);
    return data;
  } catch (error) {
    if (error.business) { app.pending.delete(command); app.lastError = error.message; }
    else { setConnection(false); error.message = "导入结果待确认，请保留此页面；重连后会核对原命令，避免重复导入。"; }
    throw error;
  } finally { updateSaveStatus(); }
}

async function manageBoardsDialog() {
  if (hasBoardDrafts()) { toast("请先保存或取消当前草稿，并等待操作确认。",true); return; }
  const root = el("div"), rows = el("div"), errorBox = el("p",{class:"field-error",role:"alert"});
  let busy = false;
  const run = async action => {
    if (busy) return; busy = true; errorBox.textContent = "";
    $$("button",root).forEach(button => { button.disabled = true; });
    try { return await action(); }
    catch (error) { errorBox.textContent = describeError(error); }
    finally {
      busy = false;
      $$(".board-file-toolbar button",root).forEach(button => { button.disabled = false; });
      if (rows.isConnected) { try { await load(); } catch (error) { errorBox.textContent = describeError(error); } }
    }
  };
  const load = async () => {
    app.boards = (await allPages("list_boards")).items; renderBoardNavigation(); rows.replaceChildren();
    for (const board of app.boards) {
      const actions = el("div",{class:"board-management-actions"},
        el("button",{class:"secondary",text:"重命名",onClick:() => boardNameDialog("rename",board)}),
        el("button",{class:"secondary",text:"复制副本",onClick:() => boardNameDialog("duplicate",board)}),
        el("button",{class:"secondary",text:"导出",onClick:() => run(() => exportBoardFile(board))}),
        el("button",{class:"secondary",text:board.locked ? "解除删除保护" : "锁定黑板",onClick:() => run(async () => {
          await mutate("set_board_lock",{board_id:board.id,expected_version:board.version,locked:!board.locked});
          toast(board.locked ? "删除保护已解除。" : "删除保护已开启，用户与 Agent 均不能直接删除整块黑板。");
        })}),
        el("button",{class:"secondary danger",text:"删除",disabled:board.locked,title:board.locked ? "请先解除删除保护" : "删除整块黑板",onClick:() => run(async () => {
          if (!confirm(`删除黑板「${board.name}」及其所有卡片？黑板将从列表移除，界面暂不提供恢复入口。`)) return;
          await mutate("delete_board",{board_id:board.id,expected_version:board.version});
          await sync(); toast("黑板已删除。");
        })}));
      rows.append(el("section",{class:"board-management-row"},el("strong",{text:board.name+(board.id === app.boardId ? " · 当前" : "")}),
        el("p",{class:"board-management-id",text:board.id}),el("p",{class:"dialog-description",text:board.locked ? "已开启删除保护 · 可继续编辑，只有用户可解锁" : "未开启删除保护"}),actions));
    }
    if (!app.boards.length) rows.append(el("p",{text:"尚无黑板，点击新建开始。"}));
  };
  root.append(el("p",{class:"dialog-description",text:"每块黑板独立自动保存。锁定只保护整块黑板不被删除，不阻止日常编辑。"}),
    el("div",{class:"board-file-toolbar"},el("button",{class:"primary",text:"＋ 新建黑板",onClick:() => boardNameDialog("create")}),
      el("button",{class:"secondary",text:"导入黑板",onClick:() => run(async () => {
        const file = await chooseBoardFile(); return file ? importBoardFile(file) : null;
      }).then(data => { if (data?.board) return switchBoard(data.board.id); }).catch(error => toast(error.message,true))})),
    el("p",{class:"dialog-description board-file-description",text:"导出为单个.agentboard文件，包含卡片、交互演示、回收站和资源副本。导入创建新黑板，保留锁定与权限，不覆盖已有内容、不带入目录授权；交互代码在隔离区域运行。"}),rows,errorBox);
  showModal("管理黑板",root,"MANAGE BOARDS",() => !busy);
  await load();
}

async function settingsDialog() {
  const form = el("form"), errorBox = el("p", {class: "field-error", role: "alert"});
  const info = await rpc("get_board_info");
  form.append(field("看板名称", "name", info.name), field("看板说明", "description", info.description, {multiline: true}), el("span", {class: "section-label", text: "AGENT 访问权限"}));
  const labels = {read: "允许读取看板", create: "允许创建卡片", edit: "允许编辑卡片", delete: "允许删除卡片", restore: "允许恢复卡片", import_asset: "允许导入资源"};
  for (const [key, label] of Object.entries(labels)) form.append(field(label, "permissions." + key, info.agent_permissions[key], {kind: "boolean"}));
  form.append(el("p", {class: "dialog-description", text: "Agent 始终不能解除卡片锁定或扩大目录授权。关闭读取权限将阻止该 Agent 的所有看板操作与历史回执读取。"}));
  const save = el("button", {class: "primary", type: "submit", text: "保存设置"});
  form.append(errorBox, save);
  form.addEventListener("submit", async event => {
    event.preventDefault(); save.disabled = true;
    const permissions = {};
    for (const key of Object.keys(labels)) permissions[key] = $(`[name="permissions.${key}"]`, form).checked;
    try { await mutate("update_board", {name: $('[name="name"]', form).value, description: $('[name="description"]', form).value, agent_permissions: permissions}); toast("看板设置已保存。"); await sync(); }
    catch (error) { errorBox.textContent = error.message; }
    finally { save.disabled = false; }
  });
  const rootList = el("div"), rootPath = field("本地目录绝对路径", "path", "", {hint: "仅授权你希望 Agent 能访问的目录；目录外文件不会开放。"}), rootLabel = field("目录显示名称", "label", "工作资料");
  const loadRoots = async () => {
    const {roots,default_workspace} = await rpc("get_asset_sources"); rootList.replaceChildren();
    rootList.append(el("p",{class:"dialog-description",text:"应用级授权，所有黑板共用。首次启动默认授权项目 workspace："+default_workspace+"。取消后不会在重启时自动恢复；仅可通过资源接口读取，不授予执行文件或写入目录的权限。"}));
    if (!roots.length) rootList.append(el("p",{text:"当前没有已授权目录。"}));
    for (const root of roots) rootList.append(el("div", {class: "root-item"}, el("div",{},el("strong", {text: root.label}),el("p",{class:"root-path",text:root.path})), el("button", {type: "button", text: "取消授权", onClick: async event => {
      if (!confirm("撤销「" + root.label + "」？此目录下的文件引用将不可读取；已导入副本不受影响。")) return;
      event.target.disabled = true;
      try { await mutate("revoke_root", {root_id: root.root_id}); await loadRoots(); toast("目录授权已撤销。"); } catch (error) { errorBox.textContent = error.message; event.target.disabled = false; }
    }})));
  };
  const rootForm = el("form", {}, rootPath, rootLabel, el("button", {type: "submit", class: "secondary", text: "授权此目录"}));
  rootForm.addEventListener("submit", async event => {
    event.preventDefault(); const submit = $('button[type="submit"]', rootForm); submit.disabled = true;
    try { await mutate("grant_root", {path: $('[name="path"]', rootForm).value, label: $('[name="label"]', rootForm).value}); await loadRoots(); toast("目录已授权。"); }
    catch (error) { errorBox.textContent = error.message; }
    finally { submit.disabled = false; }
  });
  const root = el("div", {}, space.preferences(), form, el("span", {class: "section-label", text: "已授权资源目录"}), rootList, rootForm);
  showModal("看板设置", root, "BOARD SETTINGS"); await loadRoots();
}

function connectionDialog() {
  showModal("让 Agent 接着一起改", el("div", {}, el("p", {class: "dialog-description", text: "在支持 MCP stdio 的 Agent 客户端中添加 AgentBoard。MCP 进程和网页服务使用同一个 data 目录。配置中的路径请替换为此项目的绝对路径，完整示例见 README。"}),
    el("pre", {class: "code-block", text: JSON.stringify({mcpServers: {agentboard: {command: "python", args: ["-u", "<项目绝对路径>/scripts/mcp_server.py"]}}}, null, 2)}),
    el("p", {class: "dialog-description", text: `当前看板：${app.boardId}。连接后先调用 list_boards / get_card_types，再读取或创建卡片。网页变化不会自动启动 Agent 推理。`}),
    el("button", {class: "secondary", text: "复制看板标识", onClick: () => copy(app.boardId)})), "CONNECT YOUR AGENT");
}

function startDrag(event, card, resizing) {
  if (event.button !== 0 || blocked(card) || app.inline.has(card.id) || app.dragging) return;
  event.preventDefault(); event.stopPropagation();
  const handle = event.currentTarget, node = cardElement(card.id), original = clone(card.layout);
  const x0 = event.clientX, y0 = event.clientY, zoom = space.camera.zoom, initialHeight = node.offsetHeight;
  app.dragging = card.id; document.body.classList.add("card-dragging");
  handle.setPointerCapture(event.pointerId);
  let layout = original, moved = false;
  const snap = value => Math.round(value / (space.prefs.snap ? 24 : 1)) * (space.prefs.snap ? 24 : 1);
  const move = next => {
    const dx = (next.clientX - x0) / zoom, dy = (next.clientY - y0) / zoom;
    if (Math.abs(next.clientX - x0) + Math.abs(next.clientY - y0) < 3 && !moved) return;
    moved = true;
    layout = resizing ? {...original, w: Math.max(180, Math.min(10000, snap(original.w + dx))),
      h: Math.max(100, Math.min(20000, snap(initialHeight + dy))), height_mode:"fixed"} :
      {...original, x: Math.max(-10000000, Math.min(10000000, snap(original.x + dx))),
       y: Math.max(-10000000, Math.min(10000000, snap(original.y + dy)))};
    Object.assign(node.style, {left:layout.x+"px", top:layout.y+"px", width:layout.w+"px",
      height:layout.height_mode === "fixed" ? layout.h+"px" : "auto"});
    node.dataset.heightMode = layout.height_mode;
    node.classList.add("moving");
  };
  const clean = () => {
    app.dragging = null; document.body.classList.remove("card-dragging");
    handle.removeEventListener("pointermove", move); handle.removeEventListener("pointerup", end);
    handle.removeEventListener("pointercancel", cancel); document.removeEventListener("keydown", escape);
    const latest = app.cards.get(card.id);
    if (latest && (moved || latest !== card)) renderCard(latest);
    if (handle.hasPointerCapture(event.pointerId)) handle.releasePointerCapture(event.pointerId);
    if (moved) app.suppressClickUntil = Date.now()+150;
  };
  const end = async () => {
    clean(); if (!moved || equal(layout, original)) return;
    try { await mutate("edit_card", {card_id:card.id, expected_layout_version:card.layout_version,
      changes:{layout}}, {cardId:card.id}); }
    catch (error) { toast(error.message+" 布局保留最后已保存位置。",true); }
  };
  const cancel = () => clean();
  const escape = key => { if (key.key === "Escape") { key.preventDefault(); cancel(); } };
  handle.addEventListener("pointermove",move); handle.addEventListener("pointerup",end);
  handle.addEventListener("pointercancel",cancel); document.addEventListener("keydown",escape);
}

async function examples() {
  const button = $("#example-btn"); button.disabled = true;
  const samples = [
    {type: "text", type_version: "1.0", title: "控制器设计目标", props: {format: "markdown"}, state: {text: "## 一起调好这个参数\n\n- 最大超调量不超过 **20%**\n- 保留当前候选值与验证结论\n- 参数调整后，在 Agent 客户端继续讨论\n\n这里的控件只记录共享值，不执行仿真。"}},
    {type: "slider", type_version: "1.0", title: "比例增益 Kp", description: "当前候选值，尚待实验验证。", props: {min: 0, max: 10, step: 0.1, precision: 1}, state: {value: 2.5}},
    {type: "input", type_version: "1.0", title: "允许的最大超调量", props: {mode: "number", required: true, min: 0, max: 100, unit: "%"}, state: {value: 20}},
    {type: "checkbox", type_version: "1.0", title: "实验验证", props: {label: "已通过实验验证"}, state: {checked: false}},
  ];
  try { for (const card of samples) await mutate("push_card", {card}); toast("PID 协作示例已添加，可以直接修改参数。"); }
  catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}

$("#search").addEventListener("input", event => { app.query = event.target.value.trim(); renderGrid(); });
$("#clear-search").addEventListener("click", () => { $("#search").value = ""; app.query = ""; renderGrid(); });
$("#add-btn").addEventListener("click", chooseType);
$("#first-card-btn").addEventListener("click", chooseType);
$("#example-btn").addEventListener("click", examples);
$("#copy-board").addEventListener("click", () => copy(app.boardId));
$("#import-btn").addEventListener("click", () => importDialog());
$("#trash-btn").addEventListener("click", () => trashDialog());
$("#activity-btn").addEventListener("click", activityDialog);
$("#settings-btn").addEventListener("click", () => settingsDialog().catch(error => toast(error.message, true)));
$("#connect-btn").addEventListener("click", connectionDialog);
$("#board-nav").addEventListener("click", () => guardEditor(closeEditor));
$("#board-select").addEventListener("change",event => switchBoard(event.target.value).catch(error => toast(error.message,true)));
$("#new-board-btn").addEventListener("click",() => boardNameDialog("create"));
$("#manage-boards-btn").addEventListener("click",() => manageBoardsDialog().catch(error => toast(error.message,true)));
document.addEventListener("keydown", event => {
  if (event.key === "/" && !event.target.closest("input,textarea,select") && !$("#modal").open) { event.preventDefault(); $("#search").focus(); }
  if (event.key === "Escape" && !$("#modal").open && app.editor && !event.target.closest("input,textarea")) guardEditor(closeEditor);
});
window.addEventListener("beforeunload", event => { if (app.editor?.dirty || app.inline.size || app.pending.size || app.modalDirty || interactive.hasDraft()) { event.preventDefault(); event.returnValue = ""; } });
window.addEventListener("online", sync);
window.addEventListener("offline", () => setConnection(false));
if (location.pathname === "/__capture__") {
  loadCapture();
} else {
  sync();
  setInterval(sync, 1500);
}
