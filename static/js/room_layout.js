/*
 * Room Layout Editor
 *
 * World coordinates (stored / sent to server):
 *   x_in  east  from room centre  (positive = east)
 *   y_in  height from floor       (positive = up)
 *   z_in  south from room centre  (positive = south)
 *
 * Stage coordinates: pixels within #wall-stage at zoom=1.
 *   Origin = top-left corner of stage.
 *   For N/S walls:  stage-x = x_in + wallW/2,  stage-y = wallH - y_in  (in px/in units)
 *   For E/W walls:  stage-x = ±z_in + wallW/2, stage-y = wallH - y_in
 *   Ceiling/floor:  stage-x = x_in + wallW/2,  stage-y = z_in + wallD/2
 *
 * Pan/zoom: CSS transform on #wall-stage.
 *   canvas coords = stageOffsetX + stage-x * zoom
 */
(function () {
  'use strict';

  // ── State ────────────────────────────────────────────────────────────────
  var cfg        = window.ROOM_CONFIG;
  var placements = window.PLACEMENTS;
  var pool       = window.POOL_ARTWORKS;
  var saveUrl    = window.SAVE_URL;
  var csrfToken  = window.CSRF_TOKEN;

  var currentWall = 'N';
  var placementMap = {};
  placements.forEach(function (p) { placementMap[p.artwork.id] = p; });

  // Tracks click order so first/last selected can anchor align/distribute ops
  var selectionOrder = [];
  function clearSelection() {
    stageEl.querySelectorAll('.placed-art.selected').forEach(function (el) { el.classList.remove('selected'); });
    selectionOrder = [];
    if (measureDisplay) measureDisplay.textContent = '';
  }

  // Immutable master list of all artworks (pool + any server-placed artworks not in pool)
  var allArtworks = pool.slice();
  placements.forEach(function (p) {
    if (!allArtworks.some(function (a) { return a.id === p.artwork.id; })) {
      allArtworks.push(p.artwork);
    }
  });

  // Stage geometry (recalculated on wall switch / resize)
  var baseScale   = 1;   // px per inch at zoom=1
  var stageLeft   = 0;   // initial centered offset X (px, in canvas coords)
  var stageTop    = 0;   // initial centered offset Y
  var zoom        = 1;
  var panX        = 0;   // additional pan beyond centered offset
  var panY        = 0;

  var popoverArtId = null;
  var spaceDown    = false;
  var isPanning    = false;
  var panAnchorX, panAnchorY, panAnchorPX, panAnchorPY;

  // ── DOM ───────────────────────────────────────────────────────────────────
  var canvasWrap  = document.getElementById('canvas-wrap');
  var stageEl     = document.getElementById('wall-stage');
  var poolList    = document.getElementById('pool-list');
  var placedList  = document.getElementById('placed-list');
  var saveBtn     = document.getElementById('btn-save');
  var saveStatus  = document.getElementById('save-status');
  var tabs        = document.querySelectorAll('.wall-tab');

  var measureDisplay = document.getElementById('measure-display');
  var posPanel      = document.getElementById('pos-panel');
  var posTitle      = document.getElementById('pos-title');
  var posHoriz      = document.getElementById('pos-horiz');
  var posVert       = document.getElementById('pos-vert');
  var posDepth      = document.getElementById('pos-depth');
  var posHorizLabel = document.getElementById('pos-horiz-label');
  var posDepthLabel = document.getElementById('pos-depth-label');

  // ── Wall dimensions ───────────────────────────────────────────────────────
  function wallDims(wall) {
    var w = cfg.width_in, d = cfg.depth_in, h = cfg.height_in;
    if (wall === 'N' || wall === 'S') return [w, h];
    if (wall === 'E' || wall === 'W') return [d, h];
    return [w, d];
  }

  // ── Stage setup ───────────────────────────────────────────────────────────
  var PAD = 48;  // pixels of padding around wall in canvas

  var WALL_IMG_MAP = {
    'N': cfg.wall_n_img, 'E': cfg.wall_e_img,
    'S': cfg.wall_s_img, 'W': cfg.wall_w_img,
    'ceiling': cfg.ceiling_img, 'floor': cfg.floor_img,
  };

  function updateStageBackground() {
    var url = WALL_IMG_MAP[currentWall];
    if (url) {
      stageEl.style.backgroundImage = 'url(' + url + ')';
      stageEl.style.backgroundSize  = '100% 100%';
    } else {
      stageEl.style.backgroundImage = '';
    }
  }

  function initStage() {
    var dims = wallDims(currentWall);
    var wrapW = canvasWrap.clientWidth;
    var wrapH = canvasWrap.clientHeight;
    baseScale = Math.min((wrapW - PAD * 2) / dims[0], (wrapH - PAD * 2) / dims[1]);
    var stageW = dims[0] * baseScale;
    var stageH = dims[1] * baseScale;
    stageEl.style.width  = stageW + 'px';
    stageEl.style.height = stageH + 'px';
    stageLeft = (wrapW - stageW) / 2;
    stageTop  = (wrapH - stageH) / 2;
    zoom = 1; panX = 0; panY = 0;
    updateStageBackground();
    applyTransform();
  }

  function applyTransform() {
    var tx = stageLeft + panX;
    var ty = stageTop  + panY;
    stageEl.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + zoom + ')';
  }

  // Convert canvas-relative coords → stage-local pixels (before baseScale)
  function canvasToStage(cx, cy) {
    return {
      x: (cx - stageLeft - panX) / zoom,
      y: (cy - stageTop  - panY) / zoom,
    };
  }

  // ── World ↔ stage-pixel conversions ──────────────────────────────────────
  function worldToStage(wall, p) {
    var dims = wallDims(wall);
    var ww = dims[0], wh = dims[1];
    var sx, sy;
    if (wall === 'N' || wall === 'S') { sx = (p.x_in + ww/2) * baseScale; sy = (wh - p.y_in) * baseScale; }
    else if (wall === 'E')             { sx = (p.z_in + ww/2) * baseScale; sy = (wh - p.y_in) * baseScale; }
    else if (wall === 'W')             { sx = (-p.z_in + ww/2) * baseScale; sy = (wh - p.y_in) * baseScale; }
    else /* ceil/floor */              { sx = (p.x_in + ww/2) * baseScale; sy = (p.z_in + wh/2) * baseScale; }
    return { x: sx, y: sy };
  }

  function stageToWorld(wall, sx, sy) {
    var dims = wallDims(wall);
    var ww = dims[0], wh = dims[1];
    var xi = sx / baseScale - ww / 2;
    var yi = wh - sy / baseScale;
    var p = {};
    if (wall === 'N') { p.x_in = xi; p.y_in = yi; p.z_in = -cfg.depth_in / 2; }
    else if (wall === 'S') { p.x_in = xi; p.y_in = yi; p.z_in = cfg.depth_in / 2; }
    else if (wall === 'E') { p.x_in = cfg.width_in / 2; p.y_in = yi; p.z_in = xi; }
    else if (wall === 'W') { p.x_in = -cfg.width_in / 2; p.y_in = yi; p.z_in = -xi; }
    else { p.x_in = xi; p.y_in = wall === 'ceiling' ? cfg.height_in : 0; p.z_in = sy / baseScale - wh / 2; }
    return p;
  }

  function artStagePx(p) {
    var sc  = worldToStage(currentWall, p);
    var aw  = p.artwork.w_in * baseScale;
    var ah  = p.artwork.h_in * baseScale;
    return { left: sc.x - aw / 2, top: sc.y - ah / 2, w: aw, h: ah };
  }

  // ── World-coordinate helpers for popover ─────────────────────────────────
  function worldHoriz(wall, p) {
    if (wall === 'N' || wall === 'S') return p.x_in;
    if (wall === 'E')  return  p.z_in;
    if (wall === 'W')  return -p.z_in;
    return p.x_in;
  }
  function applyHoriz(wall, p, val) {
    if (wall === 'N' || wall === 'S') p.x_in = val;
    else if (wall === 'E')  p.z_in =  val;
    else if (wall === 'W')  p.z_in = -val;
    else                    p.x_in =  val;
  }

  // ── Sidebar ───────────────────────────────────────────────────────────────
  function renderSidebar() {
    poolList.innerHTML = placedList.innerHTML = '';
    allArtworks.forEach(function (art) {
      var p = placementMap[art.id];
      var el = makeSidebarThumb(art, !!p);
      if (p) {
        el.querySelector('.pool-label').textContent += ' (' + p.wall + ')';
        placedList.appendChild(el);
      } else {
        poolList.appendChild(el);
      }
    });
  }

  function makeSidebarThumb(art, placed) {
    var div = document.createElement('div');
    div.className  = 'pool-thumb';
    div.dataset.id = art.id;
    div.draggable  = true;
    div.title      = art.name + (placed ? ' — drag to reposition' : ' — drag onto wall');
    div.innerHTML  = '<img src="' + (art.thumb || art.img) + '" alt="">' +
                     '<div class="pool-label">' + art.name + '</div>';
    div.addEventListener('dragstart', function (e) {
      e.dataTransfer.setData('text/plain', String(art.id));
      div.classList.add('dragging-src');
      // Use a correctly-proportioned ghost instead of the wide sidebar thumbnail
      var ghost = document.createElement('img');
      ghost.src = art.thumb || art.img;
      var GHOST_H = 80;
      var ghostW = ghost.naturalWidth ? Math.round(GHOST_H * ghost.naturalWidth / ghost.naturalHeight) : GHOST_H;
      ghost.style.cssText = 'position:fixed;top:-9999px;height:' + GHOST_H + 'px;width:' + ghostW + 'px;object-fit:fill;pointer-events:none;';
      document.body.appendChild(ghost);
      e.dataTransfer.setDragImage(ghost, ghostW / 2, GHOST_H / 2);
      setTimeout(function () { ghost.remove(); }, 0);
    });
    div.addEventListener('dragend', function () { div.classList.remove('dragging-src'); });
    return div;
  }

  // ── Wall rendering ────────────────────────────────────────────────────────
  function clearPlacedDivs() {
    stageEl.querySelectorAll('.placed-art').forEach(function (el) { el.remove(); });
  }

  var CORNER_T = 6;  // corner strip thickness in inches

  function addCornerDivs() {
    var dims = wallDims(currentWall);
    var wIn = dims[0], hIn = dims[1];
    var T   = CORNER_T * baseScale;
    var wPx = wIn * baseScale, hPx = hIn * baseScale;
    var corners = [
      { id: 'corner-left',   left: 0,        top: 0,        w: T,   h: hPx },
      { id: 'corner-right',  left: wPx - T,  top: 0,        w: T,   h: hPx },
      { id: 'corner-top',    left: 0,        top: 0,        w: wPx, h: T   },
      { id: 'corner-bottom', left: 0,        top: hPx - T,  w: wPx, h: T   },
    ];
    corners.forEach(function (c) {
      var div = document.createElement('div');
      div.className = 'placed-art corner';
      div.dataset.id = c.id;
      div.style.left   = c.left + 'px';
      div.style.top    = c.top  + 'px';
      div.style.width  = c.w   + 'px';
      div.style.height = c.h   + 'px';
      addSelectableListener(div, c.id);
      stageEl.appendChild(div);
    });
  }

  function addObstacleDivs() {
    var wallObs = (cfg.obstacles || []).filter(function (ob) { return ob.wall === currentWall; });
    wallObs.forEach(function (ob) {
      var div = document.createElement('div');
      div.className = 'placed-art obstacle';
      div.dataset.id = 'obs-' + ob.id;
      var sc  = worldToStage(currentWall, ob);
      var wPx = ob.w_in * baseScale;
      var hPx = ob.h_in * baseScale;
      div.style.left   = (sc.x - wPx / 2) + 'px';
      div.style.top    = (sc.y - hPx / 2) + 'px';
      div.style.width  = wPx + 'px';
      div.style.height = hPx + 'px';
      var lbl = document.createElement('div');
      lbl.className = 'obstacle-label';
      lbl.textContent = ob.label;
      div.appendChild(lbl);
      addSelectableListener(div, 'obs-' + ob.id);
      stageEl.appendChild(div);
    });
  }

  // Shared click-to-select handler for corners and obstacles (no popover, no drag)
  function addSelectableListener(div, id) {
    div.addEventListener('click', function (e) {
      e.stopPropagation();
      var wasSelected = div.classList.contains('selected');
      if (!e.shiftKey) {
        clearSelection();
        div.classList.add('selected');
        selectionOrder.push(id);
      } else {
        if (wasSelected) {
          div.classList.remove('selected');
          selectionOrder = selectionOrder.filter(function (i) { return i !== id; });
        } else {
          div.classList.add('selected');
          selectionOrder.push(id);
        }
      }
      if (measureDisplay) measureDisplay.textContent = '';
    });
  }

  function renderWall() {
    closePopover();
    selectionOrder = [];
    initStage();
    clearPlacedDivs();
    addCornerDivs();   // z-index 0 — behind everything
    addObstacleDivs(); // z-index 1 — behind artworks
    Object.values(placementMap).forEach(function (p) {
      if (p.wall === currentWall) addPlacedDiv(p);
    });
  }

  function addPlacedDiv(p) {
    var r   = artStagePx(p);
    var div = document.createElement('div');
    div.className  = 'placed-art';
    div.dataset.id = p.artwork.id;
    div.style.left   = r.left + 'px';
    div.style.top    = r.top  + 'px';
    div.style.width  = r.w    + 'px';
    div.style.height = r.h    + 'px';
    // Use the high-res image on the wall (thumb is only ~200px → blurry when zoomed).
    div.innerHTML =
      '<img src="' + (p.artwork.img || p.artwork.thumb) + '" alt="' + p.artwork.name + '">' +
      '<div class="placard-bar">' + p.artwork.name + '</div>' +
      '<div class="art-dims">' + fmtIn(p.artwork.w_in) + '×' + fmtIn(p.artwork.h_in) + '"</div>' +
      '<div class="hang-info"></div>';

    makeDraggableOnStage(div, p);
    div.addEventListener('click', function (e) {
      e.stopPropagation();
      var id = String(p.artwork.id);  // string for consistent selectionOrder
      var wasSelected = div.classList.contains('selected');
      if (!e.shiftKey) {
        clearSelection();
        div.classList.add('selected');
        selectionOrder.push(id);
        openPopover(p, div);
      } else {
        if (wasSelected) {
          div.classList.remove('selected');
          selectionOrder = selectionOrder.filter(function (i) { return i !== id; });
        } else {
          div.classList.add('selected');
          selectionOrder.push(id);
        }
      }
      if (measureDisplay) measureDisplay.textContent = '';
    });
    stageEl.appendChild(div);

    // Resize width to match actual image pixel aspect ratio (height stays fixed at h_in).
    // Use a standalone Image object — unlike an off-DOM img element, it reliably reports
    // naturalWidth for cached images without requiring the load event to fire.
    var arImg = new Image();
    function applyAspect() {
      if (!arImg.naturalWidth || !arImg.naturalHeight) return;
      var newW = r.h * arImg.naturalWidth / arImg.naturalHeight;
      div.style.width = newW + 'px';
      div.style.left  = (worldToStage(currentWall, p).x - newW / 2) + 'px';
      fitLabel(div);
      updateHangInfo(div);
    }
    arImg.onload = applyAspect;
    arImg.src = p.artwork.img || p.artwork.thumb;
    if (arImg.complete && arImg.naturalWidth) applyAspect();
    fitLabel(div);
    updateHangInfo(div);
  }

  // Shrink an element's font so its single-line text fits its box width.
  // Uses canvas text measurement (independent of overflow/ellipsis state, which
  // makes scrollWidth unreliable) so text never clips regardless of artwork size.
  var _fitCanvas = null;
  function _textWidth(text, fontPx, cs) {
    if (!_fitCanvas) _fitCanvas = document.createElement('canvas');
    var ctx = _fitCanvas.getContext('2d');
    ctx.font = (cs.fontStyle || 'normal') + ' ' + (cs.fontWeight || '400') + ' ' +
               fontPx + 'px ' + (cs.fontFamily || 'sans-serif');
    return ctx.measureText(text).width;
  }
  function fitTextEl(el, maxFont) {
    if (!el || !el.textContent) return;
    var cs = getComputedStyle(el);
    var padX = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
    var avail = el.clientWidth - padX;              // usable content width
    if (avail <= 0) { el.style.fontSize = maxFont + 'px'; return; }  // hidden — refit when shown
    var w = _textWidth(el.textContent, maxFont, cs);
    // 0.97 safety margin absorbs sub-pixel rounding so the ellipsis never triggers
    el.style.fontSize = (w > avail ? Math.max(0.5, maxFont * avail / w * 0.97) : maxFont) + 'px';
  }
  var LABEL_MAX_FONT = 8, HANG_MAX_FONT = 7;   // px at zoom=1; JS scales down to fit

  // Compact inches: drop trailing ".0" (12.0 → "12", 12.5 → "12.5")
  function fmtIn(v) { var r = Math.round(v * 10) / 10; return r % 1 === 0 ? r.toFixed(0) : r.toFixed(1); }

  function fitLabel(div) {
    fitTextEl(div.querySelector('.placard-bar'), LABEL_MAX_FONT);
    fitTextEl(div.querySelector('.art-dims'),    HANG_MAX_FONT);
  }

  // Floor-to-bottom + center-to-nearest-edge, computed from the div's live
  // pixel geometry so it stays correct mid-drag.  Meaningful for walls only.
  function updateHangInfo(div) {
    var hi = div.querySelector('.hang-info');
    if (!hi) return;
    if (currentWall === 'ceiling' || currentWall === 'floor') { hi.textContent = ''; return; }
    var dims = wallDims(currentWall);
    var ww = dims[0], wh = dims[1];
    var left = parseFloat(div.style.left);
    var top  = parseFloat(div.style.top);
    var w    = parseFloat(div.style.width);
    var h    = parseFloat(div.style.height);
    var floorToBottom = wh - (top + h) / baseScale;   // inches, floor = 0
    var leftH  = left / baseScale - ww / 2;            // horiz of left edge from wall center
    var rightH = (left + w) / baseScale - ww / 2;      // horiz of right edge
    var nearest = Math.min(Math.abs(leftH), Math.abs(rightH));
    hi.textContent = '↥' + round1(floorToBottom) + '"  ↔' + round1(nearest) + '"';
    fitTextEl(hi, HANG_MAX_FONT);
  }

  // ── Drag placed artworks on stage ─────────────────────────────────────────
  function makeDraggableOnStage(div, p) {
    var startMx, startMy, startL, startT;
    div.addEventListener('mousedown', function (e) {
      if (e.button !== 0 || spaceDown) return;
      e.preventDefault();
      startMx = e.clientX; startMy = e.clientY;
      startL  = parseFloat(div.style.left);
      startT  = parseFloat(div.style.top);
      var dragMx = e.clientX, dragMy = e.clientY, dragRaf = 0;
      function applyDrag() {
        dragRaf = 0;
        // Mouse delta in canvas pixels → stage pixels (divide by zoom)
        div.style.left = (startL + (dragMx - startMx) / zoom) + 'px';
        div.style.top  = (startT + (dragMy - startMy) / zoom) + 'px';
        updateHangInfo(div);
      }
      function onMove(ev) {
        dragMx = ev.clientX; dragMy = ev.clientY;
        if (!dragRaf) dragRaf = requestAnimationFrame(applyDrag);  // coalesce to 1 write/frame
      }
      function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        if (dragRaf) { cancelAnimationFrame(dragRaf); applyDrag(); }
        syncWorldFromDiv(div, p);
        if (popoverArtId === p.artwork.id) updatePopoverValues(p);
        scheduleSave();
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  function syncWorldFromDiv(div, p) {
    var r  = artStagePx(p);
    var sx = parseFloat(div.style.left) + parseFloat(div.style.width) / 2;
    var sy = parseFloat(div.style.top)  + r.h / 2;
    var w  = stageToWorld(currentWall, sx, sy);
    p.x_in = w.x_in; p.y_in = w.y_in; p.z_in = w.z_in;
    updateHangInfo(div);
  }

  function syncDivFromWorld(div, p) {
    var r = artStagePx(p);
    var w = parseFloat(div.style.width);
    div.style.left = (worldToStage(currentWall, p).x - w / 2) + 'px';
    div.style.top  = r.top + 'px';
    updateHangInfo(div);
  }

  // ── Drop from sidebar ─────────────────────────────────────────────────────
  canvasWrap.addEventListener('dragover', function (e) { e.preventDefault(); });
  canvasWrap.addEventListener('drop', function (e) {
    e.preventDefault();
    var id  = parseInt(e.dataTransfer.getData('text/plain'), 10);
    if (!id) return;
    var art = findArtwork(id);
    if (!art) return;
    var wrapRect = canvasWrap.getBoundingClientRect();
    var sp = canvasToStage(e.clientX - wrapRect.left, e.clientY - wrapRect.top);
    var w  = stageToWorld(currentWall, sp.x, sp.y);

    if (placementMap[id]) {
      placements = placements.filter(function (p) { return p.artwork.id !== id; });
      var old = stageEl.querySelector('.placed-art[data-id="' + id + '"]');
      if (old) old.remove();
    }
    var p = { artwork: art, wall: currentWall, x_in: w.x_in, y_in: w.y_in, z_in: w.z_in };
    placementMap[id] = p;
    placements.push(p);
    addPlacedDiv(p);
    renderSidebar();
    scheduleSave();
  });

  function findArtwork(id) {
    for (var i = 0; i < allArtworks.length; i++) if (allArtworks[i].id === id) return allArtworks[i];
    return null;
  }

  // ── Pan & Zoom ────────────────────────────────────────────────────────────
  var MIN_ZOOM = 0.15, MAX_ZOOM = 8;

  // Mouse-wheel zoom (zoom toward cursor)
  canvasWrap.addEventListener('wheel', function (e) {
    e.preventDefault();
    var factor  = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    var newZoom = Math.max(MIN_ZOOM, Math.min(MAX_ZOOM, zoom * factor));
    var wrapRect = canvasWrap.getBoundingClientRect();
    var mx = e.clientX - wrapRect.left;
    var my = e.clientY - wrapRect.top;
    // Keep point under cursor fixed
    var offX = stageLeft + panX;
    var offY = stageTop  + panY;
    panX = mx - stageLeft - (mx - offX) * newZoom / zoom;
    panY = my - stageTop  - (my - offY) * newZoom / zoom;
    zoom = newZoom;
    applyTransform();
  }, { passive: false });

  // Middle-mouse-button pan
  canvasWrap.addEventListener('mousedown', function (e) {
    if (e.button === 1 || (e.button === 0 && spaceDown)) {
      e.preventDefault();
      isPanning    = true;
      panAnchorX   = e.clientX; panAnchorY   = e.clientY;
      panAnchorPX  = panX;      panAnchorPY  = panY;
      canvasWrap.style.cursor = 'grabbing';
    }
  });
  var panRaf = 0;
  document.addEventListener('mousemove', function (e) {
    if (!isPanning) return;
    panX = panAnchorPX + (e.clientX - panAnchorX);
    panY = panAnchorPY + (e.clientY - panAnchorY);
    if (!panRaf) panRaf = requestAnimationFrame(function () { panRaf = 0; applyTransform(); });
  });
  document.addEventListener('mouseup', function (e) {
    if (isPanning && (e.button === 1 || e.button === 0)) {
      isPanning = false;
      canvasWrap.style.cursor = spaceDown ? 'grab' : '';
    }
  });

  // Space = pan mode (grab cursor)
  document.addEventListener('keydown', function (e) {
    if (e.code === 'Space' && !e.target.matches('input,textarea')) {
      e.preventDefault();
      if (!spaceDown) { spaceDown = true; canvasWrap.style.cursor = 'grab'; }
    }
  });
  document.addEventListener('keyup', function (e) {
    if (e.code === 'Space') { spaceDown = false; if (!isPanning) canvasWrap.style.cursor = ''; }
  });

  // Double-click to reset view
  canvasWrap.addEventListener('dblclick', function (e) {
    if (e.target === canvasWrap || e.target === stageEl) {
      zoom = 1; panX = 0; panY = 0;
      applyTransform();
    }
  });

  // ── Position popover ──────────────────────────────────────────────────────
  function openPopover(p, div) {
    popoverArtId = p.artwork.id;
    posTitle.textContent = p.artwork.name;
    var isCF = (currentWall === 'ceiling' || currentWall === 'floor');
    posHorizLabel.textContent = isCF ? 'East from center (in)' : 'Horiz from center (in, + = right)';
    posDepthLabel.style.display = isCF ? 'block' : 'none';
    posDepth.style.display      = isCF ? 'block' : 'none';
    updatePopoverValues(p);

    // Position popover in canvas coords (right of artwork, clamped to wrap)
    var stageRect = stageEl.getBoundingClientRect();
    var wrapRect  = canvasWrap.getBoundingClientRect();
    var artCanvasLeft  = (parseFloat(div.style.left) * zoom) + (stageRect.left - wrapRect.left);
    var artCanvasRight = artCanvasLeft + parseFloat(div.style.width) * zoom;
    var artCanvasTop   = (parseFloat(div.style.top)  * zoom) + (stageRect.top  - wrapRect.top);
    var panelW = 220;
    var left   = artCanvasRight + 6;
    if (left + panelW > wrapRect.width) left = artCanvasLeft - panelW - 6;
    if (left < 0) left = 4;
    posPanel.style.left    = left + 'px';
    posPanel.style.top     = Math.max(4, artCanvasTop) + 'px';
    posPanel.style.display = 'block';
  }

  function updatePopoverValues(p) {
    posHoriz.value = round1(worldHoriz(currentWall, p));
    posVert.value  = round1(p.y_in);
    if (currentWall === 'ceiling' || currentWall === 'floor') posDepth.value = round1(p.z_in);
  }

  function round1(v) { return Math.round(v * 10) / 10; }

  function closePopover() {
    posPanel.style.display = 'none';
    popoverArtId = null;
  }

  document.getElementById('pos-close').addEventListener('click', closePopover);

  function applyPopoverInputs() {
    if (popoverArtId === null) return;
    var p = placementMap[popoverArtId];
    if (!p) return;
    var h = parseFloat(posHoriz.value), v = parseFloat(posVert.value);
    if (!isNaN(h)) applyHoriz(currentWall, p, h);
    if (!isNaN(v)) p.y_in = Math.max(0, v);
    if ((currentWall === 'ceiling' || currentWall === 'floor') && posDepth.value !== '') {
      var d = parseFloat(posDepth.value);
      if (!isNaN(d)) p.z_in = d;
    }
    var div = stageEl.querySelector('.placed-art[data-id="' + popoverArtId + '"]');
    if (div) syncDivFromWorld(div, p);
  }

  [posHoriz, posVert, posDepth].forEach(function (inp) {
    inp.addEventListener('input', function () { applyPopoverInputs(); scheduleSave(); });
  });

  // Click empty stage/canvas → deselect + close popover
  [canvasWrap, stageEl].forEach(function (el) {
    el.addEventListener('click', function (e) {
      if (e.target === el) { clearSelection(); closePopover(); }
    });
  });

  // ── Wall tabs ─────────────────────────────────────────────────────────────
  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      tabs.forEach(function (t) { t.classList.remove('active'); });
      tab.classList.add('active');
      currentWall = tab.dataset.wall;
      updateStageBackground();
      renderWall();
    });
  });

  // ── Toolbar ───────────────────────────────────────────────────────────────
  function getSelected() {
    // Return in selection order (first clicked = index 0)
    return selectionOrder.map(function (id) {
      return stageEl.querySelector('.placed-art[data-id="' + id + '"]');
    }).filter(Boolean);
  }

  // Center H: align all selected to first-selected's vertical centre
  document.getElementById('btn-align-h').addEventListener('click', function () {
    var sel = getSelected();
    if (sel.length < 2) return;
    var ref_cy = parseFloat(sel[0].style.top) + parseFloat(sel[0].style.height) / 2;
    sel.forEach(function (div) {
      div.style.top = (ref_cy - parseFloat(div.style.height) / 2) + 'px';
      var p = placementMap[parseInt(div.dataset.id, 10)];
      if (p) syncWorldFromDiv(div, p);
    });
    scheduleSave();
  });

  // Center V: align all selected to first-selected's horizontal centre
  document.getElementById('btn-align-v').addEventListener('click', function () {
    var sel = getSelected();
    if (sel.length < 2) return;
    var ref_cx = parseFloat(sel[0].style.left) + parseFloat(sel[0].style.width) / 2;
    sel.forEach(function (div) {
      div.style.left = (ref_cx - parseFloat(div.style.width) / 2) + 'px';
      var p = placementMap[parseInt(div.dataset.id, 10)];
      if (p) syncWorldFromDiv(div, p);
    });
    scheduleSave();
  });

  // Dist H: sum all current horizontal gaps, divide by (n-1), apply equal gap
  document.getElementById('btn-dist-h').addEventListener('click', function () {
    var sel = getSelected();
    if (sel.length < 3) return;
    var sorted = sel.slice().sort(function (a, b) { return parseFloat(a.style.left) - parseFloat(b.style.left); });
    var totalGap = 0;
    for (var i = 0; i < sorted.length - 1; i++) {
      totalGap += parseFloat(sorted[i + 1].style.left) - (parseFloat(sorted[i].style.left) + parseFloat(sorted[i].style.width));
    }
    var gap = totalGap / (sorted.length - 1);
    var x = parseFloat(sorted[0].style.left) + parseFloat(sorted[0].style.width) + gap;
    for (var i = 1; i < sorted.length; i++) {
      sorted[i].style.left = x + 'px';
      x += parseFloat(sorted[i].style.width) + gap;
      var p = placementMap[parseInt(sorted[i].dataset.id, 10)]; if (p) syncWorldFromDiv(sorted[i], p);
    }
    scheduleSave();
  });

  // Dist V: sum all current vertical gaps, divide by (n-1), apply equal gap
  document.getElementById('btn-dist-v').addEventListener('click', function () {
    var sel = getSelected();
    if (sel.length < 3) return;
    var sorted = sel.slice().sort(function (a, b) { return parseFloat(a.style.top) - parseFloat(b.style.top); });
    var totalGap = 0;
    for (var i = 0; i < sorted.length - 1; i++) {
      totalGap += parseFloat(sorted[i + 1].style.top) - (parseFloat(sorted[i].style.top) + parseFloat(sorted[i].style.height));
    }
    var gap = totalGap / (sorted.length - 1);
    var y = parseFloat(sorted[0].style.top) + parseFloat(sorted[0].style.height) + gap;
    for (var i = 1; i < sorted.length; i++) {
      sorted[i].style.top = y + 'px';
      y += parseFloat(sorted[i].style.height) + gap;
      var p = placementMap[parseInt(sorted[i].dataset.id, 10)]; if (p) syncWorldFromDiv(sorted[i], p);
    }
    scheduleSave();
  });

  document.getElementById('btn-remove').addEventListener('click', function () {
    getSelected().forEach(function (div) {
      if (div.classList.contains('obstacle') || div.classList.contains('corner')) return;
      var id = parseInt(div.dataset.id, 10);
      delete placementMap[id];
      placements = placements.filter(function (p) { return p.artwork.id !== id; });
      div.remove();
    });
    selectionOrder = [];
    closePopover();
    renderSidebar();
    scheduleSave();
  });

  // ── Measure ───────────────────────────────────────────────────────────────
  function divWorldCenter(div) {
    var sx = parseFloat(div.style.left) + parseFloat(div.style.width)  / 2;
    var sy = parseFloat(div.style.top)  + parseFloat(div.style.height) / 2;
    return stageToWorld(currentWall, sx, sy);
  }

  function doMeasure() {
    var sel = getSelected();
    if (sel.length !== 2) {
      measureDisplay.textContent = 'Select exactly 2 items to measure';
      return;
    }
    var p1 = divWorldCenter(sel[0]);
    var p2 = divWorldCenter(sel[1]);
    var dx = Math.abs(p2.x_in - p1.x_in);
    var dy = Math.abs(p2.y_in - p1.y_in);
    var dz = Math.abs(p2.z_in - p1.z_in);
    var dist = Math.sqrt(dx * dx + dy * dy + dz * dz);
    var r = function (v) { return Math.round(v * 10) / 10; };
    measureDisplay.textContent =
      'Δx ' + r(dx) + '"  Δy ' + r(dy) + '"' +
      (dz > 0.05 ? '  Δz ' + r(dz) + '"' : '') +
      '  dist ' + r(dist) + '"';
  }

  document.getElementById('btn-measure').addEventListener('click', doMeasure);

  // ── Hang Info toggle (floor-to-bottom + center-to-edge on each artwork) ────
  var hangBtn = document.getElementById('btn-hang-info');
  function applyHangInfoState() {
    var on = localStorage.getItem('roomLayoutHangInfo') === '1';
    stageEl.classList.toggle('hang-on', on);
    if (hangBtn) hangBtn.classList.toggle('active', on);
    if (on) stageEl.querySelectorAll('.placed-art').forEach(function (div) {
      fitTextEl(div.querySelector('.art-dims'), HANG_MAX_FONT);  // now visible → fit
      updateHangInfo(div);
    });
  }
  if (hangBtn) {
    hangBtn.addEventListener('click', function () {
      var on = localStorage.getItem('roomLayoutHangInfo') === '1';
      localStorage.setItem('roomLayoutHangInfo', on ? '0' : '1');
      applyHangInfoState();
    });
  }

  // ── Keyboard: pan (WASD) and artwork nudge (arrows) ─────────────────────
  document.addEventListener('keydown', function (e) {
    if (e.target.matches('input, textarea, select')) return;

    if (e.code === 'KeyM') { doMeasure(); return; }
    var isPan   = (e.code === 'KeyW' || e.code === 'KeyA' || e.code === 'KeyS' || e.code === 'KeyD');
    var isArrow = (e.code === 'ArrowUp' || e.code === 'ArrowDown' || e.code === 'ArrowLeft' || e.code === 'ArrowRight');
    if (!isPan && !isArrow) return;
    e.preventDefault();

    var selected = getSelected();

    // Arrows with artwork(s) selected → nudge in wall-local coordinates (skip obstacles/corners)
    var artworkSelected = selected.filter(function (d) {
      return !d.classList.contains('obstacle') && !d.classList.contains('corner');
    });
    if (isArrow && artworkSelected.length > 0) {
      var step = e.shiftKey ? 0.1 : 1.0;  // inches
      artworkSelected.forEach(function (div) {
        var id = parseInt(div.dataset.id, 10);
        var p  = placementMap[id];
        if (!p) return;
        if (e.code === 'ArrowLeft')  applyHoriz(currentWall, p, worldHoriz(currentWall, p) - step);
        if (e.code === 'ArrowRight') applyHoriz(currentWall, p, worldHoriz(currentWall, p) + step);
        if (e.code === 'ArrowUp')    p.y_in += step;
        if (e.code === 'ArrowDown')  p.y_in = Math.max(0, p.y_in - step);
        syncDivFromWorld(div, p);
      });
      if (popoverArtId !== null && placementMap[popoverArtId]) updatePopoverValues(placementMap[popoverArtId]);
      scheduleSave();
      return;
    }

    // WASD always pans; arrows pan when nothing is selected
    var PAN = 30;  // px per keypress
    if (e.code === 'KeyA' || e.code === 'ArrowLeft')  panX += PAN;
    if (e.code === 'KeyD' || e.code === 'ArrowRight') panX -= PAN;
    if (e.code === 'KeyW' || e.code === 'ArrowUp')    panY += PAN;
    if (e.code === 'KeyS' || e.code === 'ArrowDown')  panY -= PAN;
    applyTransform();
  });

  // ── Save ──────────────────────────────────────────────────────────────────
  var saveTimer = null;

  function doSave() {
    saveStatus.textContent = 'Saving…';
    fetch(saveUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
      body: JSON.stringify({
        room: { width_in: cfg.width_in, depth_in: cfg.depth_in, height_in: cfg.height_in },
        placements: placements.map(function (p) {
          return { artwork_id: p.artwork.id, wall: p.wall, x_in: p.x_in, y_in: p.y_in, z_in: p.z_in };
        }),
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        saveStatus.textContent = data.ok ? 'Saved.' : 'Error: ' + (data.errors || []).join('; ');
        setTimeout(function () { saveStatus.textContent = ''; }, 3000);
      })
      .catch(function () { saveStatus.textContent = 'Save failed.'; });
  }

  function scheduleSave() {
    saveStatus.textContent = 'Unsaved…';
    clearTimeout(saveTimer);
    saveTimer = setTimeout(doSave, 1500);
  }

  saveBtn.addEventListener('click', doSave);

  // ── Resize ────────────────────────────────────────────────────────────────
  window.addEventListener('resize', function () { renderWall(); });

  // ── Sidebar resizer (wider = more thumbnail columns) ───────────────────────
  (function () {
    var sidebar = document.getElementById('sidebar');
    var resizer = document.getElementById('sidebar-resizer');
    var MIN = 120, MAX = 640, DEFAULT = 220;

    var saved = parseFloat(localStorage.getItem('roomLayoutSidebarW'));
    if (saved >= MIN && saved <= MAX) sidebar.style.width = saved + 'px';

    var resizeRaf = 0, pendingW = 0;
    function applyWidth() {
      resizeRaf = 0;
      sidebar.style.width = pendingW + 'px';
    }
    resizer.addEventListener('mousedown', function (e) {
      e.preventDefault();
      resizer.classList.add('dragging');
      document.body.style.userSelect = 'none';
      function onMove(ev) {
        var w = ev.clientX - sidebar.getBoundingClientRect().left;
        pendingW = Math.max(MIN, Math.min(MAX, w));
        if (!resizeRaf) resizeRaf = requestAnimationFrame(applyWidth);  // coalesce writes
      }
      function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        resizer.classList.remove('dragging');
        document.body.style.userSelect = '';
        localStorage.setItem('roomLayoutSidebarW', parseFloat(sidebar.style.width));
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
    resizer.addEventListener('dblclick', function () {
      sidebar.style.width = DEFAULT + 'px';
      localStorage.setItem('roomLayoutSidebarW', DEFAULT);
    });
  }());

  // ── Init ─────────────────────────────────────────────────────────────────
  renderSidebar();
  renderWall();
  applyHangInfoState();

}());
