"use strict";

// The MCP renderer serves this page from a private, intercepted origin. It shares
// the normal card renderer but never starts polling or sends a write request.
async function loadCapture() {
  try {
    document.body.classList.add("capture-mode");
    const response = await fetch("/__snapshot__");
    if (!response.ok) throw new Error("截图快照不可用。");
    const payload = await response.json();
    app.info = payload.board; app.boardId = payload.board.id;
    app.cards = new Map(payload.cards.map(card => [card.id, card]));
    app.bindingCards = new Map([...(payload.source_cards || []),...payload.cards].map(card => [card.id,card]));
    app.assets = new Map(payload.assets.map(asset => [asset.id, asset]));
    app.loaded = true; app.connected = true; app.revision = payload.revision;
    space.ready = true; space.userMoved = true;
    space.prefs.theme = payload.options.theme || "light"; space.applyPrefs();
    renderGrid();
    await document.fonts.ready;
    const imageWarnings = [];
    await Promise.all([...document.images].map(async image => {
      try { await image.decode(); } catch { imageWarnings.push("图片未能渲染：" + image.getAttribute("src")); }
    }));
    const cards = payload.cards.map(card => ({id:card.id, type:card.type, title:card.title,
      version:card.version, content_version:card.content_version, layout_version:card.layout_version,
      world:space.rect(card), body_overflow:cardElement(card.id).querySelector(".card-body").scrollHeight >
        cardElement(card.id).querySelector(".card-body").clientHeight}));
    const width = innerWidth, height = innerHeight;
    let bounds = payload.options.region, padding = 0;
    if (!bounds && cards.length) {
      const x = Math.min(...cards.map(card => card.world.x)), y = Math.min(...cards.map(card => card.world.y));
      bounds = {x,y,w:Math.max(...cards.map(card => card.world.x+card.world.w))-x,
        h:Math.max(...cards.map(card => card.world.y+card.world.h))-y};
      padding = 32;
    }
    bounds ||= {x:0,y:0,w:width,h:height};
    const zoom = Math.min((width-padding*2)/bounds.w,(height-padding*2)/bounds.h,
      payload.options.view === "region" ? Infinity : 1);
    const camera = {x:width/2-(bounds.x+bounds.w/2)*zoom,y:height/2-(bounds.y+bounds.h/2)*zoom,zoom};
    space.camera = camera; space.paint();
    // Position far-away world cards before waiting for their iframe animations.
    // Only demonstrations intersecting the requested image need to be ready.
    const interactiveResults = await interactive.settled();
    for (const card of cards) {
      const w = card.world;
      card.pixels = {x:camera.x+w.x*zoom,y:camera.y+w.y*zoom,w:w.w*zoom,h:w.h*zoom};
      card.visible = card.pixels.x < width && card.pixels.x+card.pixels.w > 0 &&
        card.pixels.y < height && card.pixels.y+card.pixels.h > 0;
    }
    window.boardCapture = {camera,focus_bounds:bounds,
      visible_world:{x:-camera.x/zoom,y:-camera.y/zoom,w:width/zoom,h:height/zoom},
      width,height,cards,warnings:imageWarnings,
      math_errors:document.querySelectorAll(".math-error").length};
    window.boardCapture.interactive = interactiveResults;
    for (const result of interactiveResults) {
      window.boardCapture.math_errors += result.math_errors;
      const card = window.boardCapture.cards.find(card => card.id === result.id);
      if (card) card.body_overflow ||= result.body_overflow;
      if (result.error) window.boardCapture.warnings.push("交互演示 "+result.id+"："+result.error);
      if (Object.keys(result.input_errors).length) window.boardCapture.warnings.push("交互演示输入不可用："+result.id);
    }
    document.body.inert = true;
    document.body.dataset.captureReady = "true";
  } catch (error) {
    document.body.dataset.captureError = error.message;
  }
}
