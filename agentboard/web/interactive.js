"use strict";

class InteractiveCards {
  constructor(options) {
    this.options = options; this.records = new Map();
    addEventListener("message", event => this.receive(event));
    new MutationObserver(() => this.refresh()).observe(document.documentElement,{attributes:true,attributeFilter:["data-theme"]});
  }
  static validateState(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("演示参数必须是JSON对象。");
    const visit = (item,depth) => {
      if (depth > 16) throw new Error("参数嵌套超过16层。");
      if (typeof item === "number" && !Number.isFinite(item)) throw new Error("参数数值必须有限。");
      if (item !== null && !["object","number","boolean","string"].includes(typeof item)) throw new Error("参数仅接受JSON值。");
      if (item && typeof item === "object" && !Array.isArray(item) && ![Object.prototype,null].includes(Object.getPrototypeOf(item))) throw new Error("参数对象须转换为普通JSON。");
      if (item && typeof item === "object") for (const key of Object.keys(item)) {
        if (["__proto__","constructor","prototype"].includes(key)) throw new Error("参数包含不允许的属性。");
        visit(item[key],depth+1);
      }
    };
    visit(value,0);
    if (new TextEncoder().encode(JSON.stringify(value)).length > 65536) throw new Error("演示参数超过64KiB。");
  }
  hasDraft() { return [...this.records.values()].some(r => r.dirty); }
  isSaving(id) { return !!this.records.get(id)?.saving; }
  visible(r) {
    if (!r.stage?.isConnected) return false;
    const rect = r.stage.getBoundingClientRect(), clip = r.root.closest(".canvas-wrapper")?.getBoundingClientRect() || rect;
    return Math.min(rect.right,clip.right,innerWidth) > Math.max(rect.left,clip.left,0) &&
      Math.min(rect.bottom,clip.bottom,innerHeight) > Math.max(rect.top,clip.top,0);
  }
  inputs(card) {
    const inputs = {}, inputErrors = {}, sources = {};
    for (const [name, binding] of Object.entries(card.props.bindings)) {
      try {
        const source = this.options.getCards().get(binding.card_id);
        if (!source || source.lifecycle !== "active") throw new Error("来源卡片不存在或已删除");
        let value = {state:this.options.getLiveState(source)};
        for (let key of binding.path.split("/").slice(1)) {
          key = key.replaceAll("~1","/").replaceAll("~0","~");
          if (!value || typeof value !== "object" || !Object.hasOwn(value,key)) throw new Error("来源中不存在此状态路径");
          value = value[key];
        }
        const wrapped = {value}; InteractiveCards.validateState(wrapped);
        inputs[name] = structuredClone(value);
        sources[name] = {card_id:source.id,content_version:source.content_version};
      } catch (error) { inputs[name] = null; inputErrors[name] = error.message; }
    }
    return {inputs,inputErrors,sources};
  }
  mount(body, card) {
    let record = this.records.get(card.id);
    if (!record) {
      record = {card,values:structuredClone(card.state.values),base:card.content_version,dirty:false,serial:0};
      this.records.set(card.id,record);
    }
    if (record.frame) record.frame.remove();
    record.frame = null;
    const root = document.createElement("div"); root.className = "interactive-host";
    const actions = document.createElement("div"); actions.className = "interactive-actions";
    const add = (text, run) => {
      const button = document.createElement("button"); button.textContent = text; button.type = "button";
      button.addEventListener("click",run); actions.append(button); return button;
    };
    record.reload = add("重新运行",() => this.restart(record,true));
    record.stop = add("停止",() => this.halt(record));
    record.reset = add("重置参数",() => {
      if (!this.readOnly(record)) this.changeState(record,structuredClone(record.card.props.initial_state),record.serial+1);
    });
    record.status = document.createElement("div"); record.status.className = "interactive-status";
    record.status.setAttribute("role","status");
    record.stage = document.createElement("div"); record.stage.className = "interactive-stage";
    root.append(actions,record.status,record.stage); body.append(root);
    record.root = root;
    this.updateCard(card); this.start(record);
  }
  start(r) {
    if (!r.stage?.isConnected) { requestAnimationFrame(() => { if (r.stage?.isConnected) this.start(r); }); return; }
    clearInterval(r.watchdog); r.frame?.remove();
    r.error = null; r.ready = false; r.stopped = false; r.hello = false; r.lastData = null;
    r.mathErrors = 0; r.overflow = false;
    r.key = [...crypto.getRandomValues(new Uint8Array(16))].map(b => b.toString(16).padStart(2,"0")).join("");
    r.source = r.card.props.source;
    const frame = document.createElement("iframe");
    frame.className = "interactive-frame"; frame.title = r.card.title + "交互演示";
    frame.setAttribute("sandbox","allow-scripts"); frame.setAttribute("referrerpolicy","no-referrer");
    frame.setAttribute("allow","camera 'none'; microphone 'none'; geolocation 'none'; fullscreen 'none'");
    frame.src = "/api/interactive/"+encodeURIComponent(r.card.id)+"?key="+r.key+"&v="+r.card.content_version+"&board_id="+encodeURIComponent(r.card.board_id);
    r.frame = frame; r.stage.replaceChildren(frame);
    let loads = 0;
    frame.addEventListener("load",() => { if (++loads > 1) this.halt(r,"演示发生页面跳转，已停止。请检查代码后重新运行。"); });
    let waiting = 0, lastTick = Date.now();
    r.watchdog = setInterval(() => {
      const now = Date.now();
      if (document.visibilityState !== "hidden" && this.visible(r)) waiting += Math.min(now-lastTick,1000);
      lastTick = now;
      if (waiting >= 10000 && !r.ready && !r.error && !r.stopped) this.halt(r,"演示未在10秒内报告就绪，请检查代码后重新运行。");
    },250);
    this.status(r);
  }
  halt(r, reason) {
    r.frame?.remove(); r.frame = null; r.key = null; r.stopped = true; r.ready = false;
    clearInterval(r.watchdog); r.error = reason || null; this.status(r);
  }
  restart(r, ask = false) {
    if (r.saving || (r.dirty && this.options.isBusy(r.card.id))) return;
    if (ask && r.dirty && !confirm("放弃尚未保存的演示参数并加载最新状态？")) return;
    clearTimeout(r.timer); r.dirty = false; r.conflict = false;
    r.values = structuredClone(r.card.state.values); r.base = r.card.content_version; r.serial = 0;
    this.start(r); this.options.changed(); this.refresh();
  }
  readOnly(r) { return this.options.readOnly(r.card) || !!r.conflict; }
  status(r) {
    if (!r.status) return;
    const issues = Object.entries(r.inputErrors || {}).map(([name,error]) => name+"："+error);
    r.status.textContent = r.error || (issues.length ? "输入不可用 · "+issues.join("；") : r.conflict ? "已有新版本，参数草稿保留。重新运行可采用最新状态。" :
      r.stopped ? "演示已停止" : !r.ready ? "正在准备演示…" : r.saving ? "正在保存参数…" : r.dirty ? "参数尚未保存" :
      this.readOnly(r) ? "只读演示" : "参数已保存");
    r.status.classList.toggle("error",!!r.error || !!r.conflict || !!issues.length);
    r.root.dataset.ready = String(!!r.ready);
    r.root.dataset.error = r.error || (issues.length ? "输入不可用" : "");
    r.stage.classList.toggle("input-unavailable",!!issues.length);
    r.stage.classList.toggle("read-only",this.readOnly(r));
    r.reset.disabled = this.readOnly(r) || !!r.stopped;
    r.reload.disabled = !!r.saving; r.stop.disabled = !!r.stopped;
  }
  updateCard(card) {
    const r = this.records.get(card.id); if (!r) return;
    r.card = card;
    if (r.frame) r.frame.title = card.title+"交互演示";
    if (!r.saving && card.content_version !== r.base) {
      if (r.dirty) r.conflict = true;
      else { r.values = structuredClone(card.state.values); r.base = card.content_version; }
    }
    if (r.frame && r.source !== card.props.source && !r.saving && !r.dirty) this.start(r);
    this.sendData(r); this.status(r);
  }
  sendData(r) {
    const resolved = this.inputs(r.card); r.inputErrors = resolved.inputErrors; r.sources = resolved.sources;
    const data = {state:r.values,inputs:resolved.inputs,inputErrors:resolved.inputErrors,
      initialState:r.card.props.initial_state,readOnly:this.readOnly(r),
      theme:document.documentElement.dataset.theme || "light",mode:this.options.capture() ? "screenshot" : "live"};
    const serialized = JSON.stringify({...data,serial:r.serial});
    if (r.hello && r.frame && r.lastData !== serialized) {
      r.frame.contentWindow.postMessage({channel:"agentboard-interactive",key:r.key,type:"data",data,serial:r.serial},"*");
      r.lastData = serialized;
    }
  }
  refresh() {
    for (const [id,r] of this.records) {
      const current = this.options.getCards().get(id);
      if (!current || current.lifecycle !== "active") {
        clearTimeout(r.timer); clearInterval(r.watchdog); r.frame?.remove(); this.records.delete(id); continue;
      }
      this.updateCard(current);
      if (r.dirty && !r.saving && !r.conflict && !this.readOnly(r) && !r.error && !this.options.isBusy(id)) this.schedule(r);
    }
  }
  receive(event) {
    const message = event.data;
    if (event.origin !== "null" || !message || message.channel !== "agentboard-interactive" || typeof message.key !== "string") return;
    const r = [...this.records.values()].find(item => item.key === message.key && item.frame?.contentWindow === event.source);
    if (!r) return;
    if (message.type === "hello") { r.hello = true; r.lastData = null; this.sendData(r); }
    else if (message.type === "rendered") {
      r.ready = !r.error; r.mathErrors = Number.isSafeInteger(message.mathErrors) ? Math.max(0,message.mathErrors) : 0;
      r.overflow = Number.isFinite(message.contentHeight) && Number.isFinite(message.viewportHeight) && message.contentHeight > message.viewportHeight+1;
      r.overflow ||= Number.isFinite(message.contentWidth) && Number.isFinite(message.viewportWidth) && message.contentWidth > message.viewportWidth+1;
      clearInterval(r.watchdog);
    }
    else if (message.type === "error") { r.error = String(message.message || "演示运行失败").slice(0,1200); clearInterval(r.watchdog); }
    else if (message.type === "state") this.changeState(r,message.values,message.serial);
    this.status(r);
  }
  changeState(r, values, serial) {
    if (this.readOnly(r) || r.stopped || !Number.isSafeInteger(serial) || serial < r.serial) return;
    // Limit bursts independently from persistence; dragging remains local and immediate.
    const now = Date.now();
    if (!r.rate || now-r.rate.start > 1000) r.rate = {start:now,count:0};
    if (++r.rate.count > 90) return;
    try {
      InteractiveCards.validateState(values);
      r.serial = serial;
      if (JSON.stringify(values) === JSON.stringify(r.values)) return;
      if (!r.dirty) r.base = r.card.content_version;
      r.values = structuredClone(values); r.dirty = true; r.error = null;
      this.schedule(r); this.options.changed(); this.refresh();
    } catch (error) { r.serial = serial; r.error = error.message; this.sendData(r); this.status(r); }
  }
  schedule(r) {
    clearTimeout(r.timer); r.timer = setTimeout(() => this.flush(r),350);
  }
  async flush(r) {
    if (!r.dirty || r.saving || this.readOnly(r) || this.options.capture() || this.options.isBusy(r.card.id)) return;
    const sent = structuredClone(r.values), base = r.base, operation = {done:false};
    r.saving = true; this.status(r);
    const accept = saved => {
      if (operation.done) return; operation.done = true;
      r.saving = false; r.conflict = false; r.base = saved.content_version;
      r.dirty = JSON.stringify(r.values) !== JSON.stringify(sent); r.error = null;
      this.updateCard(r.card); this.options.changed(); this.refresh();
    };
    try { await this.options.save(r.card.id,sent,base,accept); }
    catch (error) {
      r.saving = false;
      if (error.business?.code === "VERSION_CONFLICT") r.conflict = true;
      r.error = error.message+" 参数草稿仍保留，可重新运行采用最新状态。";
    }
    finally { this.sendData(r); this.status(r); this.options.changed(); }
  }
  async settled(timeout = 9000) {
    const start = Date.now();
    const pending = r => this.visible(r) && !r.ready && !r.error && !r.stopped;
    while ([...this.records.values()].some(pending)) {
      if (Date.now()-start > timeout) {
        for (const r of this.records.values()) if (pending(r)) this.halt(r,"截图等待演示就绪超时。");
        break;
      }
      await new Promise(resolve => setTimeout(resolve,40));
    }
    return this.diagnostics();
  }
  diagnostics() {
    return [...this.records.values()].filter(r => r.root?.isConnected).map(r => ({id:r.card.id,
      ready:!!r.ready,error:r.error || null,math_errors:r.mathErrors || 0,body_overflow:!!r.overflow,input_errors:r.inputErrors || {},sources:r.sources || {}}));
  }
}
