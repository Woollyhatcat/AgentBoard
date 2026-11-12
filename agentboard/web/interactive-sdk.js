"use strict";
(() => {
  const config = __BOARD_FRAME_CONFIG__;
  const copy = value => structuredClone(value);
  let data, resolveReady, requestedReady = false, deferred = false, serial = 0, scheduled;
  const subscribers = new Set();
  const ready = new Promise(resolve => { resolveReady = resolve; });
  const send = (type, extra = {}) => parent.postMessage({channel:"agentboard-interactive",key:config.key,type,...extra},"*");
  const report = error => send("error",{message:String(error?.message || error).slice(0,1200)});
  const afterLayout = () => new Promise(resolve => {
    // Chromium suspends animation frames in offscreen iframes. Parameter delivery
    // and readiness must still progress when a card is outside the camera.
    let frame, timer;
    const done = () => { cancelAnimationFrame(frame); clearTimeout(timer); resolve(); };
    frame = requestAnimationFrame(done); timer = setTimeout(done,150);
  });
  const notify = () => {
    for (const listener of subscribers) {
      try { listener(copy(data)); } catch (error) { report(error); }
    }
  };
  async function rendered() {
    requestedReady = true;
    await document.fonts.ready;
    await afterLayout(); await afterLayout();
    send("rendered",{mathErrors:document.querySelectorAll(".katex-error").length,
      contentHeight:document.documentElement.scrollHeight,viewportHeight:innerHeight,
      contentWidth:document.documentElement.scrollWidth,viewportWidth:innerWidth});
  }
  const api = {
    version:"1.0", ready,
    getState:() => copy(data?.state || {}),
    getInputs:() => copy(data?.inputs || {}),
    onChange(listener) {
      if (typeof listener !== "function") throw new TypeError("onChange需要函数");
      subscribers.add(listener);
      if (data) { try { listener(copy(data)); } catch (error) { report(error); } }
      return () => subscribers.delete(listener);
    },
    setState(patch) {
      if (!data) throw new Error("请先等待Board.ready");
      if (data.readOnly) throw new Error("当前演示只读，参数未保存");
      if (!patch || typeof patch !== "object" || Array.isArray(patch)) throw new TypeError("参数补丁必须是JSON对象");
      data.state = {...data.state,...copy(patch)};
      serial++; notify();
      // Coalesce pointer events; the host performs bounded validation and persistence.
      if (!scheduled) {
        scheduled = true;
        afterLayout().then(() => { scheduled = false; send("state",{values:data.state,serial}); });
      }
    },
    reset() {
      if (!data || data.readOnly) return;
      data.state = copy(data.initialState); serial++; notify(); send("state",{values:data.state,serial});
    },
    rendered,
    deferReady() { deferred = true; },
    math(element, latex, options = {}) {
      katex.render(String(latex),element,{...options,trust:false,throwOnError:false,maxExpand:1000,maxSize:20});
    }
  };
  Object.defineProperty(window,"Board",{value:Object.freeze(api),writable:false});
  addEventListener("error", event => { report(event.error || event.message || "演示资源加载失败，请使用内联代码与内置公式库。"); event.preventDefault(); });
  addEventListener("unhandledrejection", event => { report(event.reason); event.preventDefault(); });
  addEventListener("message", event => {
    const message = event.data;
    if (event.source !== parent || !message || message.channel !== "agentboard-interactive" || message.key !== config.key) return;
    if (message.type === "data") {
      const first = !data;
      const localState = data?.state;
      data = copy(message.data);
      if (serial > (message.serial || 0) && localState) data.state = localState;
      serial = Math.max(serial,message.serial || 0);
      const dark = data.theme === "dark";
      document.documentElement.style.setProperty("--board-bg",dark ? "#1d2c33" : "#fff");
      document.documentElement.style.setProperty("--board-ink",dark ? "#e0eae7" : "#233538");
      document.documentElement.style.colorScheme = data.theme;
      if (first) resolveReady(copy(data));
      notify();
      // Plain synchronous HTML/JS can omit rendered(); async work calls it explicitly.
      if (first) afterLayout().then(afterLayout).then(() => { if (!requestedReady && !deferred) rendered(); });
    }
  });
  if (document.readyState === "loading") addEventListener("DOMContentLoaded",() => send("hello"),{once:true});
  else send("hello");
})();
