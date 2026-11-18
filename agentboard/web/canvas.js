"use strict";

class FreeCanvas {
  constructor(viewport, world, getCards, getSelected) {
    this.viewport = viewport; this.world = world; this.getCards = getCards; this.getSelected = getSelected;
    this.key = "agentboard:board_local:camera:v2";
    this.prefKey = "agentboard:appearance:v2";
    this.camera = {x: 70, y: 50, zoom: 1};
    this.cameraTarget = null;
    this.cameraFrame = null;
    this.panKeys = new Map();
    this.reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
    this.prefs = {theme: "light", accent: "green", background: "paper", grid: true, compact: false, snap: false};
    try {
      const saved = JSON.parse(localStorage.getItem(this.key));
      if (saved && [saved.x, saved.y, saved.zoom].every(Number.isFinite) && saved.zoom >= .1 && saved.zoom <= 4) {
        this.camera = saved; this.restored = true;
      }
      const prefs = JSON.parse(localStorage.getItem(this.prefKey));
      if (prefs && typeof prefs === "object") Object.assign(this.prefs, prefs);
    } catch { /* Storage is optional; the live canvas remains usable. */ }
    this.installControls(); this.bindInput(); this.applyPrefs(); this.paint();
    this.ready = false;
  }
  store() {
    try { localStorage.setItem(this.key, JSON.stringify(this.camera)); } catch { /* Private browsing. */ }
  }
  switchBoard(id) {
    this.stopCameraMotion();
    this.store();
    this.key = `agentboard:${id}:camera:v2`;
    this.camera = {x:70,y:50,zoom:1}; this.ready = false; this.restored = false; this.userMoved = false;
    try {
      const saved = JSON.parse(localStorage.getItem(this.key));
      if (saved && [saved.x,saved.y,saved.zoom].every(Number.isFinite) && saved.zoom >= .1 && saved.zoom <= 4) {
        this.camera = saved; this.restored = true;
      }
    } catch { /* Optional local storage. */ }
    this.paint();
  }
  applyPrefs() {
    const root = document.documentElement;
    root.dataset.theme = this.prefs.theme === "dark" ? "dark" : "light";
    root.dataset.compact = String(!!this.prefs.compact);
    const palettes = {green: ["#216657", "#184f43"], blue: ["#316ab0", "#24558d"],
      purple: ["#7956a2", "#634488"], amber: ["#946319", "#754d10"]};
    const palette = palettes[this.prefs.accent] || palettes.green;
    root.style.setProperty("--green", palette[0]); root.style.setProperty("--green-dark", palette[1]);
    this.viewport.dataset.background = this.prefs.background;
    this.viewport.dataset.grid = String(!!this.prefs.grid);
    if (this.snapInput) this.snapInput.checked = !!this.prefs.snap;
    try { localStorage.setItem(this.prefKey, JSON.stringify(this.prefs)); } catch { /* Optional storage. */ }
    this.paint();
  }
  paint() {
    const {x, y, zoom} = this.camera;
    this.world.style.transform = `translate(${x}px,${y}px) scale(${zoom})`;
    this.viewport.style.setProperty("--grid-size", (24 * zoom) + "px");
    this.viewport.style.setProperty("--grid-x", x + "px");
    this.viewport.style.setProperty("--grid-y", y + "px");
    if (this.zoomLabel) this.zoomLabel.textContent = Math.round(zoom * 100) + "%";
    this.viewport.setAttribute("aria-description", `自由画布，缩放 ${Math.round(zoom * 100)}%。用 WASD 或方向键平移，用滚轮缩放。`);
  }
  width() {
    const inspector = document.getElementById("inspector");
    return Math.max(200, this.viewport.clientWidth - (inspector && !inspector.hidden ? inspector.offsetWidth : 0));
  }
  toWorld(clientX, clientY) {
    const rect = this.viewport.getBoundingClientRect();
    return {x: (clientX - rect.left - this.camera.x) / this.camera.zoom,
      y: (clientY - rect.top - this.camera.y) / this.camera.zoom};
  }
  zoomAt(value, x = this.width() / 2, y = this.viewport.clientHeight / 2) {
    const zoom = Math.max(.1, Math.min(4, value));
    if (zoom === (this.cameraTarget?.zoom ?? this.camera.zoom)) return;
    const ratio = zoom / this.camera.zoom;
    this.userMoved = true;
    this.moveCamera({x: x - (x - this.camera.x) * ratio, y: y - (y - this.camera.y) * ratio, zoom}, true);
  }
  zoomBy(factor, x, y) {
    this.zoomAt((this.cameraTarget?.zoom ?? this.camera.zoom) * factor, x, y);
  }
  moveCamera(destination, smooth) {
    if (smooth && !this.reducedMotion.matches) {
      this.cameraTarget = destination;
      this.requestCameraFrame();
      return;
    }
    this.stopCameraMotion();
    this.camera = destination;
    this.paint();
    this.store();
  }
  stopCameraMotion() {
    if (this.cameraFrame !== null) cancelAnimationFrame(this.cameraFrame);
    this.cameraFrame = null;
    this.cameraTarget = null;
    this.panKeys.clear();
  }
  requestCameraFrame() {
    if (this.cameraFrame !== null) return;
    this.cameraTime = performance.now();
    this.cameraFrame = requestAnimationFrame(this.animateCamera);
  }
  animateCamera = time => {
    // Limit the movement per frame when the browser has been busy or hidden.
    const elapsed = Math.max(0, Math.min(32, time - this.cameraTime));
    this.cameraTime = time;
    const target = this.cameraTarget;
    let x = 0;
    let y = 0;
    for (const direction of this.panKeys.values()) {
      x += direction[0];
      y += direction[1];
    }
    x = Math.sign(x);
    y = Math.sign(y);
    const distance = .36 * elapsed / (Math.hypot(x, y) || 1);
    target.x += x * distance;
    target.y += y * distance;

    const blend = this.reducedMotion.matches ? 1 : -Math.expm1(-elapsed / 42);
    const next = {
      x: this.camera.x + (target.x - this.camera.x) * blend,
      y: this.camera.y + (target.y - this.camera.y) * blend,
      zoom: this.camera.zoom + (target.zoom - this.camera.zoom) * blend,
    };
    const settled = Math.abs(target.x - next.x) < .2 && Math.abs(target.y - next.y) < .2 &&
      Math.abs(target.zoom - next.zoom) < .0005;
    this.camera = settled ? {...target} : next;
    this.paint();

    if (this.panKeys.size || !settled) {
      this.cameraFrame = requestAnimationFrame(this.animateCamera);
    } else {
      this.cameraFrame = null;
      this.cameraTarget = null;
      this.store();
    }
  }
  rect(card) {
    const node = document.getElementById(card.id);
    return {...card.layout, h: node?.offsetHeight || card.layout.h};
  }
  fit(cards = this.getCards(), allowEnlarge = false, smooth = false) {
    if (!cards.length) { this.origin(smooth); return; }
    const boxes = cards.map(card => this.rect(card));
    const left = Math.min(...boxes.map(box => box.x)), top = Math.min(...boxes.map(box => box.y));
    const right = Math.max(...boxes.map(box => box.x + box.w)), bottom = Math.max(...boxes.map(box => box.y + box.h));
    const zoom = Math.max(.1, Math.min(allowEnlarge ? 2 : 1, (this.width() - 80) / (right - left),
      (this.viewport.clientHeight - 80) / (bottom - top)));
    this.moveCamera({x: this.width() / 2 - (left + right) / 2 * zoom,
      y: this.viewport.clientHeight / 2 - (top + bottom) / 2 * zoom, zoom}, smooth);
  }
  initialFit() {
    if (this.ready || !this.getCards().length) return;
    this.ready = true;
    if (!this.restored && !this.userMoved) {
      const boardKey = this.key;
      const fitOnLoad = () => {
        if (this.key === boardKey && !this.userMoved && !this.restored) this.fit();
      };
      requestAnimationFrame(fitOnLoad);
      document.fonts?.ready.then(fitOnLoad);
    }
  }
  origin(smooth = false) { this.userMoved = true; this.moveCamera({x: 70, y: 50, zoom: 1}, smooth); }
  focus(card) { this.userMoved = true; this.fit([card], true, true); }
  placement(kind, size) {
    const rect = this.viewport.getBoundingClientRect();
    const center = this.toWorld(rect.left + this.width() / 2, rect.top + this.viewport.clientHeight / 2);
    const boxes = this.getCards().map(card => this.rect(card));
    const base = {space: "world", x: Math.round(center.x - size.w / 2), y: Math.round(center.y - size.h / 2),
      ...size, z: 0, height_mode: kind === "text" ? "auto" : "fixed"};
    for (let ring = 0; ring < 30; ring++) {
      const candidate = {...base, x: base.x + ring * 32, y: base.y + ring * 32};
      if (!boxes.some(box => candidate.x < box.x + box.w && box.x < candidate.x + candidate.w &&
          candidate.y < box.y + box.h && box.y < candidate.y + candidate.h)) return candidate;
    }
    return {...base, x: Math.max(...boxes.map(box => box.x + box.w), base.x) + 24};
  }
  layers() {
    const cards = this.getCards().slice().sort((a, b) => (a.layout.z || 0) - (b.layout.z || 0) ||
      a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
    cards.forEach((card, at) => {
      const node = document.getElementById(card.id);
      if (node) node.style.zIndex = card.id === this.getSelected() ? cards.length + 10 : at + 1;
    });
  }
  bindInput() {
    const panDirections = new Map([
      ["w", [0, 1]], ["arrowup", [0, 1]],
      ["a", [1, 0]], ["arrowleft", [1, 0]],
      ["s", [0, -1]], ["arrowdown", [0, -1]],
      ["d", [-1, 0]], ["arrowright", [-1, 0]],
    ]);
    const usesKeyboard = target => target.isContentEditable ||
      target.closest?.("input,textarea,select,button,a,dialog,#inspector");
    // Focus/scrollIntoView inside embedded programs must not move the world by
    // changing the browser's native scroll offset behind the saved camera.
    this.viewport.addEventListener("scroll",() => {
      if (this.viewport.scrollLeft || this.viewport.scrollTop) this.viewport.scrollTo(0,0);
    });
    document.addEventListener("keydown", event => {
      if (event.defaultPrevented || event.isComposing || usesKeyboard(event.target)) return;
      if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) { this.panKeys.clear(); return; }
      if (event.code === "Space") {
        this.space = true; this.viewport.classList.add("pan-ready"); event.preventDefault();
        return;
      }
      const direction = panDirections.get(event.key.toLowerCase());
      if (!direction || this.viewport.classList.contains("panning") ||
          document.body.classList.contains("card-dragging")) return;
      event.preventDefault();
      if (event.repeat) return;
      this.cameraTarget ??= {...this.camera};
      if (!this.panKeys.size) {
        // Give a tap a short travel distance even if released before the first frame.
        this.cameraTarget.x += direction[0] * 32;
        this.cameraTarget.y += direction[1] * 32;
      }
      this.panKeys.set(event.code || event.key, direction);
      this.userMoved = true;
      this.requestCameraFrame();
    });
    document.addEventListener("keyup", event => {
      this.panKeys.delete(event.code || event.key);
      if (event.code === "Space") { this.space = false; this.viewport.classList.remove("pan-ready"); }
    });
    document.addEventListener("focusin", event => {
      if (usesKeyboard(event.target)) this.panKeys.clear();
    });
    window.addEventListener("blur", () => {
      this.panKeys.clear();
      this.space = false;
      this.viewport.classList.remove("pan-ready");
    });
    window.addEventListener("pagehide", () => {
      if (this.cameraFrame !== null) this.store();
    });
    this.viewport.addEventListener("pointerdown", event => {
      const blank = !event.target.closest(".card,button,input,textarea,select,a,.canvas-controls,.empty");
      if (!(event.button === 1 || (event.button === 0 && (blank || this.space)))) return;
      event.preventDefault(); event.stopPropagation();
      this.stopCameraMotion();
      if (blank && event.button === 0) this.viewport.focus({preventScroll: true});
      const before = {...this.camera}, x = event.clientX, y = event.clientY;
      this.viewport.setPointerCapture(event.pointerId); this.viewport.classList.add("panning");
      const move = next => {
        this.camera.x = before.x + next.clientX - x; this.camera.y = before.y + next.clientY - y;
        this.userMoved = true; this.paint();
      };
      const end = () => { cleanup(); this.store(); };
      const cancel = () => { this.camera = before; this.paint(); cleanup(); };
      const cleanup = () => {
        this.viewport.classList.remove("panning");
        this.viewport.removeEventListener("pointermove", move);
        this.viewport.removeEventListener("pointerup", end); this.viewport.removeEventListener("pointercancel", cancel);
      };
      this.viewport.addEventListener("pointermove", move); this.viewport.addEventListener("pointerup", end);
      this.viewport.addEventListener("pointercancel", cancel);
    }, true);
    this.viewport.addEventListener("wheel", event => {
      if (document.body.classList.contains("card-dragging") || this.viewport.classList.contains("panning")) {
        event.preventDefault();
        return;
      }
      const body = event.target.closest(".card-body");
      if (!event.ctrlKey && !event.metaKey && body) {
        const style = getComputedStyle(body);
        const scrolls = (event.deltaY && body.scrollHeight > body.clientHeight && /^(auto|scroll)$/.test(style.overflowY)) ||
          (event.deltaX && body.scrollWidth > body.clientWidth && /^(auto|scroll)$/.test(style.overflowX));
        if (scrolls) return;
      }
      event.preventDefault();
      if (!event.deltaY) return;
      const pixelsPerUnit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? this.viewport.clientHeight : 1;
      const rect = this.viewport.getBoundingClientRect();
      this.zoomBy(Math.exp(-event.deltaY * pixelsPerUnit * .002),
        event.clientX - rect.left, event.clientY - rect.top);
    }, {passive: false});
    new ResizeObserver(() => this.paint()).observe(this.viewport);
  }
  installControls() {
    const controls = document.createElement("div"); controls.className = "canvas-controls";
    const add = (text, title, action) => {
      const button = document.createElement("button"); button.textContent = text; button.title = title;
      button.setAttribute("aria-label", title); button.addEventListener("click", action); controls.append(button); return button;
    };
    add("−", "缩小画布", () => this.zoomBy(1 / 1.2));
    this.zoomLabel = add("100%", "恢复100%缩放", () => this.zoomAt(1));
    add("＋", "放大画布", () => this.zoomBy(1.2));
    add("查看全部", "适配全部卡片", () => { this.userMoved = true; this.fit(this.getCards(), false, true); });
    add("原点", "返回画布原点", () => this.origin(true));
    add("定位", "定位选中卡片", () => {
      const card = this.getCards().find(item => item.id === this.getSelected()); if (card) this.focus(card);
    });
    const label = document.createElement("label"); label.textContent = " 吸附";
    this.snapInput = document.createElement("input"); this.snapInput.type = "checkbox"; this.snapInput.setAttribute("aria-label", "网格吸附");
    this.snapInput.addEventListener("change", () => { this.prefs.snap = this.snapInput.checked; this.applyPrefs(); });
    label.prepend(this.snapInput); controls.append(label); this.viewport.append(controls);
  }
  preferences() {
    const panel = document.createElement("div"); panel.className = "appearance-form";
    const title = document.createElement("h3"); title.textContent = "本机显示风格"; panel.append(title);
    const options = {
      theme: ["明暗主题", [["light", "浅色"], ["dark", "深色"]]],
      accent: ["主题色", [["green", "松绿"], ["blue", "海蓝"], ["purple", "紫色"], ["amber", "琥珀"]]],
      background: ["画布背景", [["paper", "纸白"], ["warm", "暖纸"], ["slate", "石板"]]],
    };
    for (const [key, [name, choices]] of Object.entries(options)) {
      const label = document.createElement("label"); label.className = "field"; label.textContent = name;
      const select = document.createElement("select"); select.setAttribute("aria-label", name);
      for (const [value, text] of choices) { const option = document.createElement("option"); option.value = value; option.textContent = text; select.append(option); }
      select.value = this.prefs[key]; select.addEventListener("change", () => { this.prefs[key] = select.value; this.applyPrefs(); });
      label.append(select); panel.append(label);
    }
    for (const [key, name] of [["grid", "显示画布网格"], ["compact", "紧凑显示"], ["snap", "网格吸附"]]) {
      const label = document.createElement("label"); label.className = "check-label";
      const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = !!this.prefs[key];
      checkbox.addEventListener("change", () => { this.prefs[key] = checkbox.checked; this.applyPrefs(); }); label.append(checkbox, name); panel.append(label);
    }
    const note = document.createElement("p"); note.className = "dialog-description";
    note.textContent = "显示风格和相机仅在本机保存。正文基准字号在卡片属性中设置，并与 Agent 共享。"; panel.append(note);
    return panel;
  }
}
