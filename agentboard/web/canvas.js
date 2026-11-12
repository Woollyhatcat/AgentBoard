"use strict";

class FreeCanvas {
  constructor(viewport, world, getCards, getSelected) {
    this.viewport = viewport; this.world = world; this.getCards = getCards; this.getSelected = getSelected;
    this.key = "agentboard:board_local:camera:v2";
    this.prefKey = "agentboard:appearance:v2";
    this.camera = {x: 70, y: 50, zoom: 1};
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
    this.viewport.setAttribute("aria-description", `自由画布，缩放 ${Math.round(zoom * 100)}%。空白拖动平移，Ctrl 加滚轮缩放。`);
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
    const previous = this.camera.zoom, zoom = Math.max(.1, Math.min(4, value));
    this.camera.x = x - (x - this.camera.x) * zoom / previous;
    this.camera.y = y - (y - this.camera.y) * zoom / previous;
    this.camera.zoom = zoom; this.userMoved = true; this.paint(); this.store();
  }
  rect(card) {
    const node = document.getElementById(card.id);
    return {...card.layout, h: node?.offsetHeight || card.layout.h};
  }
  fit(cards = this.getCards(), allowEnlarge = false) {
    if (!cards.length) { this.origin(); return; }
    const boxes = cards.map(card => this.rect(card));
    const left = Math.min(...boxes.map(box => box.x)), top = Math.min(...boxes.map(box => box.y));
    const right = Math.max(...boxes.map(box => box.x + box.w)), bottom = Math.max(...boxes.map(box => box.y + box.h));
    const zoom = Math.max(.1, Math.min(allowEnlarge ? 2 : 1, (this.width() - 80) / (right - left),
      (this.viewport.clientHeight - 80) / (bottom - top)));
    this.camera = {x: this.width() / 2 - (left + right) / 2 * zoom,
      y: this.viewport.clientHeight / 2 - (top + bottom) / 2 * zoom, zoom};
    this.paint(); this.store();
  }
  initialFit() {
    if (this.ready || !this.getCards().length) return;
    this.ready = true;
    if (!this.restored && !this.userMoved) {
      requestAnimationFrame(() => { if (!this.userMoved) this.fit(); });
      document.fonts?.ready.then(() => { if (!this.userMoved && !this.restored) this.fit(); });
    }
  }
  origin() { this.camera = {x: 70, y: 50, zoom: 1}; this.userMoved = true; this.paint(); this.store(); }
  focus(card) { this.userMoved = true; this.fit([card], true); }
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
    document.addEventListener("keydown", event => {
      if (event.code === "Space" && !event.target.closest("input,textarea,select,[contenteditable=true],dialog")) {
        this.space = true; this.viewport.classList.add("pan-ready"); event.preventDefault();
      }
    });
    document.addEventListener("keyup", event => { if (event.code === "Space") { this.space = false; this.viewport.classList.remove("pan-ready"); } });
    window.addEventListener("blur", () => { this.space = false; this.viewport.classList.remove("pan-ready"); });
    this.viewport.addEventListener("pointerdown", event => {
      const blank = !event.target.closest(".card,button,input,textarea,select,a,.canvas-controls,.empty");
      if (!(event.button === 1 || (event.button === 0 && (blank || this.space)))) return;
      event.preventDefault(); event.stopPropagation();
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
      if (document.body.classList.contains("card-dragging")) { event.preventDefault(); return; }
      const body = event.target.closest(".card-body");
      if (!event.ctrlKey && body && (body.scrollHeight > body.clientHeight || body.scrollWidth > body.clientWidth)) return;
      event.preventDefault(); this.userMoved = true;
      if (event.ctrlKey) {
        const rect = this.viewport.getBoundingClientRect();
        this.zoomAt(this.camera.zoom * Math.exp(-event.deltaY * .002), event.clientX - rect.left, event.clientY - rect.top);
      } else {
        const scale = event.deltaMode === 1 ? 16 : 1;
        this.camera.x -= event.deltaX * scale; this.camera.y -= event.deltaY * scale; this.paint(); this.store();
      }
    }, {passive: false});
    new ResizeObserver(() => this.paint()).observe(this.viewport);
  }
  installControls() {
    const controls = document.createElement("div"); controls.className = "canvas-controls";
    const add = (text, title, action) => {
      const button = document.createElement("button"); button.textContent = text; button.title = title;
      button.setAttribute("aria-label", title); button.addEventListener("click", action); controls.append(button); return button;
    };
    add("−", "缩小画布", () => this.zoomAt(this.camera.zoom / 1.2));
    this.zoomLabel = add("100%", "恢复100%缩放", () => this.zoomAt(1));
    add("＋", "放大画布", () => this.zoomAt(this.camera.zoom * 1.2));
    add("查看全部", "适配全部卡片", () => { this.userMoved = true; this.fit(); });
    add("原点", "返回画布原点", () => this.origin());
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
