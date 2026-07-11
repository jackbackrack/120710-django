/*
 * Room Layout Editor
 * Coordinate conventions (stored in PLACEMENTS / sent to server):
 *   x_in  east from room centre  (positive = east)
 *   y_in  height from floor      (positive = up)
 *   z_in  south from room centre (positive = south)
 *
 * The canvas represents one wall at a time in screen coordinates.
 * For N/S walls: screen-x maps to room-x (east), screen-y maps to room-y (height).
 * For E/W walls: screen-x maps to room-z (south←→north), screen-y maps to room-y.
 * Ceiling/floor: screen-x → room-x, screen-y → room-z.
 */
(function () {
  'use strict';

  // ── State ────────────────────────────────────────────────────────────────
  var cfg         = window.ROOM_CONFIG;    // { width_in, depth_in, height_in }
  var placements  = window.PLACEMENTS;     // [{ artwork, wall, x_in, y_in, z_in }]
  var pool        = window.POOL_ARTWORKS;  // [artwork …]
  var saveUrl     = window.SAVE_URL;
  var csrfToken   = window.CSRF_TOKEN;

  var WALLS = ['N','E','S','W','ceiling','floor'];
  var currentWall = 'N';

  // Map: artwork.id → placement data { artwork, wall, x_in, y_in, z_in }
  var placementMap = {};
  placements.forEach(function (p) { placementMap[p.artwork.id] = p; });

  // ── DOM refs ─────────────────────────────────────────────────────────────
  var canvasWrap   = document.getElementById('canvas-wrap');
  var poolList     = document.getElementById('pool-list');
  var placedList   = document.getElementById('placed-list');
  var saveBtn      = document.getElementById('btn-save');
  var saveStatus   = document.getElementById('save-status');
  var tabs         = document.querySelectorAll('.wall-tab');

  // ── Helpers ───────────────────────────────────────────────────────────────

  // Wall dimensions in inches: [width, height] in screen space
  function wallDims(wall) {
    var w = cfg.width_in, d = cfg.depth_in, h = cfg.height_in;
    if (wall === 'N' || wall === 'S') return [w, h];
    if (wall === 'E' || wall === 'W') return [d, h];
    return [w, d];  // ceiling / floor
  }

  // Convert screen fraction [0–1] to world coords for a given wall
  function screenToWorld(wall, fx, fy) {
    var w = cfg.width_in, d = cfg.depth_in, h = cfg.height_in;
    var hw = w / 2, hd = d / 2;
    if (wall === 'N' || wall === 'S') {
      return { x_in: (fx - 0.5) * w, y_in: (1 - fy) * h, z_in: wall === 'N' ? -hd : hd };
    }
    if (wall === 'E' || wall === 'W') {
      var signZ = wall === 'E' ? 1 : -1;
      return { x_in: wall === 'E' ? hw : -hw, y_in: (1 - fy) * h, z_in: signZ * (fx - 0.5) * d };
    }
    // ceiling / floor
    return { x_in: (fx - 0.5) * w, y_in: wall === 'ceiling' ? h : 0, z_in: (fy - 0.5) * d };
  }

  function worldToScreen(wall, p) {
    var w = cfg.width_in, d = cfg.depth_in, h = cfg.height_in;
    if (wall === 'N' || wall === 'S') {
      return { fx: p.x_in / w + 0.5, fy: 1 - p.y_in / h };
    }
    if (wall === 'E' || wall === 'W') {
      var signZ = wall === 'E' ? 1 : -1;
      return { fx: signZ * p.z_in / d + 0.5, fy: 1 - p.y_in / h };
    }
    return { fx: p.x_in / w + 0.5, fy: p.z_in / d + 0.5 };
  }

  // artwork pixel size on canvas at current scale
  function artPxSize(art) {
    var dims = wallDims(currentWall);
    var rect = canvasWrap.getBoundingClientRect();
    var scaleX = rect.width  / dims[0];
    var scaleY = rect.height / dims[1];
    var sc = Math.min(scaleX, scaleY);
    return { w: art.w_in * sc, h: art.h_in * sc };
  }

  // ── Render sidebar ────────────────────────────────────────────────────────

  function renderSidebar() {
    poolList.innerHTML = '';
    placedList.innerHTML = '';

    pool.forEach(function (art) {
      if (placementMap[art.id]) return;
      poolList.appendChild(makeSidebarThumb(art, false));
    });

    pool.concat(placements.map(function (p) { return p.artwork; })).forEach(function (art) {
      if (!placementMap[art.id]) return;
      var p = placementMap[art.id];
      var el = makeSidebarThumb(art, true);
      var lbl = el.querySelector('.pool-label');
      lbl.textContent += ' (' + p.wall + ')';
      placedList.appendChild(el);
    });
  }

  function makeSidebarThumb(art, placed) {
    var div = document.createElement('div');
    div.className = 'pool-thumb';
    div.dataset.id = art.id;
    div.draggable = !placed;
    div.innerHTML = '<img src="' + (art.thumb || art.img) + '" alt="">' +
      '<div class="pool-label">' + art.name + '</div>';

    if (!placed) {
      div.addEventListener('dragstart', function (e) {
        e.dataTransfer.setData('text/plain', String(art.id));
        div.classList.add('dragging-src');
      });
      div.addEventListener('dragend', function () {
        div.classList.remove('dragging-src');
      });
    }
    return div;
  }

  // ── Render wall canvas ────────────────────────────────────────────────────

  // Remove all placed-art elements from canvasWrap
  function clearPlacedDivs() {
    canvasWrap.querySelectorAll('.placed-art').forEach(function (el) { el.remove(); });
  }

  function renderWall() {
    clearPlacedDivs();
    Object.keys(placementMap).forEach(function (id) {
      var p = placementMap[id];
      if (p.wall !== currentWall) return;
      addPlacedDiv(p);
    });
  }

  function addPlacedDiv(p) {
    var art  = p.artwork;
    var sc   = worldToScreen(currentWall, p);
    var dims = wallDims(currentWall);
    var rect = canvasWrap.getBoundingClientRect();
    var scaleX = rect.width  / dims[0];
    var scaleY = rect.height / dims[1];

    // Maintain aspect but fit to wall scale
    var sc2 = Math.min(scaleX, scaleY);
    var pw = art.w_in * sc2;
    var ph = art.h_in * sc2;

    var cx = sc.fx * rect.width;
    var cy = sc.fy * rect.height;

    var div = document.createElement('div');
    div.className = 'placed-art';
    div.dataset.id = art.id;
    div.style.left   = (cx - pw / 2) + 'px';
    div.style.top    = (cy - ph / 2) + 'px';
    div.style.width  = pw + 'px';
    div.style.height = ph + 'px';
    div.innerHTML = '<img src="' + (art.thumb || art.img) + '" alt="' + art.name + '">' +
      '<div class="placard-bar">' + art.name + '</div>';

    makeDraggableOnCanvas(div, p);
    div.addEventListener('click', function (e) {
      e.stopPropagation();
      div.classList.toggle('selected');
    });
    canvasWrap.appendChild(div);
  }

  // ── Drag on canvas ────────────────────────────────────────────────────────

  function makeDraggableOnCanvas(div, p) {
    var startMx, startMy, startL, startT;

    div.addEventListener('mousedown', function (e) {
      if (e.button !== 0) return;
      e.preventDefault();
      startMx = e.clientX; startMy = e.clientY;
      startL  = parseFloat(div.style.left);
      startT  = parseFloat(div.style.top);

      function onMove(ev) {
        var dx = ev.clientX - startMx;
        var dy = ev.clientY - startMy;
        div.style.left = (startL + dx) + 'px';
        div.style.top  = (startT + dy) + 'px';
      }
      function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
        // Update world coords
        var rect  = canvasWrap.getBoundingClientRect();
        var dims  = wallDims(currentWall);
        var scaleX = rect.width  / dims[0];
        var scaleY = rect.height / dims[1];
        var sc2 = Math.min(scaleX, scaleY);
        var cx = parseFloat(div.style.left) + parseFloat(div.style.width)  / 2;
        var cy = parseFloat(div.style.top)  + parseFloat(div.style.height) / 2;
        var fx = cx / rect.width;
        var fy = cy / rect.height;
        var world = screenToWorld(currentWall, fx, fy);
        p.x_in = world.x_in;
        p.y_in = world.y_in;
        p.z_in = world.z_in;
      }
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
    });
  }

  // ── Drop from sidebar ─────────────────────────────────────────────────────

  canvasWrap.addEventListener('dragover', function (e) { e.preventDefault(); });
  canvasWrap.addEventListener('drop', function (e) {
    e.preventDefault();
    var id = parseInt(e.dataTransfer.getData('text/plain'), 10);
    if (!id) return;
    var art = findArtwork(id);
    if (!art) return;

    var rect = canvasWrap.getBoundingClientRect();
    var fx = (e.clientX - rect.left) / rect.width;
    var fy = (e.clientY - rect.top)  / rect.height;
    var world = screenToWorld(currentWall, fx, fy);

    var p = { artwork: art, wall: currentWall, x_in: world.x_in, y_in: world.y_in, z_in: world.z_in };
    placementMap[art.id] = p;
    placements.push(p);

    addPlacedDiv(p);
    renderSidebar();
  });

  function findArtwork(id) {
    for (var i = 0; i < pool.length; i++) if (pool[i].id === id) return pool[i];
    for (var j = 0; j < placements.length; j++) if (placements[j].artwork.id === id) return placements[j].artwork;
    return null;
  }

  // ── Wall tab switching ────────────────────────────────────────────────────

  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      tabs.forEach(function (t) { t.classList.remove('active'); });
      tab.classList.add('active');
      currentWall = tab.dataset.wall;
      renderWall();
    });
  });

  // ── Toolbar actions ───────────────────────────────────────────────────────

  function getSelected() {
    return Array.from(canvasWrap.querySelectorAll('.placed-art.selected'));
  }

  function updateWorldFromDiv(div) {
    var id = parseInt(div.dataset.id, 10);
    var p  = placementMap[id];
    if (!p) return;
    var rect  = canvasWrap.getBoundingClientRect();
    var cx = parseFloat(div.style.left) + parseFloat(div.style.width)  / 2;
    var cy = parseFloat(div.style.top)  + parseFloat(div.style.height) / 2;
    var fx = cx / rect.width;
    var fy = cy / rect.height;
    var world = screenToWorld(currentWall, fx, fy);
    p.x_in = world.x_in; p.y_in = world.y_in; p.z_in = world.z_in;
  }

  document.getElementById('btn-align-h').addEventListener('click', function () {
    var sel = getSelected();
    if (sel.length < 2) return;
    var rect = canvasWrap.getBoundingClientRect();
    var cy = rect.height / 2;
    sel.forEach(function (div) {
      var h = parseFloat(div.style.height);
      div.style.top = (cy - h / 2) + 'px';
      updateWorldFromDiv(div);
    });
  });

  document.getElementById('btn-align-v').addEventListener('click', function () {
    var sel = getSelected();
    if (sel.length < 2) return;
    var rect = canvasWrap.getBoundingClientRect();
    var cx = rect.width / 2;
    sel.forEach(function (div) {
      var w = parseFloat(div.style.width);
      div.style.left = (cx - w / 2) + 'px';
      updateWorldFromDiv(div);
    });
  });

  document.getElementById('btn-dist-h').addEventListener('click', function () {
    var sel = getSelected().sort(function (a, b) { return parseFloat(a.style.left) - parseFloat(b.style.left); });
    if (sel.length < 3) return;
    var rect = canvasWrap.getBoundingClientRect();
    var firstL = parseFloat(sel[0].style.left);
    var lastR  = parseFloat(sel[sel.length - 1].style.left) + parseFloat(sel[sel.length - 1].style.width);
    var totalW = sel.reduce(function (s, d) { return s + parseFloat(d.style.width); }, 0);
    var gap = (lastR - firstL - totalW) / (sel.length - 1);
    var x = firstL;
    sel.forEach(function (div) {
      div.style.left = x + 'px';
      x += parseFloat(div.style.width) + gap;
      updateWorldFromDiv(div);
    });
    void rect;
  });

  document.getElementById('btn-dist-v').addEventListener('click', function () {
    var sel = getSelected().sort(function (a, b) { return parseFloat(a.style.top) - parseFloat(b.style.top); });
    if (sel.length < 3) return;
    var rect = canvasWrap.getBoundingClientRect();
    var firstT = parseFloat(sel[0].style.top);
    var lastB  = parseFloat(sel[sel.length - 1].style.top) + parseFloat(sel[sel.length - 1].style.height);
    var totalH = sel.reduce(function (s, d) { return s + parseFloat(d.style.height); }, 0);
    var gap = (lastB - firstT - totalH) / (sel.length - 1);
    var y = firstT;
    sel.forEach(function (div) {
      div.style.top = y + 'px';
      y += parseFloat(div.style.height) + gap;
      updateWorldFromDiv(div);
    });
    void rect;
  });

  document.getElementById('btn-remove').addEventListener('click', function () {
    getSelected().forEach(function (div) {
      var id = parseInt(div.dataset.id, 10);
      delete placementMap[id];
      placements = placements.filter(function (p) { return p.artwork.id !== id; });
      div.remove();
    });
    renderSidebar();
  });

  // Click on empty canvas → deselect all
  canvasWrap.addEventListener('click', function () {
    canvasWrap.querySelectorAll('.placed-art.selected').forEach(function (el) { el.classList.remove('selected'); });
  });

  // ── Save ─────────────────────────────────────────────────────────────────

  saveBtn.addEventListener('click', function () {
    saveStatus.textContent = 'Saving…';
    var body = {
      room: { width_in: cfg.width_in, depth_in: cfg.depth_in, height_in: cfg.height_in },
      placements: placements.map(function (p) {
        return { artwork_id: p.artwork.id, wall: p.wall, x_in: p.x_in, y_in: p.y_in, z_in: p.z_in };
      }),
    };
    fetch(saveUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
      body: JSON.stringify(body),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        saveStatus.textContent = data.ok ? 'Saved.' : ('Error: ' + (data.errors || []).join('; '));
        setTimeout(function () { saveStatus.textContent = ''; }, 3000);
      })
      .catch(function () { saveStatus.textContent = 'Save failed.'; });
  });

  // ── Re-render on window resize ────────────────────────────────────────────

  window.addEventListener('resize', function () { renderWall(); });

  // ── Init ──────────────────────────────────────────────────────────────────

  renderSidebar();
  renderWall();

}());
