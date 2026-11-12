"use strict";

// Local, pinned libraries; parsing is shared by saved cards and the live preview.
const BoardText = (() => {
  const md = window.markdownit({html: false, breaks: true, linkify: false, typographer: false});
  const escape = md.utils.escapeHtml;
  function mathHTML(source, display) {
    try {
      return katex.renderToString(source, {displayMode: display, throwOnError: true,
        trust: false, strict: "warn", maxExpand: 1000, maxSize: 20, output: "htmlAndMathml"});
    } catch (error) {
      return `<span class="math-error" title="${escape(error.message)}">公式错误：<code>${escape(source)}</code></span>`;
    }
  }
  function findClose(source, close, start) {
    let at = source.indexOf(close, start);
    while (at >= 0) {
      let backslashes = 0;
      for (let i = at - 1; i >= 0 && source[i] === "\\"; i--) backslashes++;
      if (backslashes % 2 === 0) return at;
      at = source.indexOf(close, at + close.length);
    }
    return -1;
  }
  md.inline.ruler.before("escape", "board_math", (state, silent) => {
    const start = state.pos;
    let open, close, display = false;
    if (state.src.startsWith("\\(", start)) { open = "\\("; close = "\\)"; }
    else if (state.src.startsWith("\\[", start)) { open = "\\["; close = "\\]"; display = true; }
    else if (state.src.startsWith("$$", start)) { open = close = "$$"; display = true; }
    else if (state.src[start] === "$" && !/\s/.test(state.src[start + 1] || " ")) open = close = "$";
    else return false;
    const end = findClose(state.src, close, start + open.length);
    if (end < 0 || (close === "$" && /\s/.test(state.src[end - 1]))) return false;
    if (!silent) {
      const token = state.push("board_math", "", 0);
      token.content = state.src.slice(start + open.length, end);
      token.meta = {display};
    }
    state.pos = end + close.length;
    return true;
  });
  md.block.ruler.before("fence", "board_math_block", (state, startLine, endLine, silent) => {
    const begin = state.bMarks[startLine] + state.tShift[startLine];
    const line = state.src.slice(begin, state.eMarks[startLine]);
    const open = line.startsWith("$$") ? "$$" : line.startsWith("\\[") ? "\\[" : null;
    if (!open) return false;
    const close = open === "$$" ? "$$" : "\\]";
    let last = startLine, end = findClose(state.src, close, begin + open.length);
    if (end < 0) return false;
    while (last < endLine && state.eMarks[last] < end + close.length) last++;
    if (last >= endLine || state.src.slice(end + close.length, state.eMarks[last]).trim()) return false;
    if (silent) return true;
    const token = state.push("board_math", "", 0);
    token.block = true; token.map = [startLine, last + 1];
    token.content = state.src.slice(begin + open.length, end).trim(); token.meta = {display: true};
    state.line = last + 1;
    return true;
  });
  md.renderer.rules.board_math = (tokens, at) => mathHTML(tokens[at].content, tokens[at].meta.display);
  md.core.ruler.after("inline", "board_tasks", state => {
    for (let i = 2; i < state.tokens.length; i++) {
      const token = state.tokens[i];
      if (token.type !== "inline" || state.tokens[i - 1].type !== "paragraph_open" ||
          state.tokens[i - 2].type !== "list_item_open") continue;
      const first = token.children?.[0];
      const match = first?.type === "text" && first.content.match(/^\[([ xX])\] /);
      if (!match) continue;
      first.content = first.content.slice(match[0].length);
      const box = new state.Token("html_inline", "", 0);
      box.content = `<input type="checkbox" disabled aria-label="任务状态"${match[1] !== " " ? " checked" : ""}> `;
      token.children.unshift(box);
    }
  });
  md.renderer.rules.image = (tokens, at) => {
    const token = tokens[at], source = token.attrGet("src") || "";
    const alt = token.content || "图片";
    return /^\/api\/assets\/asset_[a-f\d]+(?:[/?]|$)/.test(source)
      ? `<img src="${escape(source)}" alt="${escape(alt)}">`
      : `<span class="asset-caption">[图片：${escape(alt)}；请先导入看板资源]</span>`;
  };
  const linkOpen = md.renderer.rules.link_open || ((tokens, at, options, env, self) => self.renderToken(tokens, at, options));
  md.renderer.rules.link_open = (tokens, at, options, env, self) => {
    tokens[at].attrSet("target", "_blank"); tokens[at].attrSet("rel", "noopener noreferrer");
    return linkOpen(tokens, at, options, env, self);
  };
  function render(text, format = "markdown") {
    const node = document.createElement("div");
    node.className = format === "plain" ? "plain-text" : "markdown";
    if (format === "plain") node.textContent = text;
    else node.innerHTML = DOMPurify.sanitize(md.render(text), {ADD_ATTR: ["target"],
      FORBID_TAGS: ["style", "script", "iframe", "object", "embed"], FORBID_ATTR: ["srcdoc"]});
    return node;
  }
  return {render};
})();
