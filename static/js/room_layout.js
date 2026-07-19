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
 * Pan: CSS translate on #wall-stage.  Zoom: folded into baseScale (px/inch) and
 * applied by re-laying out at real pixels — no CSS scale() (keeps zoom crisp and
 * repaints cheap).  canvas coords = stageOffset + pan + stage-px
 */
(function () {
  'use strict';

  // ── State ────────────────────────────────────────────────────────────────
  var cfg        = window.ROOM_CONFIG;
  var placements = window.PLACEMENTS;
  var pool       = window.POOL_ARTWORKS;
  var saveUrl    = window.SAVE_URL;
  var csrfToken  = window.CSRF_TOKEN;
  var READONLY   = !!window.LAYOUT_READONLY;   // 2D viewer mode: navigation only, no editing

  // Live sync to any open 3D viewer of the same show (same browser). The 3D
  // viewer listens on this channel and repositions/rebuilds pieces as they move.
  var roomChan = (window.BroadcastChannel && window.ROOM_SLUG)
    ? new BroadcastChannel('room-layout-' + window.ROOM_SLUG) : null;
  function broadcastPlacements() {
    if (!roomChan) return;
    roomChan.postMessage({
      type: 'placements',
      placements: placements.map(function (p) {
        return { artwork: p.artwork, wall: p.wall, x_in: p.x_in, y_in: p.y_in,
                 z_in: p.z_in, rotation: p.rotation || 0, group: p.group,
                 support: p.support == null ? null : p.support };
      }),
      supports: supports.map(function (s) {
        return { id: s.id, wall: s.wall, x_in: s.x_in, y_in: s.y_in,
                 z_in: s.z_in, w_in: s.w_in, h_in: s.h_in, d_in: s.d_in,
                 rotation: s.rotation || 0, texture: s.texture || null };
      }),
    });
  }

  // Escape user-controlled text (artwork titles etc.) before it goes into innerHTML.
  // Artwork names are set by artists and these views are public, so an unescaped
  // title like `<img src=x onerror=…>` would be stored XSS.
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  var currentWall = 'N';
  var placementMap = {};
  placements.forEach(function (p) { placementMap[p.artwork.id] = p; });

  // Tracks click order so first/last selected can anchor align/distribute ops
  var selectionOrder = [];
  function clearSelection() {
    stageEl.querySelectorAll('.placed-art.selected').forEach(function (el) { el.classList.remove('selected'); });
    selectionOrder = [];
    if (measureDisplay) measureDisplay.textContent = '';
    closePopover();   // hide inline position controls when selection is cleared
    // also drop any support selection
    stageEl.querySelectorAll('.support.selected').forEach(function (el) { el.classList.remove('selected'); });
    selectedSupportId = null;
    if (supportPanel) supportPanel.classList.remove('active');
  }

  // Immutable master list of all artworks (pool + any server-placed artworks not in pool)
  var allArtworks = pool.slice();
  placements.forEach(function (p) {
    if (!allArtworks.some(function (a) { return a.id === p.artwork.id; })) {
      allArtworks.push(p.artwork);
    }
  });

  // Stage geometry (recalculated on wall switch / resize)
  // Zoom is folded into baseScale (live px/inch) and applied by re-laying out the
  // stage at real pixels — NOT via a CSS scale() transform.  A scale transform on
  // a stage full of high-res images forces the browser to re-rasterize/downscale
  // every image on each repaint (even a selection border), which is what made
  // clicks feel slow.  Real-pixel layout keeps repaints cheap and zoom crisp.
  var baseScale   = 1;   // LIVE px per inch (fit-scale × zoom)
  var fitScale    = 1;   // px per inch at zoom-to-fit (zoom = 1)
  var stageLeft   = 0;   // initial centered offset X (px, in canvas coords)
  var stageTop    = 0;   // initial centered offset Y
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

  // ── Supports (pedestals / shelves) state + DOM ────────────────────────────
  var supports = window.SUPPORTS || [];
  var supportMap = {};
  supports.forEach(function (s) { supportMap[s.id] = s; });
  var nextSupportTmp = -1;          // temp negative ids for supports added this session
  var selectedSupportId = null;
  var supportPanel = document.getElementById('support-panel');
  var supportList  = document.getElementById('support-list');
  var spW = document.getElementById('sp-w'), spH = document.getElementById('sp-h'), spD = document.getElementById('sp-d');
  var spHoriz = document.getElementById('sp-horiz'), spVert = document.getElementById('sp-vert');

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
    fitScale = baseScale; panX = 0; panY = 0;
    updateStageBackground();
    applyTransform();
  }

  function applyTransform() {
    // Translate only — zoom lives in baseScale (real-pixel layout), no scale().
    var tx = stageLeft + panX;
    var ty = stageTop  + panY;
    stageEl.style.transform = 'translate(' + tx + 'px,' + ty + 'px)';
  }

  // Convert canvas-relative coords → stage-local pixels (no scale transform now)
  function canvasToStage(cx, cy) {
    return {
      x: (cx - stageLeft - panX),
      y: (cy - stageTop  - panY),
    };
  }

  // Re-lay out the stage at a new pixel scale (zoom).  Scales the stage box and
  // every child's geometry by `ratio`, then re-fits text.  No image reload.
  function rescaleStage(ratio, newScale) {
    baseScale = newScale;
    stageEl.style.width  = (parseFloat(stageEl.style.width)  * ratio) + 'px';
    stageEl.style.height = (parseFloat(stageEl.style.height) * ratio) + 'px';
    // Scale every positioned child — pieces AND supports — so they stay glued to
    // the wall/floor as the zoom changes (supports were being left behind, making
    // them look like they floated above the floor while zooming).
    var els = stageEl.querySelectorAll('.placed-art, .support');
    els.forEach(function (el) {
      el.style.left   = (parseFloat(el.style.left)   * ratio) + 'px';
      el.style.top    = (parseFloat(el.style.top)    * ratio) + 'px';
      el.style.width  = (parseFloat(el.style.width)  * ratio) + 'px';
      el.style.height = (parseFloat(el.style.height) * ratio) + 'px';
    });
    stageEl.querySelectorAll('.placed-art').forEach(function (el) {
      if (el.classList.contains('corner') || el.classList.contains('obstacle')) return;
      fitLabel(el);
      updateHangInfo(el);   // text auto-scales with zoom via fitTextEl
    });
    renderGroupBoxes();     // group outlines must follow the rescaled pieces
    renderSupportBoxes();   // support boxes derive from positions — rebuild at new zoom
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

  // On-screen footprint of a placed piece, in inches. Vertical walls: width ×
  // height. Floor/ceiling: a top-down width × depth footprint, with width and
  // depth swapped when the piece is rotated 90°. Flat pieces (depth 0) fall back
  // to height so they stay visible.
  function footprintDims(p) {
    var a = p.artwork;
    if (currentWall === 'floor' || currentWall === 'ceiling') {
      var depth = (a.d_in && a.d_in > 0) ? a.d_in : a.h_in;
      var swapped = (((p.rotation || 0) % 180) === 90);   // 90° or 270° swaps w/d
      return swapped ? { w: depth, h: a.w_in } : { w: a.w_in, h: depth };
    }
    return { w: a.w_in, h: a.h_in };
  }

  function artStagePx(p) {
    var sc = worldToStage(currentWall, p);
    var d  = footprintDims(p);
    var aw = d.w * baseScale, ah = d.h * baseScale;
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

  // Jump to the wall a placed artwork lives on and center the view on it.
  function focusPlacement(art) {
    var p = placementMap[art.id];
    if (!p) return;
    if (p.wall !== currentWall) {
      tabs.forEach(function (t) { t.classList.toggle('active', t.dataset.wall === p.wall); });
      currentWall = p.wall;
      updateStageBackground();
      renderWall();
    }
    // Pan so the artwork's center sits in the middle of the canvas (keeps current zoom)
    var sc = worldToStage(currentWall, p);
    panX = canvasWrap.clientWidth  / 2 - stageLeft - sc.x;
    panY = canvasWrap.clientHeight / 2 - stageTop  - sc.y;
    applyTransform();
    // Highlight + show its position controls
    var div = stageEl.querySelector('.placed-art[data-id="' + art.id + '"]');
    if (div) {
      clearSelection();
      div.classList.add('selected');
      selectionOrder.push(String(art.id));
      openPopover(p);
    }
  }

  function makeSidebarThumb(art, placed) {
    var div = document.createElement('div');
    div.className  = 'pool-thumb';
    div.dataset.id = art.id;
    div.draggable  = true;
    div.title      = art.name + (placed ? ' — click to locate · drag to reposition' : ' — drag onto wall');
    // thumb prefers a crop thumbnail; if it 404s (e.g. a legacy crop whose small
    // spec was never generated) fall back to the display image, never a broken img.
    div.innerHTML  = '<img src="' + (art.thumb || art.img) + '"' +
                     ' onerror="this.onerror=null;this.src=\'' + (art.img || '') + '\'" alt="">' +
                     '<div class="pool-label">' + esc(art.name) + '</div>';
    // Click a placed thumbnail → jump to its wall and center the view on it
    if (placed) {
      div.addEventListener('click', function () { focusPlacement(art); });
    }
    div.addEventListener('dragstart', function (e) {
      e.dataTransfer.setData('text/plain', String(art.id));
      div.classList.add('dragging-src');
      // Use a correctly-proportioned ghost instead of the wide sidebar thumbnail
      var ghost = document.createElement('img');
      ghost.onerror = function () { ghost.onerror = null; ghost.src = art.img || ''; };
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
    stageEl.querySelectorAll('.placed-art, .support').forEach(function (el) { el.remove(); });
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
    if (READONLY) return;                     // read-only 2D viewer: no selection
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
    redrawPieces();
  }

  // Re-render every piece on the current wall at the CURRENT zoom/pan (no view
  // reset). Used after operations that change many pieces (e.g. rotate).
  function redrawPieces() {
    clearPlacedDivs();
    addCornerDivs();   // z-index 0 — behind everything
    addObstacleDivs(); // z-index 1 — behind artworks
    redrawSupports();  // z-index 1 — behind artworks (art sits in front)
    Object.values(placementMap).forEach(function (p) {
      if (p.wall === currentWall) addPlacedDiv(p);
    });
    renderGroupBoxes();
    renderSupportBoxes();
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
    // decoding=async keeps image decode off the paint path so it never blocks UI.
    div.innerHTML =
      '<img src="' + (p.artwork.img || p.artwork.thumb) + '"' +
      ' onerror="this.onerror=null;this.src=\'' + (p.artwork.thumb || '') + '\'"' +
      ' alt="' + esc(p.artwork.name) + '" decoding="async">' +
      '<div class="placard-bar">' + esc(p.artwork.name) + '</div>' +
      '<div class="art-dims">' + fmtIn(footprintDims(p).w) + '×' + fmtIn(footprintDims(p).h) + '"</div>' +
      '<div class="hang-h"></div>' +
      '<div class="hang-v"></div>';

    makeDraggableOnStage(div, p);
    // Selection stays enabled in read-only mode so artists can pick two pieces to
    // measure between; only editing (drag/nudge/delete/rotate) is disabled.
    div.addEventListener('click', function (e) {
      e.stopPropagation();
      var id = String(p.artwork.id);  // string for consistent selectionOrder
      var wasSelected = div.classList.contains('selected');
      if (!e.shiftKey) {
        clearSelection();
        div.classList.add('selected');
        selectionOrder.push(id);
        openPopover(p);
      } else {
        if (wasSelected) {
          div.classList.remove('selected');
          selectionOrder = selectionOrder.filter(function (i) { return i !== id; });
        } else {
          div.classList.add('selected');
          selectionOrder.push(id);
        }
        closePopover();   // multi-select → hide single-artwork position controls
      }
      expandSelectionToGroups();                 // grouped piece → select whole group
      if (selectionOrder.length > 1) closePopover();
      if (measureDisplay) measureDisplay.textContent = '';
    });
    stageEl.appendChild(div);

    // The rectangle is the artwork's real w_in × h_in footprint (set above from
    // artStagePx); the image is best-fit (object-fit: contain) inside it and
    // letterboxed if its pixel aspect ratio differs — no width override.
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
    maxFont = maxFont * (baseScale / fitScale);     // scale text with zoom (zoom to read)
    var cs = getComputedStyle(el);
    var padX = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
    var avail = el.clientWidth - padX;              // usable content width
    if (avail <= 0) { el.style.fontSize = maxFont + 'px'; return; }  // hidden — refit when shown
    var w = _textWidth(el.textContent, maxFont, cs);
    // 0.97 safety margin absorbs sub-pixel rounding so the ellipsis never triggers
    el.style.fontSize = (w > avail ? Math.max(0.5, maxFont * avail / w * 0.97) : maxFont) + 'px';
  }
  var LABEL_MAX_FONT = 8, HANG_MAX_FONT = 7;   // px at zoom=1; JS scales down to fit

  // The artwork name label is a FIXED physical size (not fit-to-width, which made
  // every piece a different size). Sized so ~NAME_CHARS_PER_IN characters fit per
  // inch of wall — i.e. ~24 characters across a 12" piece. CHAR_W_RATIO is the
  // average glyph advance as a fraction of the font size for the label font.
  var NAME_CHARS_PER_IN = 2, CHAR_W_RATIO = 0.5;
  function nameFontPx() { return baseScale / (NAME_CHARS_PER_IN * CHAR_W_RATIO); }

  // Compact inches: drop trailing ".0" (12.0 → "12", 12.5 → "12.5")
  function fmtIn(v) { var r = Math.round(v * 10) / 10; return r % 1 === 0 ? r.toFixed(0) : r.toFixed(1); }

  function fitLabel(div) {
    var bar = div.querySelector('.placard-bar');
    if (bar) bar.style.fontSize = nameFontPx() + 'px';   // fixed physical size; overflow → ellipsis
    fitTextEl(div.querySelector('.art-dims'), HANG_MAX_FONT);
  }

  // Distance from each artwork edge to the corresponding wall edge, computed
  // from the div's live pixel geometry so it stays correct mid-drag.  Works on
  // every surface (walls, floor, ceiling) since it's pure 2D stage geometry.
  function updateHangInfo(div) {
    var hh = div.querySelector('.hang-h');
    var hv = div.querySelector('.hang-v');
    if (!hh || !hv) return;
    var dims = wallDims(currentWall);
    var ww = dims[0], wh = dims[1];
    var left = parseFloat(div.style.left);
    var top  = parseFloat(div.style.top);
    var w    = parseFloat(div.style.width);
    var h    = parseFloat(div.style.height);
    var leftD   = left / baseScale;                  // left edge → left wall edge
    var rightD  = ww - (left + w) / baseScale;       // right edge → right wall edge
    var topD    = top / baseScale;                   // top edge → top wall edge
    var bottomD = wh - (top + h) / baseScale;        // bottom edge → bottom wall edge
    hh.textContent = '←' + fmtIn(leftD) + '"  →' + fmtIn(rightD) + '"';
    hv.textContent = '↑' + fmtIn(topD) + '"  ↓' + fmtIn(bottomD) + '"';
    fitTextEl(hh, HANG_MAX_FONT);
    fitTextEl(hv, HANG_MAX_FONT);
  }

  // ── Drag placed artworks on stage ─────────────────────────────────────────
  // Dragging moves EVERY selected piece together (so groups / multi-selections
  // move as one). Grabbing an unselected piece first selects it (and its group).
  function makeDraggableOnStage(div, p) {
    if (READONLY) return;                     // read-only 2D viewer: no dragging
    div.addEventListener('mousedown', function (e) {
      if (e.button !== 0 || spaceDown) return;
      e.preventDefault();
      // Shift-click is a selection toggle handled by the click handler — don't
      // let the drag setup clear/replace the selection on mousedown.
      if (!e.shiftKey && !div.classList.contains('selected')) {
        clearSelection();
        div.classList.add('selected');
        selectionOrder = [String(p.artwork.id)];
        expandSelectionToGroups();
        if (selectionOrder.length === 1) openPopover(p); else closePopover();
      }
      var movers = getSelected().filter(function (d) {
        return !d.classList.contains('obstacle') && !d.classList.contains('corner');
      }).map(function (d) {
        return { div: d, startL: parseFloat(d.style.left), startT: parseFloat(d.style.top),
                 p: placementMap[parseInt(d.dataset.id, 10)] };
      });
      // A piece on a support drags the support (and its siblings) with it, so an
      // art+support group stays locked together and moves as one.
      var moverIds = {};
      movers.forEach(function (m) { if (m.p) moverIds[m.p.artwork.id] = true; });
      var carriedSids = {};
      movers.forEach(function (m) { if (m.p && m.p.support != null) carriedSids[m.p.support] = true; });
      // Also carry any marquee-selected supports (a rectangle around a piece and
      // its shelf moves both, even if it isn't formally attached).
      stageEl.querySelectorAll('.support.selected').forEach(function (sd) { carriedSids[normSid(sd.dataset.sid)] = true; });
      var supMovers = [];
      Object.keys(carriedSids).forEach(function (sidKey) {
        var sid = normSid(sidKey);
        var sd = stageEl.querySelector('.support[data-sid="' + sidKey + '"]');
        if (sd) supMovers.push({ div: sd, s: supportMap[sid],
                                 startL: parseFloat(sd.style.left), startT: parseFloat(sd.style.top) });
        attachedPlacements(sid).forEach(function (p) {       // pull in un-selected siblings
          if (moverIds[p.artwork.id]) return;
          var ad = stageEl.querySelector('.placed-art[data-id="' + p.artwork.id + '"]');
          if (ad) { movers.push({ div: ad, startL: parseFloat(ad.style.left),
                                  startT: parseFloat(ad.style.top), p: p });
                    moverIds[p.artwork.id] = true; }
        });
      });
      // Group outline(s) of the pieces being dragged — translate them rigidly too.
      var draggedGids = {};
      movers.forEach(function (m) { if (m.p && m.p.group != null) draggedGids[m.p.group] = true; });
      var boxMovers = [];
      stageEl.querySelectorAll('.group-box').forEach(function (b) {
        if (draggedGids[b.dataset.gid]) {
          boxMovers.push({ el: b, startL: parseFloat(b.style.left), startT: parseFloat(b.style.top) });
        }
      });
      var startMx = e.clientX, startMy = e.clientY;
      var preDragSnap = snapshotPlacements();
      var dragMx = e.clientX, dragMy = e.clientY, dragRaf = 0, movedAny = false;
      function applyDrag() {
        dragRaf = 0;
        var dx = dragMx - startMx, dy = dragMy - startMy;
        if (dx || dy) movedAny = true;
        movers.forEach(function (m) {
          m.div.style.left = (m.startL + dx) + 'px';
          m.div.style.top  = (m.startT + dy) + 'px';
          clampDivToWall(m.div);   // don't let a piece leave the wall
          updateHangInfo(m.div);
        });
        supMovers.forEach(function (m) {    // carried supports translate rigidly with the art
          m.div.style.left = (m.startL + dx) + 'px';
          m.div.style.top  = (m.startT + dy) + 'px';
        });
        boxMovers.forEach(function (bm) {   // move the group outline with the pieces
          bm.el.style.left = (bm.startL + dx) + 'px';
          bm.el.style.top  = (bm.startT + dy) + 'px';
        });
        renderSupportBoxes();               // keep the art+support box around the pair
        // Highlight the support a free piece is hovering over — a live cue that it
        // will attach on drop (before the confirmation box appears).
        stageEl.querySelectorAll('.support.drop-target').forEach(function (sd) { sd.classList.remove('drop-target'); });
        movers.forEach(function (m) {
          if (m.p && m.p.support == null) { var tgt = supportUnderArt(m.div); if (tgt) tgt.classList.add('drop-target'); }
        });
        // Live-sync to the 3D viewer while dragging (commit world coords first).
        if (roomChan) {
          movers.forEach(function (m) { if (m.p) syncWorldFromDiv(m.div, m.p); });
          supMovers.forEach(function (m) { if (m.s) syncSupportFromDiv(m.div, m.s); });
          broadcastPlacements();
        }
      }
      function onMove(ev) {
        dragMx = ev.clientX; dragMy = ev.clientY;
        if (!dragRaf) dragRaf = requestAnimationFrame(applyDrag);  // coalesce to 1 write/frame
      }
      function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        if (dragRaf) { cancelAnimationFrame(dragRaf); applyDrag(); }
        stageEl.querySelectorAll('.support.drop-target').forEach(function (sd) { sd.classList.remove('drop-target'); });
        if (movedAny) pushUndo(preDragSnap);
        supMovers.forEach(function (m) { if (m.s) syncSupportFromDiv(m.div, m.s); });
        movers.forEach(function (m) { if (m.p) syncWorldFromDiv(m.div, m.p); });
        if (movedAny) {
          // Free pieces may attach to a support they were dropped on; pieces already
          // on a carried support stay attached and just re-center on it.
          var free = movers.filter(function (m) { return !(m.p && m.p.support != null); });
          attachDroppedArt(free);
          movers.forEach(function (m) {
            if (m.p && m.p.support != null && supportMap[m.p.support]) snapArtToSupport(m.p, m.div);
          });
          renderSupportBoxes();
        }
        if (popoverArtId != null && placementMap[popoverArtId]) updatePopoverValues(placementMap[popoverArtId]);
        scheduleSave();
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  // Keep an artwork's rectangle inside the wall: its edges may reach but not
  // cross the wall boundary (e.g. the bottom stops at the floor). Oversized
  // pieces pin to the top-left corner.
  function clampDivToWall(div) {
    var dims = wallDims(currentWall);
    var maxL = dims[0] * baseScale - parseFloat(div.style.width);
    var maxT = dims[1] * baseScale - parseFloat(div.style.height);
    div.style.left = Math.max(0, Math.min(maxL, parseFloat(div.style.left))) + 'px';
    div.style.top  = Math.max(0, Math.min(maxT, parseFloat(div.style.top)))  + 'px';
  }

  // Single choke point for committing a piece's on-screen position to world
  // coords. Every move path (drag, nudge, popover, center/distribute, rotate)
  // goes through here, so refreshing the group outlines here means no operation
  // has to remember to do it.
  function syncWorldFromDiv(div, p) {
    clampDivToWall(div);
    var r  = artStagePx(p);
    var sx = parseFloat(div.style.left) + parseFloat(div.style.width) / 2;
    var sy = parseFloat(div.style.top)  + r.h / 2;
    var w  = stageToWorld(currentWall, sx, sy);
    p.x_in = w.x_in; p.y_in = w.y_in; p.z_in = w.z_in;
    updateHangInfo(div);
    scheduleGroupBoxes();
  }

  function syncDivFromWorld(div, p) {
    var r = artStagePx(p);
    var w = parseFloat(div.style.width);
    div.style.left = (worldToStage(currentWall, p).x - w / 2) + 'px';
    div.style.top  = r.top + 'px';
    clampDivToWall(div);
    syncWorldFromDiv(div, p);   // reflect any clamp back into world coords + hang info
  }

  // ══ Supports (pedestals / shelves) ═════════════════════════════════════════
  // Cuboid objects a piece can sit on. Drawn behind artworks; excluded from the
  // artwork align/center/distribute ops; an attached piece centers on the support
  // and moves with it. Supports added this session get temporary negative ids.
  function supportFootprint(s) {
    if (currentWall === 'floor' || currentWall === 'ceiling') {
      var swapped = (((s.rotation || 0) % 180) === 90);
      return swapped ? { w: s.d_in, h: s.w_in } : { w: s.w_in, h: s.d_in };
    }
    return { w: s.w_in, h: s.h_in };
  }
  function supportStagePx(s) {
    var sc = worldToStage(currentWall, s);
    var d  = supportFootprint(s);
    var w = d.w * baseScale, h = d.h * baseScale;
    return { left: sc.x - w / 2, top: sc.y - h / 2, w: w, h: h };
  }
  function attachedPlacements(sid) {
    return Object.values(placementMap).filter(function (p) { return p.support === sid; });
  }
  // A support is a "pedestal" on the floor/ceiling, a "shelf" on a vertical wall.
  function supportTerm(wall) { return (wall === 'floor' || wall === 'ceiling') ? 'Pedestal' : 'Shelf'; }
  function redrawSupports() {
    supports.forEach(function (s) { if (s.wall === currentWall) addSupportDiv(s); });
  }
  function addSupportDiv(s) {
    var r = supportStagePx(s);
    var div = document.createElement('div');
    div.className = 'support' + (s.id === selectedSupportId ? ' selected' : '');
    div.dataset.sid = s.id;
    div.style.left = r.left + 'px'; div.style.top = r.top + 'px';
    div.style.width = r.w + 'px'; div.style.height = r.h + 'px';
    div.style.backgroundImage = s.texture ? ('url("' + s.texture + '")') : '';
    div.innerHTML = '<span class="support-label">' + esc(s.label || supportTerm(s.wall)) + '</span>';
    makeSupportDraggable(div, s);
    div.addEventListener('click', function (e) { e.stopPropagation(); selectSupport(s); });
    stageEl.appendChild(div);
  }
  function syncSupportFromDiv(div, s) {
    clampDivToWall(div);
    var sx = parseFloat(div.style.left) + parseFloat(div.style.width) / 2;
    var sy = parseFloat(div.style.top)  + parseFloat(div.style.height) / 2;
    var w  = stageToWorld(currentWall, sx, sy);
    s.x_in = w.x_in; s.y_in = w.y_in; s.z_in = w.z_in;
  }
  function makeSupportDraggable(div, s) {
    if (READONLY) return;
    div.addEventListener('mousedown', function (e) {
      if (e.button !== 0 || spaceDown) return;
      e.preventDefault();
      selectSupport(s);
      var startL = parseFloat(div.style.left), startT = parseFloat(div.style.top);
      var carried = attachedPlacements(s.id).map(function (p) {
        var ad = stageEl.querySelector('.placed-art[data-id="' + p.artwork.id + '"]');
        return ad ? { div: ad, p: p, startL: parseFloat(ad.style.left), startT: parseFloat(ad.style.top) } : null;
      }).filter(Boolean);
      var smx = e.clientX, smy = e.clientY, mx = e.clientX, my = e.clientY, raf = 0, moved = false;
      function apply() {
        raf = 0;
        var dx = mx - smx, dy = my - smy;
        if (dx || dy) moved = true;
        div.style.left = (startL + dx) + 'px'; div.style.top = (startT + dy) + 'px';
        clampDivToWall(div);
        carried.forEach(function (c) { c.div.style.left = (c.startL + dx) + 'px'; c.div.style.top = (c.startT + dy) + 'px'; clampDivToWall(c.div); });
        renderSupportBoxes();               // keep the box around the support + its art
        if (roomChan) {                      // live-sync the moving support/art to 3D
          syncSupportFromDiv(div, s);
          carried.forEach(function (c) { syncWorldFromDiv(c.div, c.p); });
          broadcastPlacements();
        }
      }
      function onMove(ev) { mx = ev.clientX; my = ev.clientY; if (!raf) raf = requestAnimationFrame(apply); }
      function onUp() {
        document.removeEventListener('mousemove', onMove); document.removeEventListener('mouseup', onUp);
        if (raf) { cancelAnimationFrame(raf); apply(); }
        if (moved) {
          syncSupportFromDiv(div, s);
          carried.forEach(function (c) { syncWorldFromDiv(c.div, c.p); });
          renderSupportBoxes();
          scheduleSave();
        }
      }
      document.addEventListener('mousemove', onMove); document.addEventListener('mouseup', onUp);
    });
  }
  function selectSupport(s) {
    clearSelection();                       // drop any artwork/other-support selection
    selectedSupportId = s ? s.id : null;
    stageEl.querySelectorAll('.support').forEach(function (d) {
      d.classList.toggle('selected', !!s && String(d.dataset.sid) === String(s.id));
    });
    if (s) openSupportPanel(s);
  }
  function openSupportPanel(s) {
    if (READONLY || !supportPanel) return;
    supportPanel.classList.add('active');
    document.getElementById('sp-title').textContent = supportTerm(s.wall);
    spW.value = round1(s.w_in); spH.value = round1(s.h_in); spD.value = round1(s.d_in);
    spHoriz.value = round1(worldHoriz(currentWall, s));
    spVert.value  = round1(s.y_in);
  }
  function applySupportPanel() {
    var s = supportMap[selectedSupportId]; if (!s) return;
    s.w_in = Math.max(1, parseFloat(spW.value) || s.w_in);
    s.h_in = Math.max(1, parseFloat(spH.value) || s.h_in);
    s.d_in = Math.max(1, parseFloat(spD.value) || s.d_in);
    if (spHoriz.value !== '') applyHoriz(currentWall, s, parseFloat(spHoriz.value));
    if (spVert.value  !== '') s.y_in = Math.max(0, parseFloat(spVert.value));
    stageEl.querySelectorAll('.support').forEach(function (d) { d.remove(); });
    redrawSupports();
    attachedPlacements(s.id).forEach(function (p) {           // re-center art after a resize/move
      var ad = stageEl.querySelector('.placed-art[data-id="' + p.artwork.id + '"]');
      if (ad) snapArtToSupport(p, ad);
    });
    renderSupportBoxes();
    scheduleSave();
  }
  function addSupport(opts) {
    opts = opts || {};
    var onFloor = (currentWall === 'floor' || currentWall === 'ceiling');
    var dims = wallDims(currentWall);
    var center = stageToWorld(currentWall, dims[0] * baseScale / 2, dims[1] * baseScale / 2);
    var s = { id: nextSupportTmp--, wall: currentWall, label: opts.label || '',
              w_in: opts.w_in || (onFloor ? 16 : 36),   // pedestal-ish on floor, shelf-ish on a wall
              h_in: opts.h_in || (onFloor ? 40 : 2),
              d_in: opts.d_in || (onFloor ? 16 : 8),
              texture: opts.texture || null,
              rotation: 0, x_in: center.x_in, y_in: center.y_in, z_in: center.z_in };
    supports.push(s); supportMap[s.id] = s;
    addSupportDiv(s); renderSupportList(); selectSupport(s); scheduleSave();
  }

  // Site support catalog (definitions copied into the show when placed).
  var siteSupports = window.SITE_SUPPORTS || [];
  function renderSupportCatalog() {
    var el = document.getElementById('support-catalog');
    if (!el) return;
    el.innerHTML = '';
    siteSupports.forEach(function (cat) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'btn btn-sm btn-outline-secondary';
      b.style.cssText = 'display:block;width:100%;text-align:left;margin-bottom:4px;font-size:.72rem';
      b.textContent = '＋ ' + (cat.label || 'Support') +
        ' (' + cat.w_in + '×' + cat.h_in + '×' + cat.d_in + '")';
      b.title = 'Add a copy of this catalog support to the show';
      b.addEventListener('click', function () {
        addSupport({ w_in: cat.w_in, h_in: cat.h_in, d_in: cat.d_in, label: cat.label, texture: cat.texture });
      });
      el.appendChild(b);
    });
  }
  function saveSupportToCatalog() {
    var s = supportMap[selectedSupportId]; if (!s || !window.SUPPORT_CATALOG_URL) return;
    var name = window.prompt('Name for this catalog support:', s.label || supportTerm(s.wall));
    if (name === null) return;
    fetch(window.SUPPORT_CATALOG_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
      body: JSON.stringify({ label: name, w_in: s.w_in, h_in: s.h_in, d_in: s.d_in }),
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (data && data.ok && data.item) { siteSupports.push(data.item); renderSupportCatalog(); }
    }).catch(function () {});
  }
  function removeSupport(sid) {
    if (sid == null || !supportMap[sid]) return;
    attachedPlacements(sid).forEach(function (p) { p.support = null; });   // detach its art
    supports = supports.filter(function (x) { return String(x.id) !== String(sid); });
    delete supportMap[sid];
    var d = stageEl.querySelector('.support[data-sid="' + sid + '"]'); if (d) d.remove();
    selectedSupportId = null;
    if (supportPanel) supportPanel.classList.remove('active');
    renderSupportList(); renderSupportBoxes(); scheduleSave();
  }
  function renderSupportList() {
    if (!supportList) return;
    supportList.innerHTML = '';
    supports.forEach(function (s) {
      var row = document.createElement('div'); row.className = 'support-row';
      row.textContent = (s.label || supportTerm(s.wall)) + ' (' + s.wall + ')';
      row.addEventListener('click', function () {
        if (s.wall !== currentWall) {
          var tab = document.querySelector('.wall-tab[data-wall="' + s.wall + '"]');
          if (tab) tab.click();
        }
        selectSupport(s);
      });
      supportList.appendChild(row);
    });
  }
  // Coerce a support id (data attr) back to the type stored on placements.
  function normSid(v) { var n = parseInt(v, 10); return isNaN(n) ? v : n; }

  // Which support (if any) is the art div resting on? Its centre-x must be over
  // the support; vertically its footprint must sit within the support (floor /
  // ceiling) or its base must rest on/just above the top (a shelf on a wall).
  function supportUnderArt(ad) {
    var l0 = parseFloat(ad.style.left), t0 = parseFloat(ad.style.top);
    var w0 = parseFloat(ad.style.width), h0 = parseFloat(ad.style.height);
    var acx = l0 + w0 / 2, acy = t0 + h0 / 2, abot = t0 + h0;
    var onFloor = (currentWall === 'floor' || currentWall === 'ceiling');
    var best = null, bestD = Infinity;
    stageEl.querySelectorAll('.support').forEach(function (sd) {
      var l = parseFloat(sd.style.left), t = parseFloat(sd.style.top),
          w = parseFloat(sd.style.width), h = parseFloat(sd.style.height);
      var scx = l + w / 2, scy = t + h / 2;
      var hOverlap = (l0 < l + w) && (l0 + w0 > l);   // any horizontal overlap
      var match, d;
      if (onFloor) {
        // Footprint contains the piece's centre, or the two footprints overlap.
        var vOverlap = (t0 < t + h) && (t0 + h0 > t);
        match = (acx >= l && acx <= l + w && acy >= t && acy <= t + h) || (hOverlap && vOverlap);
        d = Math.hypot(acx - scx, acy - scy);
      } else {
        // A shelf is thin, so be generous: the piece just has to overlap it
        // horizontally and have its base resting on / near the shelf top. The
        // band is a full piece-height above the top down to below the shelf.
        var nearTop = (abot >= t - h0) && (abot <= t + h + h0 * 0.5);
        match = hOverlap && nearTop;
        d = Math.abs(abot - t);                       // closest base-to-shelf-top wins
      }
      if (match && d < bestD) { bestD = d; best = sd; }
    });
    return best;
  }

  // After moving a free piece, attach it to the support it's over (centre it, rest
  // it on top → auto-grouped) or detach it if released off every support. A piece
  // and its support then move together and show a box until it is detached.
  function attachDroppedArt(movers) {
    movers.forEach(function (m) {
      if (!m.p) return;
      var target = supportUnderArt(m.div);
      if (target) { m.p.support = normSid(target.dataset.sid); snapArtToSupport(m.p, m.div); }
      else { m.p.support = null; }
    });
    renderSupportBoxes();
  }
  function snapArtToSupport(p, div) {
    var s = supportMap[p.support]; if (!s) return;
    var sc = worldToStage(currentWall, s);
    var f  = supportFootprint(s);
    div.style.left = (sc.x - parseFloat(div.style.width) / 2) + 'px';    // center on the support
    if (currentWall === 'floor' || currentWall === 'ceiling') {
      div.style.top = (sc.y - parseFloat(div.style.height) / 2) + 'px';  // center footprints
      clampDivToWall(div); syncWorldFromDiv(div, p);
      p.y_in = s.y_in + s.h_in;   // rest the base on the support top (height matters in 3D)
    } else {
      var supTopY = sc.y - (f.h * baseScale) / 2;
      div.style.top = (supTopY - parseFloat(div.style.height)) + 'px';   // base rests on support top
      clampDivToWall(div); syncWorldFromDiv(div, p);
    }
  }

  // Draw a box around each support (on this wall) that holds art, enclosing the
  // support and the piece(s) on it — the visual confirmation that they are grouped
  // and move together until detached (ungrouped).
  function renderSupportBoxes() {
    stageEl.querySelectorAll('.support-box').forEach(function (el) { el.remove(); });
    supports.forEach(function (s) {
      if (s.wall !== currentWall) return;
      var arts = attachedPlacements(s.id);
      if (!arts.length) return;
      var sd = stageEl.querySelector('.support[data-sid="' + s.id + '"]');
      if (!sd) return;
      var minL = parseFloat(sd.style.left), minT = parseFloat(sd.style.top);
      var maxR = minL + parseFloat(sd.style.width), maxB = minT + parseFloat(sd.style.height);
      arts.forEach(function (p) {
        var ad = stageEl.querySelector('.placed-art[data-id="' + p.artwork.id + '"]');
        if (!ad) return;
        var l = parseFloat(ad.style.left), t = parseFloat(ad.style.top);
        minL = Math.min(minL, l); minT = Math.min(minT, t);
        maxR = Math.max(maxR, l + parseFloat(ad.style.width));
        maxB = Math.max(maxB, t + parseFloat(ad.style.height));
      });
      var pad = 5;
      var box = document.createElement('div');
      box.className = 'support-box';
      box.dataset.sid = s.id;
      box.style.left   = (minL - pad) + 'px';
      box.style.top    = (minT - pad) + 'px';
      box.style.width  = (maxR - minL + 2 * pad) + 'px';
      box.style.height = (maxB - minT + 2 * pad) + 'px';
      var tag = document.createElement('span');
      tag.className = 'support-box-tag';
      tag.textContent = 'grouped';
      box.appendChild(tag);
      stageEl.appendChild(box);
    });
    renderPlacards();   // placards are derived overlays too — refresh with the boxes
  }

  // ── Placards (5"×3" wall labels) ───────────────────────────────────────────
  // A physical label to the right of each wall-hung piece: 2" gap, bottoms
  // aligned. Non-interactive in 2D; it follows the piece and scales with zoom.
  var PLACARD_W_IN = 5, PLACARD_H_IN = 3, PLACARD_GAP_IN = 2;   // physical label size (inches)
  function placardHTML(a) {
    var out = '<div class="pl-name">' + esc(a.name || '') + '</div>';
    if (a.artists) out += '<div class="pl-artist">' + esc(a.artists) + '</div>';
    var meta = [];
    if (a.year)   meta.push(esc(String(a.year)));
    if (a.medium) meta.push(esc(a.medium));
    if (a.dims)   meta.push(esc(a.dims));
    if (meta.length) out += '<div class="pl-meta">' + meta.join(', ') + '</div>';
    if (a.price) out += '<div class="pl-price">' + esc(a.price) + (a.sold ? ' — sold' : '') + '</div>';
    return out;
  }
  function renderPlacards() {
    stageEl.querySelectorAll('.placard-card').forEach(function (el) { el.remove(); });
    var onFloorView = (currentWall === 'floor' || currentWall === 'ceiling');
    Object.values(placementMap).forEach(function (p) {
      if (p.wall !== currentWall) return;
      var ad = stageEl.querySelector('.placed-art[data-id="' + p.artwork.id + '"]');
      if (!ad) return;
      // Reference rect: on the floor/ceiling a piece on a pedestal aligns to the
      // pedestal footprint; otherwise (or on a wall) it aligns to the piece.
      var ref = ad;
      if (onFloorView && p.support != null) {
        var sd = stageEl.querySelector('.support[data-sid="' + p.support + '"]');
        if (sd) ref = sd;
      }
      var rL = parseFloat(ref.style.left), rT = parseFloat(ref.style.top),
          rW = parseFloat(ref.style.width), rH = parseFloat(ref.style.height);
      // Size the placard from the piece's OWN on-screen scale (px per inch), so a
      // 5x3" label always renders at 5x3" relative to the pieces — robust to any
      // baseScale drift between when pieces and placards were last laid out.
      var inchW = footprintDims(p).w;
      var artPx = parseFloat(ad.style.width);
      var pxPerIn = (artPx > 0 && inchW > 0) ? (artPx / inchW) : baseScale;
      var pw = PLACARD_W_IN * pxPerIn, ph = PLACARD_H_IN * pxPerIn, gap = PLACARD_GAP_IN * pxPerIn;
      var card = document.createElement('div');
      card.className = 'placard-card';
      card.dataset.id = p.artwork.id;
      card.style.left   = (rL + rW + gap) + 'px';    // 2" to the right
      card.style.top    = (rT + rH - ph) + 'px';     // bottoms aligned
      card.style.width  = pw + 'px';
      card.style.height = ph + 'px';
      card.style.fontSize = Math.max(3, ph * 0.13) + 'px';
      card.innerHTML = placardHTML(p.artwork);
      stageEl.appendChild(card);
    });
  }

  // ── Drop from sidebar ─────────────────────────────────────────────────────
  canvasWrap.addEventListener('dragover', function (e) { e.preventDefault(); });
  canvasWrap.addEventListener('drop', function (e) {
    e.preventDefault();
    if (READONLY) return;                     // read-only 2D viewer: no drop-to-place
    var id  = parseInt(e.dataTransfer.getData('text/plain'), 10);
    if (!id) return;
    var art = findArtwork(id);
    if (!art) return;
    pushUndo();
    var wrapRect = canvasWrap.getBoundingClientRect();
    var sp = canvasToStage(e.clientX - wrapRect.left, e.clientY - wrapRect.top);
    var w  = stageToWorld(currentWall, sp.x, sp.y);

    if (placementMap[id]) {
      placements = placements.filter(function (p) { return p.artwork.id !== id; });
      var old = stageEl.querySelector('.placed-art[data-id="' + id + '"]');
      if (old) old.remove();
    }
    var p = { artwork: art, wall: currentWall, x_in: w.x_in, y_in: w.y_in, z_in: w.z_in, rotation: 0, group: null };
    placementMap[id] = p;
    placements.push(p);
    addPlacedDiv(p);
    renderSidebar();
    renderGroupBoxes();
    // Dropping straight onto a support attaches (auto-groups) the piece too.
    var newDiv = stageEl.querySelector('.placed-art[data-id="' + id + '"]');
    if (newDiv) attachDroppedArt([{ p: p, div: newDiv }]);
    // Select just the freshly-placed piece so it can be adjusted right away.
    clearSelection();
    if (newDiv) {
      newDiv.classList.add('selected');
      selectionOrder = [String(id)];
      openPopover(p);
    }
    scheduleSave();
  });

  function findArtwork(id) {
    for (var i = 0; i < allArtworks.length; i++) if (allArtworks[i].id === id) return allArtworks[i];
    return null;
  }

  // ── Pan & Zoom ────────────────────────────────────────────────────────────
  var MIN_ZOOM = 0.15, MAX_ZOOM = 8;

  // Mouse-wheel zoom (zoom toward cursor) — re-lays out at real pixels
  canvasWrap.addEventListener('wheel', function (e) {
    e.preventDefault();
    var factor   = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    var newScale = Math.max(fitScale * MIN_ZOOM, Math.min(fitScale * MAX_ZOOM, baseScale * factor));
    var ratio    = newScale / baseScale;
    if (ratio === 1) return;
    var wrapRect = canvasWrap.getBoundingClientRect();
    var mx = e.clientX - wrapRect.left;
    var my = e.clientY - wrapRect.top;
    // Keep point under cursor fixed (compute pan against the pre-rescale offset)
    panX = mx - stageLeft - (mx - (stageLeft + panX)) * ratio;
    panY = my - stageTop  - (my - (stageTop  + panY)) * ratio;
    rescaleStage(ratio, newScale);
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

  // ── Touch pan / pinch-zoom for the read-only 2D viewer ────────────────────
  // The editor is hidden on touch; the 2D viewer needs finger navigation. Pan
  // with one finger, pinch to zoom (toward the pinch centre), double-tap to fit.
  if (READONLY) {
    var tPan = null, tPinch = null, tMoved = false, lastTapT = 0;
    function tdist(t) { return Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY); }

    // Stop iOS Safari's native pinch page-zoom over the canvas.
    canvasWrap.addEventListener('gesturestart', function (e) { e.preventDefault(); }, { passive: false });

    canvasWrap.addEventListener('touchstart', function (e) {
      if (e.touches.length === 1) {
        tPinch = null; tMoved = false;
        tPan = { x: e.touches[0].clientX, y: e.touches[0].clientY, px: panX, py: panY };
      } else if (e.touches.length === 2) {
        tPan = null;
        tPinch = {
          d: tdist(e.touches), scale: baseScale,
          mx: (e.touches[0].clientX + e.touches[1].clientX) / 2,
          my: (e.touches[0].clientY + e.touches[1].clientY) / 2,
        };
      }
    }, { passive: true });

    canvasWrap.addEventListener('touchmove', function (e) {
      if (tPan && e.touches.length === 1) {
        var dx = e.touches[0].clientX - tPan.x, dy = e.touches[0].clientY - tPan.y;
        if (Math.abs(dx) + Math.abs(dy) > 4) tMoved = true;
        e.preventDefault();
        panX = tPan.px + dx; panY = tPan.py + dy;
        applyTransform();
      } else if (tPinch && e.touches.length === 2) {
        e.preventDefault();
        tMoved = true;
        var d = tdist(e.touches);
        var newScale = Math.max(fitScale * MIN_ZOOM, Math.min(fitScale * MAX_ZOOM, tPinch.scale * (d / tPinch.d)));
        var ratio = newScale / baseScale;
        if (ratio !== 1) {
          var r = canvasWrap.getBoundingClientRect();
          var mx = tPinch.mx - r.left, my = tPinch.my - r.top;
          panX = mx - stageLeft - (mx - (stageLeft + panX)) * ratio;
          panY = my - stageTop  - (my - (stageTop  + panY)) * ratio;
          rescaleStage(ratio, newScale);
          applyTransform();
        }
      }
    }, { passive: false });

    canvasWrap.addEventListener('touchend', function (e) {
      if (e.touches.length === 0) {
        if (!tMoved) {                         // a tap → double-tap resets to fit
          var now = Date.now();
          if (now - lastTapT < 300) { renderWall(); lastTapT = 0; } else lastTapT = now;
        }
        tPan = null; tPinch = null;
      } else if (e.touches.length === 1) {     // lifted one finger of a pinch
        tPinch = null;
        tPan = { x: e.touches[0].clientX, y: e.touches[0].clientY, px: panX, py: panY };
      }
    });
  }

  // ── Marquee (rubber-band) multi-select ────────────────────────────────────
  // A plain left-drag over empty canvas draws a rectangle and selects every
  // artwork it touches. (Panning uses space-drag / middle-mouse, so there's no
  // conflict.)  Shift adds to the current selection instead of replacing it.
  var marqueeEl = null, marqueeStartX = 0, marqueeStartY = 0, marqueeShift = false, marqueeActive = false;
  var marqueeEndT = 0;   // timestamp of last completed marquee (suppresses the trailing click-to-deselect)

  function onMarqueeMove(e) {
    if (!marqueeActive) return;
    var dx = e.clientX - marqueeStartX, dy = e.clientY - marqueeStartY;
    if (!marqueeEl) {
      if (Math.abs(dx) < 4 && Math.abs(dy) < 4) return;   // still a click, not a drag
      marqueeEl = document.createElement('div');
      marqueeEl.className = 'marquee-box';
      canvasWrap.appendChild(marqueeEl);
    }
    var wrapRect = canvasWrap.getBoundingClientRect();
    marqueeEl.style.left   = (Math.min(e.clientX, marqueeStartX) - wrapRect.left) + 'px';
    marqueeEl.style.top    = (Math.min(e.clientY, marqueeStartY) - wrapRect.top)  + 'px';
    marqueeEl.style.width  = Math.abs(dx) + 'px';
    marqueeEl.style.height = Math.abs(dy) + 'px';
  }

  function onMarqueeUp() {
    document.removeEventListener('mousemove', onMarqueeMove);
    document.removeEventListener('mouseup', onMarqueeUp);
    marqueeActive = false;
    if (!marqueeEl) {                          // no drag → background click deselects
      if (!marqueeShift) clearSelection();
      return;
    }
    var box = marqueeEl.getBoundingClientRect();
    marqueeEl.remove();
    marqueeEl = null;
    if (!marqueeShift) clearSelection();
    stageEl.querySelectorAll('.placed-art:not(.obstacle):not(.corner)').forEach(function (div) {
      var r = div.getBoundingClientRect();
      var hit = !(r.right < box.left || r.left > box.right || r.bottom < box.top || r.top > box.bottom);
      if (!hit || div.classList.contains('selected')) return;
      div.classList.add('selected');
      var id = div.dataset.id;
      if (selectionOrder.indexOf(id) === -1) selectionOrder.push(id);
    });
    expandSelectionToGroups();   // a marquee touching one group member selects the whole group
    // Supports inside the marquee are selected too (so you can rectangle around a
    // piece and its shelf and move both). Also pull in supports under selected art.
    var supHit = [];
    stageEl.querySelectorAll('.support').forEach(function (sd) {
      var r = sd.getBoundingClientRect();
      var hit = !(r.right < box.left || r.left > box.right || r.bottom < box.top || r.top > box.bottom);
      if (hit) { sd.classList.add('selected'); supHit.push(normSid(sd.dataset.sid)); }
    });
    selectionOrder.forEach(function (id) {
      var p = placementMap[id];
      if (p && p.support != null) {
        var sd = stageEl.querySelector('.support[data-sid="' + p.support + '"]');
        if (sd && !sd.classList.contains('selected')) { sd.classList.add('selected'); supHit.push(p.support); }
      }
    });
    // One artwork → show its position bar; one bare support → its panel; else hidden.
    if (selectionOrder.length === 1 && placementMap[selectionOrder[0]]) {
      openPopover(placementMap[selectionOrder[0]]);
    } else if (selectionOrder.length === 0 && supHit.length === 1) {
      selectedSupportId = supHit[0];
      if (supportMap[selectedSupportId]) openSupportPanel(supportMap[selectedSupportId]);
    } else {
      closePopover();
    }
    if (measureDisplay) measureDisplay.textContent = '';
    marqueeEndT = performance.now();   // the browser fires a click right after; ignore it
  }

  canvasWrap.addEventListener('mousedown', function (e) {
    if (e.button !== 0 || spaceDown) return;         // left button only; space = pan
    if (e.target.closest('.placed-art')) return;     // artwork/obstacle handles its own click
    marqueeActive = true;
    marqueeShift  = e.shiftKey;
    marqueeStartX = e.clientX;
    marqueeStartY = e.clientY;
    marqueeEl     = null;                             // created lazily once the pointer moves
    document.addEventListener('mousemove', onMarqueeMove);
    document.addEventListener('mouseup', onMarqueeUp);
  });

  // Double-click to reset view (refit to wall + recenter)
  canvasWrap.addEventListener('dblclick', function (e) {
    if (e.target === canvasWrap || e.target === stageEl) {
      renderWall();   // re-inits stage at fit scale, pan 0, and re-lays out
    }
  });

  // ── Position popover ──────────────────────────────────────────────────────
  // Populate + reveal the inline position bar for a single selected artwork.
  function openPopover(p) {
    popoverArtId = p.artwork.id;
    posTitle.textContent = p.artwork.name;
    var isCF = (currentWall === 'ceiling' || currentWall === 'floor');
    posHorizLabel.textContent = isCF ? 'East from center (in)' : 'Horiz from center (in, + = right)';
    posDepthLabel.style.display = isCF ? 'inline' : 'none';
    posDepth.style.display      = isCF ? 'inline' : 'none';
    updatePopoverValues(p);
    posPanel.classList.add('active');
  }

  function updatePopoverValues(p) {
    posHoriz.value = round1(worldHoriz(currentWall, p));
    posVert.value  = round1(p.y_in);
    if (currentWall === 'ceiling' || currentWall === 'floor') posDepth.value = round1(p.z_in);
  }

  function round1(v) { return Math.round(v * 10) / 10; }

  function closePopover() {
    posPanel.classList.remove('active');
    popoverArtId = null;
  }

  // Toggle a floor/ceiling piece between 0° and 90° yaw, re-rendering just that
  // piece so the current view/zoom is preserved.
  // Rotate the selected floor/ceiling pieces 90°. Operates per UNIT: each group
  // rotates as a rigid body about its group centre; each ungrouped piece rotates
  // about its own centre. Reversible: rotating again returns to 0°.
  // The one Rotate command: turns whatever is selected 90° about the vertical
  // (height) axis — loose pieces, a support, or an art+support group. A support
  // rotates its own footprint (floor/ceiling) and everything on it turns with it.
  function rotateSelection() {
    if (READONLY) return;                     // read-only 2D viewer: no rotation
    var selArt = getSelected()
      .filter(function (d) { return !d.classList.contains('obstacle') && !d.classList.contains('corner'); })
      .map(function (d) { return placementMap[parseInt(d.dataset.id, 10)]; })
      .filter(Boolean);

    // Supports to turn: an explicitly selected support + those under selected art.
    var supIds = {};
    if (selectedSupportId != null && supportMap[selectedSupportId]) supIds[selectedSupportId] = true;
    selArt.forEach(function (p) { if (p.support != null) supIds[p.support] = true; });
    var supList = Object.keys(supIds).map(function (k) { return supportMap[normSid(k)]; }).filter(Boolean);

    // Pieces to turn = selected art ∪ every piece on an involved support (deduped).
    var seen = {}, pieces = [];
    function addPiece(p) { if (p && !seen[p.artwork.id]) { seen[p.artwork.id] = true; pieces.push(p); } }
    selArt.forEach(addPiece);
    supList.forEach(function (s) { attachedPlacements(s.id).forEach(addPiece); });

    if (!pieces.length && !supList.length) return;
    pushUndo();

    // 1. Turn each support's own footprint (only meaningful on the floor/ceiling).
    supList.forEach(function (s) {
      if (s.wall === 'floor' || s.wall === 'ceiling') s.rotation = ((s.rotation || 0) + 90) % 360;
    });

    // 2. Turn the pieces. Floor/ceiling pieces rotate about their group centre
    //    (a piece on a support is a single unit → rotates in place); wall pieces
    //    on a shelf just cycle rotation (yaw for the 3D object).
    var floorPieces = pieces.filter(function (p) { return p.wall === 'floor' || p.wall === 'ceiling'; });
    var wallShelfPieces = pieces.filter(function (p) { return p.wall !== 'floor' && p.wall !== 'ceiling' && p.support != null; });

    var byGroup = {}, units = [];
    floorPieces.forEach(function (p) {
      if (p.group == null) { units.push([p]); return; }
      var k = 'g' + p.group;
      if (!byGroup[k]) { byGroup[k] = []; units.push(byGroup[k]); }
      byGroup[k].push(p);
    });
    function extents(p) {   // world footprint extents (x = east, z = north/south)
      var depth = (p.artwork.d_in && p.artwork.d_in > 0) ? p.artwork.d_in : p.artwork.h_in;
      var swapped = (((p.rotation || 0) % 180) === 90);
      return { ex: (swapped ? depth : p.artwork.w_in), ez: (swapped ? p.artwork.w_in : depth) };
    }
    units.forEach(function (unit) {
      var minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
      unit.forEach(function (p) {
        var e = extents(p);
        minX = Math.min(minX, p.x_in - e.ex / 2); maxX = Math.max(maxX, p.x_in + e.ex / 2);
        minZ = Math.min(minZ, p.z_in - e.ez / 2); maxZ = Math.max(maxZ, p.z_in + e.ez / 2);
      });
      var Cx = (minX + maxX) / 2, Cz = (minZ + maxZ) / 2;
      unit.forEach(function (p) {
        var dx = p.x_in - Cx, dz = p.z_in - Cz;
        p.x_in = Cx - dz; p.z_in = Cz + dx;                 // +90°
        p.rotation = (((p.rotation || 0) + 90) % 360);
      });
    });
    wallShelfPieces.forEach(function (p) { p.rotation = ((p.rotation || 0) + 90) % 360; });

    redrawPieces();   // re-render all pieces at the current view (one clean pass)
    // Attached pieces re-centre on their (possibly turned) support; loose floor
    // pieces just get clamped to the wall and persisted.
    supList.forEach(function (s) {
      attachedPlacements(s.id).forEach(function (p) {
        var ad = stageEl.querySelector('.placed-art[data-id="' + p.artwork.id + '"]');
        if (ad) snapArtToSupport(p, ad);
      });
    });
    floorPieces.forEach(function (p) {
      if (p.support != null) return;
      var div = stageEl.querySelector('.placed-art[data-id="' + String(p.artwork.id) + '"]');
      if (div) { clampDivToWall(div); syncWorldFromDiv(div, p); }
    });

    selectionOrder = selArt.map(function (p) { return String(p.artwork.id); });
    selectionOrder.forEach(function (id) {
      var div = stageEl.querySelector('.placed-art[data-id="' + id + '"]');
      if (div) div.classList.add('selected');
    });
    if (selectionOrder.length === 1) openPopover(selArt[0]); else closePopover();
    renderGroupBoxes();
    renderSupportBoxes();
    if (roomChan) broadcastPlacements();
    scheduleSave();
  }
  document.getElementById('btn-rotate').addEventListener('click', rotateSelection);

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
      if (performance.now() - marqueeEndT < 300) return;   // don't clear a fresh marquee selection
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

  // ── Grouping ────────────────────────────────────────────────────────────
  var GROUP_COLORS = ['#c0392b', '#2277cc', '#8e44ad', '#e67e22', '#16a085', '#2c3e50', '#d81b60'];
  function groupColor(gid) {
    var n = GROUP_COLORS.length;
    return GROUP_COLORS[((gid % n) + n) % n];
  }
  function nextGroupId() {
    var max = 0;
    Object.values(placementMap).forEach(function (p) { if (p.group != null && p.group > max) max = p.group; });
    return max + 1;
  }
  function groupMemberIds(gid) {
    var ids = [];
    Object.values(placementMap).forEach(function (p) {
      if (p.group != null && p.group === gid) ids.push(String(p.artwork.id));
    });
    return ids;
  }
  function expandSelectionToGroups() {
    var extra = [];
    selectionOrder.forEach(function (id) {
      var p = placementMap[id];
      if (p && p.group != null) {
        groupMemberIds(p.group).forEach(function (mid) {
          if (selectionOrder.indexOf(mid) === -1 && extra.indexOf(mid) === -1) extra.push(mid);
        });
      }
    });
    extra.forEach(function (mid) {
      selectionOrder.push(mid);
      var div = stageEl.querySelector('.placed-art[data-id="' + mid + '"]');
      if (div) div.classList.add('selected');
    });
  }
  // Coalesce repeated position commits (e.g. distributing many pieces) into a
  // single overlay refresh on the next frame — group outlines, support boxes, and
  // placards all follow the moved pieces (placards derive from piece positions, so
  // they trail after center/distribute without being part of those ops).
  var groupBoxRaf = 0;
  function scheduleGroupBoxes() {
    if (groupBoxRaf) return;
    groupBoxRaf = requestAnimationFrame(function () {
      groupBoxRaf = 0;
      renderGroupBoxes();
      renderSupportBoxes();   // also refreshes placards (called at its end)
    });
  }
  // Draw a dashed colored box around each group's members on the current wall.
  function renderGroupBoxes() {
    stageEl.querySelectorAll('.group-box').forEach(function (el) { el.remove(); });
    var groups = {};
    Object.values(placementMap).forEach(function (p) {
      if (p.wall !== currentWall || p.group == null) return;
      (groups[p.group] = groups[p.group] || []).push(p);
    });
    Object.keys(groups).forEach(function (gidKey) {
      var gid = parseInt(gidKey, 10);
      var minL = Infinity, minT = Infinity, maxR = -Infinity, maxB = -Infinity, found = false;
      groups[gid].forEach(function (p) {
        var div = stageEl.querySelector('.placed-art[data-id="' + p.artwork.id + '"]');
        if (!div) return;
        found = true;
        var l = parseFloat(div.style.left), t = parseFloat(div.style.top);
        minL = Math.min(minL, l); minT = Math.min(minT, t);
        maxR = Math.max(maxR, l + parseFloat(div.style.width));
        maxB = Math.max(maxB, t + parseFloat(div.style.height));
      });
      if (!found) return;
      var pad = 6;
      var box = document.createElement('div');
      box.className = 'group-box';
      box.dataset.gid = gid;
      box.style.left        = (minL - pad) + 'px';
      box.style.top         = (minT - pad) + 'px';
      box.style.width       = (maxR - minL + 2 * pad) + 'px';
      box.style.height      = (maxB - minT + 2 * pad) + 'px';
      box.style.borderColor = groupColor(gid);
      stageEl.appendChild(box);
    });
  }
  function groupSelected() {
    var sel = getSelected().filter(function (d) {
      return !d.classList.contains('obstacle') && !d.classList.contains('corner');
    });
    if (sel.length < 2) return;
    pushUndo();
    var gid = nextGroupId();
    sel.forEach(function (d) { var p = placementMap[parseInt(d.dataset.id, 10)]; if (p) p.group = gid; });
    renderGroupBoxes();
    scheduleSave();
  }
  function ungroupSelected() {
    var sel = getSelected()
      .map(function (d) { return placementMap[parseInt(d.dataset.id, 10)]; })
      .filter(Boolean);
    var grouped = sel.filter(function (p) { return p.group != null; });
    var attached = sel.filter(function (p) { return p.support != null; });
    // Ungrouping a selected support detaches every piece on it.
    if (selectedSupportId != null) {
      attachedPlacements(selectedSupportId).forEach(function (p) {
        if (attached.indexOf(p) === -1) attached.push(p);
      });
    }
    if (!grouped.length && !attached.length) return;
    pushUndo();
    grouped.forEach(function (p) { p.group = null; });        // artwork↔artwork group
    attached.forEach(function (p) { p.support = null; });     // artwork↔support group
    renderGroupBoxes();
    renderSupportBoxes();
    scheduleSave();
  }
  document.getElementById('btn-group').addEventListener('click', groupSelected);
  document.getElementById('btn-ungroup').addEventListener('click', ungroupSelected);

  // ── Undo ──────────────────────────────────────────────────────────────────
  // Snapshot placement positions before each edit so a mistake (e.g. distribute
  // in the wrong direction) can be reverted to the previous layout.
  var undoStack = [];
  var lastNudgeUndoT = 0;
  var undoBtn = document.getElementById('btn-undo');
  function snapshotPlacements() {
    return placements.map(function (p) {
      return { id: p.artwork.id, wall: p.wall, x_in: p.x_in, y_in: p.y_in, z_in: p.z_in, rotation: p.rotation || 0, group: (p.group == null ? null : p.group) };
    });
  }
  function pushUndo(snap) {
    undoStack.push(snap || snapshotPlacements());
    if (undoStack.length > 50) undoStack.shift();
    if (undoBtn) undoBtn.disabled = false;
  }
  function undo() {
    if (!undoStack.length) return;
    var snap = undoStack.pop();
    var newPlacements = [], newMap = {};
    snap.forEach(function (s) {
      var art = findArtwork(s.id);
      if (!art) return;
      var p = { artwork: art, wall: s.wall, x_in: s.x_in, y_in: s.y_in, z_in: s.z_in, rotation: s.rotation || 0, group: (s.group == null ? null : s.group) };
      newPlacements.push(p);
      newMap[s.id] = p;
    });
    placements = newPlacements;
    placementMap = newMap;
    selectionOrder = [];
    renderWall();
    renderSidebar();
    scheduleSave();
    if (undoBtn) undoBtn.disabled = undoStack.length === 0;
  }
  if (undoBtn) undoBtn.addEventListener('click', undo);

  // ── Group-aware Center / Distribute ───────────────────────────────────────
  // These operate on UNITS: each group is one unit (its members move together);
  // each ungrouped piece is its own unit. That is what makes a group behave as a
  // single block for alignment/distribution rather than N loose pieces.
  function unitBBox(members) {
    var minL = Infinity, minT = Infinity, maxR = -Infinity, maxB = -Infinity;
    members.forEach(function (div) {
      var l = parseFloat(div.style.left), t = parseFloat(div.style.top);
      minL = Math.min(minL, l); minT = Math.min(minT, t);
      maxR = Math.max(maxR, l + parseFloat(div.style.width));
      maxB = Math.max(maxB, t + parseFloat(div.style.height));
    });
    return { left: minL, top: minT, right: maxR, bottom: maxB, w: maxR - minL, h: maxB - minT };
  }
  function selectionUnits() {
    var byGroup = {}, units = [];
    getSelected().forEach(function (div) {
      if (div.classList.contains('obstacle') || div.classList.contains('corner')) return;
      var p = placementMap[parseInt(div.dataset.id, 10)];
      var gid = (p && p.group != null) ? p.group : null;
      if (gid == null) {
        units.push({ members: [div] });
      } else {
        var k = 'g' + gid;
        if (!byGroup[k]) { byGroup[k] = { members: [] }; units.push(byGroup[k]); }
        byGroup[k].members.push(div);
      }
    });
    units.forEach(function (u) { u.bbox = unitBBox(u.members); });
    return units;
  }
  function moveUnit(u, dx, dy) {
    if (!dx && !dy) return;
    u.members.forEach(function (div) {
      div.style.left = (parseFloat(div.style.left) + dx) + 'px';
      div.style.top  = (parseFloat(div.style.top)  + dy) + 'px';
      clampDivToWall(div);
      var p = placementMap[parseInt(div.dataset.id, 10)];
      if (p) syncWorldFromDiv(div, p);
    });
    scheduleGroupBoxes();   // group outlines, support boxes, and placards follow
  }

  // Center H: align each unit's vertical centre to the first unit's.
  document.getElementById('btn-align-h').addEventListener('click', function () {
    var units = selectionUnits();
    if (units.length < 2) return;
    pushUndo();
    var refCy = units[0].bbox.top + units[0].bbox.h / 2;
    units.forEach(function (u) { moveUnit(u, 0, refCy - (u.bbox.top + u.bbox.h / 2)); });
    scheduleSave();
  });

  // Center V: align each unit's horizontal centre to the first unit's.
  document.getElementById('btn-align-v').addEventListener('click', function () {
    var units = selectionUnits();
    if (units.length < 2) return;
    pushUndo();
    var refCx = units[0].bbox.left + units[0].bbox.w / 2;
    units.forEach(function (u) { moveUnit(u, refCx - (u.bbox.left + u.bbox.w / 2), 0); });
    scheduleSave();
  });

  // Dist H: first & last units are anchors; space the middle units so every
  // adjacent edge-to-edge gap between unit bounding boxes is equal.
  document.getElementById('btn-dist-h').addEventListener('click', function () {
    var units = selectionUnits();
    if (units.length < 3) return;
    pushUndo();
    var sorted = units.slice().sort(function (a, b) { return a.bbox.left - b.bbox.left; });
    var E0 = sorted[0].bbox.right;                     // right edge of first anchor
    var R  = sorted[sorted.length - 1].bbox.left;      // left edge of last anchor
    var middle = sorted.slice(1, sorted.length - 1);
    var totalW = middle.reduce(function (s, u) { return s + u.bbox.w; }, 0);
    var gap = (R - E0 - totalW) / (sorted.length - 1);
    var x = E0;
    middle.forEach(function (u) {
      var targetLeft = x + gap;
      moveUnit(u, targetLeft - u.bbox.left, 0);
      x = targetLeft + u.bbox.w;
    });
    scheduleSave();
  });

  // Dist V: same as Dist H but vertical.
  document.getElementById('btn-dist-v').addEventListener('click', function () {
    var units = selectionUnits();
    if (units.length < 3) return;
    pushUndo();
    var sorted = units.slice().sort(function (a, b) { return a.bbox.top - b.bbox.top; });
    var E0 = sorted[0].bbox.bottom;                    // bottom edge of first anchor
    var B  = sorted[sorted.length - 1].bbox.top;       // top edge of last anchor
    var middle = sorted.slice(1, sorted.length - 1);
    var totalH = middle.reduce(function (s, u) { return s + u.bbox.h; }, 0);
    var gap = (B - E0 - totalH) / (sorted.length - 1);
    var y = E0;
    middle.forEach(function (u) {
      var targetTop = y + gap;
      moveUnit(u, 0, targetTop - u.bbox.top);
      y = targetTop + u.bbox.h;
    });
    scheduleSave();
  });

  function removeSelected() {
    if (READONLY) return;                     // read-only 2D viewer: no deletion
    // A support is selected on its own (selection is mutually exclusive with
    // artworks), so the shared Remove button / Delete key can drop it too.
    if (selectedSupportId != null) { removeSupport(selectedSupportId); return; }
    var snap = snapshotPlacements();
    var removed = 0;
    getSelected().forEach(function (div) {
      if (div.classList.contains('obstacle') || div.classList.contains('corner')) return;
      var id = parseInt(div.dataset.id, 10);
      delete placementMap[id];
      placements = placements.filter(function (p) { return p.artwork.id !== id; });
      div.remove();
      removed++;
    });
    if (!removed) return;
    pushUndo(snap);
    selectionOrder = [];
    closePopover();
    renderSidebar();
    renderGroupBoxes();
    scheduleSave();
  }

  document.getElementById('btn-remove').addEventListener('click', removeSelected);

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

    if ((e.metaKey || e.ctrlKey) && e.code === 'KeyZ') { e.preventDefault(); undo(); return; }
    if (e.code === 'KeyM') { doMeasure(); return; }
    if (e.code === 'Delete' || e.code === 'Backspace') { e.preventDefault(); removeSelected(); return; }
    var isPan   = (e.code === 'KeyW' || e.code === 'KeyA' || e.code === 'KeyS' || e.code === 'KeyD');
    var isArrow = (e.code === 'ArrowUp' || e.code === 'ArrowDown' || e.code === 'ArrowLeft' || e.code === 'ArrowRight');
    if (!isPan && !isArrow) return;
    e.preventDefault();

    var selected = getSelected();

    // Arrows with artwork(s) selected → nudge in wall-local coordinates (skip obstacles/corners)
    var artworkSelected = selected.filter(function (d) {
      return !d.classList.contains('obstacle') && !d.classList.contains('corner');
    });
    if (isArrow && artworkSelected.length > 0 && !READONLY) {   // no nudging in read-only; arrows pan instead
      // Coalesce a burst of nudges into a single undo step.
      var nowT = performance.now();
      if (nowT - lastNudgeUndoT > 600) pushUndo();
      lastNudgeUndoT = nowT;
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
        supports: supports.map(function (s) {
          return { key: s.id, wall: s.wall, label: s.label || '',
                   x_in: s.x_in, y_in: s.y_in, z_in: s.z_in,
                   w_in: s.w_in, h_in: s.h_in, d_in: s.d_in, rotation: s.rotation || 0,
                   texture: s.texture || null };
        }),
        placements: placements.map(function (p) {
          return { artwork_id: p.artwork.id, wall: p.wall, x_in: p.x_in, y_in: p.y_in, z_in: p.z_in, rotation: p.rotation || 0, group: (p.group == null ? null : p.group), support: (p.support == null ? null : p.support) };
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
    broadcastPlacements();   // push the change to any open 3D viewer immediately
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

  // ── Supports: wire the sidebar buttons + size panel ───────────────────────
  if (!READONLY) {
    var addSupBtn = document.getElementById('add-support-btn');
    if (addSupBtn) addSupBtn.addEventListener('click', function () { addSupport(); });
    [spW, spH, spD, spHoriz, spVert].forEach(function (inp) {
      if (inp) inp.addEventListener('input', function () { applySupportPanel(); });
    });
    // Removal is the toolbar Remove button / Delete key (unified for pieces + supports).
    var spSaveCat = document.getElementById('sp-save-catalog');
    if (spSaveCat) spSaveCat.addEventListener('click', saveSupportToCatalog);
    // Rotation is the toolbar Rotate button (unified for pieces + supports).
  }

  // ── Init ─────────────────────────────────────────────────────────────────
  renderSidebar();
  renderSupportList();
  renderSupportCatalog();
  renderWall();
  applyHangInfoState();

}());
