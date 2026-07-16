/* Slideshow overlay — vanilla JS, no dependencies */
(function () {
  'use strict';

  // ── State ─────────────────────────────────────────────────────────────────
  var items = [];       // [{img, thumb, title, sub, meta, url}]
  var current = 0;
  var activeSlot = 'a'; // which <img> tag is currently visible
  var infoVisible = true;
  var autoHideTimer = null;

  // DOM references (set after buildOverlay)
  var overlay, imgA, imgB, titleEl, subEl, yearEl, mediumEl, dimsEl, topbar, footer,
      progressFill, counterEl, thumbsEl, helpEl, openLink, saveBtnEl;

  // ── Build overlay DOM (once) ───────────────────────────────────────────────
  function buildOverlay() {
    overlay = document.createElement('div');
    overlay.id = 'slideshow-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', 'Slideshow');
    overlay.innerHTML =
      '<div id="ss-topbar">' +
        '<div id="ss-info">' +
          '<span id="ss-title"></span>' +
          '<span id="ss-sub"></span>' +
          '<span id="ss-year"></span>' +
          '<span id="ss-medium"></span>' +
          '<span id="ss-dims"></span>' +
        '</div>' +
        '<a id="ss-open-link" href="#" target="_blank" title="Open detail page" rel="noopener">&#x2197;</a>' +
        '<button class="save-artwork-btn ss-topbtn" id="ss-save-btn" title="Pin" style="display:none">&#128204;</button>' +
        '<button class="ss-topbtn" id="ss-info-btn" title="Toggle info (I)">&#x2139;</button>' +
        '<button class="ss-topbtn" id="ss-close-btn" title="Close (Esc)">&#x2715;</button>' +
      '</div>' +
      '<button id="ss-prev" aria-label="Previous">&#8249;</button>' +
      '<div id="ss-stage">' +
        '<img id="ss-img-a" alt="">' +
        '<img id="ss-img-b" alt="" class="ss-hidden-img">' +
      '</div>' +
      '<button id="ss-next" aria-label="Next">&#8250;</button>' +
      '<div id="ss-footer">' +
        '<div id="ss-progress-row">' +
          '<div id="ss-progress-bar"><div id="ss-progress-fill"></div></div>' +
          '<span id="ss-counter"></span>' +
          '<button class="ss-topbtn" id="ss-help-btn" title="Help (?)">?</button>' +
        '</div>' +
        '<div id="ss-thumbs"></div>' +
      '</div>' +
      '<div id="ss-help" style="display:none">' +
        '<div id="ss-help-inner">' +
          '<h3>Slideshow Controls</h3>' +
          '<table>' +
            '<tr><td>&#8592; &#8594;</td><td>Navigate</td></tr>' +
            '<tr><td>Space</td><td>Next</td></tr>' +
            '<tr><td>Enter / D</td><td>Open detail page</td></tr>' +
            '<tr><td>Esc</td><td>Close</td></tr>' +
            '<tr><td>I</td><td>Toggle info</td></tr>' +
            '<tr><td>?</td><td>This help</td></tr>' +
          '</table>' +
          '<button id="ss-help-close">Close</button>' +
        '</div>' +
      '</div>';
    document.body.appendChild(overlay);

    imgA         = overlay.querySelector('#ss-img-a');
    imgB         = overlay.querySelector('#ss-img-b');
    titleEl      = overlay.querySelector('#ss-title');
    subEl        = overlay.querySelector('#ss-sub');
    yearEl       = overlay.querySelector('#ss-year');
    mediumEl     = overlay.querySelector('#ss-medium');
    dimsEl       = overlay.querySelector('#ss-dims');
    topbar       = overlay.querySelector('#ss-topbar');
    footer       = overlay.querySelector('#ss-footer');
    progressFill = overlay.querySelector('#ss-progress-fill');
    counterEl    = overlay.querySelector('#ss-counter');
    thumbsEl     = overlay.querySelector('#ss-thumbs');
    helpEl       = overlay.querySelector('#ss-help');
    openLink     = overlay.querySelector('#ss-open-link');
    saveBtnEl    = overlay.querySelector('#ss-save-btn');

    overlay.querySelector('#ss-close-btn').addEventListener('click', close);
    overlay.querySelector('#ss-prev').addEventListener('click', prev);
    overlay.querySelector('#ss-next').addEventListener('click', next);
    overlay.querySelector('#ss-info-btn').addEventListener('click', toggleInfo);
    overlay.querySelector('#ss-help-btn').addEventListener('click', showHelp);
    overlay.querySelector('#ss-help-close').addEventListener('click', hideHelp);
    helpEl.addEventListener('click', function (e) {
      if (e.target === helpEl) hideHelp();
    });

    // ── Photo-roll swipe: drag the real current image + neighbor ghosts ───────
    // Horizontal drag navigates; tap (barely moved) toggles info overlay.
    var swipePanel = overlay.querySelector('#ss-stage');
    var swipeStartX = 0, swipeStartY = 0, swipeStartTime = 0, swipeDragging = false;
    var swActive = false, swW = 0, ghostPrev = null, ghostNext = null;

    function swImg(i) {
      var a = items;
      if (!a.length) return null;
      i = ((i % a.length) + a.length) % a.length;
      return a[i] ? a[i].img : null;
    }

    function swCurImg() { return activeSlot === 'a' ? imgA : imgB; }

    function swMakeGhost(url, offset) {
      var g = document.createElement('img');
      g.className = 'ss-swipe-ghost';
      if (url) g.src = url;
      g.draggable = false;
      g.style.transition = 'none';
      g.style.transform = 'translateX(' + offset + 'px)';
      swipePanel.appendChild(g);
      return g;
    }

    function swBegin() {
      swActive = true;
      swW = swipePanel.offsetWidth || window.innerWidth;
      swCurImg().style.transition = 'none';
      ghostPrev = swMakeGhost(swImg(current - 1), -swW);
      ghostNext = swMakeGhost(swImg(current + 1), swW);
    }

    function swCleanup() {
      if (ghostPrev) { ghostPrev.remove(); ghostPrev = null; }
      if (ghostNext) { ghostNext.remove(); ghostNext = null; }
      imgA.style.transition = ''; imgA.style.transform = '';
      imgB.style.transition = ''; imgB.style.transform = '';
      swActive = false;
    }

    overlay.addEventListener('touchstart', function (e) {
      if (e.touches.length !== 1) return;
      swipeStartX = e.touches[0].clientX;
      swipeStartY = e.touches[0].clientY;
      swipeStartTime = Date.now();
      swipeDragging = true;
    }, { passive: true });

    overlay.addEventListener('touchmove', function (e) {
      if (!swipeDragging) return;
      var dx = e.touches[0].clientX - swipeStartX;
      var dy = Math.abs(e.touches[0].clientY - swipeStartY);
      if (!swActive && dy > Math.abs(dx) && Math.abs(dx) < 12) { swipeDragging = false; return; }
      e.preventDefault();
      if (!swActive) swBegin();
      swCurImg().style.transform = 'translateX(' + dx + 'px)';
      if (ghostPrev) ghostPrev.style.transform = 'translateX(' + (-swW + dx) + 'px)';
      if (ghostNext) ghostNext.style.transform = 'translateX(' + (swW + dx) + 'px)';
    }, { passive: false });

    overlay.addEventListener('touchend', function (e) {
      if (!swipeDragging) return;
      swipeDragging = false;
      var dx = e.changedTouches[0].clientX - swipeStartX;
      if (!swActive) {
        if (Math.abs(dx) < 8) {
          var isControl = e.target.closest('#ss-topbar, #ss-footer, #ss-prev, #ss-next, #ss-help');
          if (!isControl) toggleInfo();
        }
        return;
      }
      var elapsed = Math.max(Date.now() - swipeStartTime, 1);
      var vel = dx / elapsed;
      var commit = Math.abs(dx) > swW * 0.25 || Math.abs(vel) > 0.4;
      var EASE = 'transform 0.28s cubic-bezier(0.25, 0.46, 0.45, 0.94)';
      var SPRING = 'transform 0.34s cubic-bezier(0.34, 1.56, 0.64, 1)';
      var cur = swCurImg();
      if (commit) {
        var navDir = dx < 0 ? 1 : -1;
        cur.style.transition = EASE;
        if (ghostPrev) ghostPrev.style.transition = EASE;
        if (ghostNext) ghostNext.style.transition = EASE;
        if (navDir > 0) {
          cur.style.transform = 'translateX(' + (-swW) + 'px)';
          if (ghostNext) ghostNext.style.transform = 'translateX(0px)';
        } else {
          cur.style.transform = 'translateX(' + swW + 'px)';
          if (ghostPrev) ghostPrev.style.transform = 'translateX(0px)';
        }
        goTo(current + navDir);
        setTimeout(swCleanup, 320);
      } else {
        cur.style.transition = SPRING;
        if (ghostPrev) ghostPrev.style.transition = SPRING;
        if (ghostNext) ghostNext.style.transition = SPRING;
        cur.style.transform = 'translateX(0px)';
        if (ghostPrev) ghostPrev.style.transform = 'translateX(' + (-swW) + 'px)';
        if (ghostNext) ghostNext.style.transform = 'translateX(' + swW + 'px)';
        setTimeout(swCleanup, 340);
      }
    });

    // Mouse movement → show chrome, then auto-hide after 2.5s
    overlay.addEventListener('mousemove', resetAutoHide);
  }

  // ── Auto-hide chrome ───────────────────────────────────────────────────────
  function resetAutoHide() {
    if (!infoVisible) return;
    topbar.classList.remove('ss-hidden');
    footer.classList.remove('ss-hidden');
    clearTimeout(autoHideTimer);
    autoHideTimer = setTimeout(function () {
      topbar.classList.add('ss-hidden');
      footer.classList.add('ss-hidden');
    }, 2500);
  }

  // ── Save state helpers ─────────────────────────────────────────────────────
  function getSavedState(item) {
    if (!item.artworkId) return item.saved;
    var cardBtn = document.querySelector(
      '.save-artwork-btn:not(#ss-save-btn)[data-artwork-id="' + item.artworkId + '"]'
    );
    return cardBtn ? cardBtn.classList.contains('saved') : item.saved;
  }

  // ── Navigation ─────────────────────────────────────────────────────────────
  function goTo(idx) {
    if (!items.length) return;
    idx = ((idx % items.length) + items.length) % items.length;
    current = idx;
    var item = items[current];

    // Crossfade: load new image into the inactive slot, then swap visibility
    var incoming = activeSlot === 'a' ? imgB : imgA;
    var outgoing  = activeSlot === 'a' ? imgA : imgB;
    incoming.src = item.img;
    incoming.alt = item.title;
    incoming.classList.remove('ss-hidden-img');
    outgoing.classList.add('ss-hidden-img');
    activeSlot = activeSlot === 'a' ? 'b' : 'a';

    titleEl.textContent  = item.title;
    subEl.textContent    = item.sub    || '';
    yearEl.textContent   = item.year   || '';
    mediumEl.textContent = item.medium || '';
    dimsEl.textContent   = item.dims   || '';
    if (item.url) {
      openLink.href = item.url;
      openLink.style.display = '';
    } else {
      openLink.style.display = 'none';
    }
    if (item.saveUrl) {
      var isSaved = getSavedState(item);
      saveBtnEl.dataset.saveUrl = item.saveUrl;
      saveBtnEl.dataset.artworkId = item.artworkId;
      saveBtnEl.classList.toggle('saved', isSaved);
      saveBtnEl.title = isSaved ? 'Unpin' : 'Pin';
      saveBtnEl.style.display = '';
    } else {
      saveBtnEl.style.display = 'none';
    }

    var pct = items.length > 1 ? (current / (items.length - 1)) * 100 : 100;
    progressFill.style.width = pct + '%';
    counterEl.textContent = (current + 1) + ' / ' + items.length;

    updateThumbs();
    preloadAdjacent();
    resetAutoHide();
  }

  function next() { goTo(current + 1); }
  function prev() { goTo(current - 1); }

  // ── Thumbnails ─────────────────────────────────────────────────────────────
  function buildThumbs() {
    thumbsEl.innerHTML = '';
    items.forEach(function (item, i) {
      var img = document.createElement('img');
      img.className = 'ss-thumb';
      img.src = item.thumb;
      img.alt = '';
      img.title = item.title;
      img.addEventListener('click', (function (idx) {
        return function () { goTo(idx); };
      })(i));
      thumbsEl.appendChild(img);
    });
  }

  function updateThumbs() {
    var thumbs = thumbsEl.querySelectorAll('.ss-thumb');
    thumbs.forEach(function (t, i) {
      t.classList.toggle('ss-thumb-active', i === current);
    });
    var active = thumbs[current];
    if (active) {
      active.scrollIntoView({ inline: 'nearest', block: 'nearest', behavior: 'smooth' });
    }
  }

  // ── Preload adjacent images ────────────────────────────────────────────────
  function preloadAdjacent() {
    if (!items.length) return;
    [current + 1, current - 1].forEach(function (idx) {
      idx = ((idx % items.length) + items.length) % items.length;
      (new Image()).src = items[idx].img;
    });
  }

  // ── Info toggle ────────────────────────────────────────────────────────────
  function toggleInfo() {
    infoVisible = !infoVisible;
    if (infoVisible) {
      topbar.classList.remove('ss-hidden');
      footer.classList.remove('ss-hidden');
      resetAutoHide();
    } else {
      clearTimeout(autoHideTimer);
      topbar.classList.add('ss-hidden');
      footer.classList.add('ss-hidden');
    }
  }

  // ── Help panel ─────────────────────────────────────────────────────────────
  function showHelp() { helpEl.style.display = 'flex'; }
  function hideHelp() { helpEl.style.display = 'none'; }

  // ── Open / Close ───────────────────────────────────────────────────────────
  function open(slideItems, startIndex) {
    if (!slideItems || !slideItems.length) return;
    items = slideItems;
    current = 0;
    infoVisible = true;
    buildThumbs();

    // Reset both image slots
    imgA.src = '';
    imgB.src = '';
    imgA.classList.remove('ss-hidden-img');
    imgB.classList.add('ss-hidden-img');
    activeSlot = 'a';

    overlay.classList.add('ss-open');
    document.body.style.overflow = 'hidden';
    topbar.classList.remove('ss-hidden');
    footer.classList.remove('ss-hidden');
    goTo(startIndex || 0);
  }

  function close() {
    overlay.classList.remove('ss-open');
    document.body.style.overflow = '';
    clearTimeout(autoHideTimer);
    helpEl.style.display = 'none';
    imgA.src = '';
    imgB.src = '';
  }

  // ── Keyboard ───────────────────────────────────────────────────────────────
  document.addEventListener('keydown', function (e) {
    // `S` shortcut when slideshow is closed
    if (!overlay || !overlay.classList.contains('ss-open')) {
      var tag = document.activeElement && document.activeElement.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (e.key === 's' || e.key === 'S') {
        var allItems = [];
        document.querySelectorAll('.cards').forEach(function (c) {
          allItems = allItems.concat(collectItems(c));
        });
        if (allItems.length) open(allItems, 0);
      }
      return;
    }

    // Controls while slideshow is open
    switch (e.key) {
      case 'ArrowRight':
      case ' ':
        e.preventDefault();
        next();
        break;
      case 'ArrowLeft':
        e.preventDefault();
        prev();
        break;
      case 'Escape':
        if (helpEl.style.display !== 'none') hideHelp();
        else close();
        break;
      case 'Enter':
      case 'd':
      case 'D':
        if (items[current] && items[current].url) {
          close();
          window.location.href = items[current].url;
        }
        break;
      case 'i':
      case 'I':
        toggleInfo();
        break;
      case '?':
        showHelp();
        break;
    }
  });

  // ── Collect slideable items from a .cards container ────────────────────────
  // Reads data-sl-* attributes off .card divs and img.card__image elements.
  function collectItems(container) {
    var result = [];
    container.querySelectorAll('.card').forEach(function (card) {
      var img = card.querySelector('img.card__image');
      if (!img) return;
      result.push({
        img:       img.dataset.slImg       || img.src,
        thumb:     img.src,
        title:     card.dataset.slTitle    || '',
        sub:       card.dataset.slSub      || '',
        year:      card.dataset.slYear     || '',
        medium:    card.dataset.slMedium   || '',
        dims:      card.dataset.slDims     || '',
        url:       card.dataset.slUrl      || '',
        saveUrl:   card.dataset.slSaveUrl  || '',
        artworkId: card.dataset.slArtworkId || '',
        saved:     !!card.dataset.slSaved,
      });
    });
    return result;
  }

  // Collect items from every .cards container on the page (computed fresh each
  // call so infinite-scroll–appended cards are always included).
  function collectAllItems() {
    var all = [];
    document.querySelectorAll('.cards').forEach(function (c) {
      all = all.concat(collectItems(c));
    });
    return all;
  }

  // ── Wire pre-placed [data-ss-first-cards] buttons (in status bar / block_title)
  function wireStatusBarButtons() {
    document.querySelectorAll('[data-ss-first-cards]').forEach(function (btn) {
      if (collectAllItems().length < 2) { btn.style.display = 'none'; return; }
      btn.addEventListener('click', function () { open(collectAllItems(), 0); });
    });
  }

  // ── Wire template-placed [data-ss-section] buttons in section-label h2s ────
  // Each button is inside a .section-label div; finds the next .cards sibling.
  function wireSectionButtons() {
    document.querySelectorAll('[data-ss-section]').forEach(function (btn) {
      var label = btn.closest('.section-label');
      if (!label) { btn.style.display = 'none'; return; }
      var sib = label.nextElementSibling, grid = null;
      while (sib) {
        if (sib.classList.contains('cards')) { grid = sib; break; }
        if (sib.classList.contains('section-label')) break; // reached next section
        sib = sib.nextElementSibling;
      }
      if (!grid || collectItems(grid).length < 1) { btn.style.display = 'none'; return; }
      // Recompute at click time (cards may be appended after wiring).
      btn.addEventListener('click', function () { open(collectItems(grid), 0); });
    });
  }

  // ── Wire per-card ▶ play buttons ───────────────────────────────────────────
  // Idempotent: skips cards that already have a play button, so it can be re-run
  // after infinite scroll appends more cards. Items + start index are computed
  // at click time so the slideshow always covers everything currently loaded.
  function openContainerAt(container, card) {
    var items = collectItems(container);
    var cards = Array.prototype.filter.call(
      container.querySelectorAll('.card'),
      function (c) { return c.querySelector('img.card__image'); });
    var idx = cards.indexOf(card);
    open(items, idx < 0 ? 0 : idx);
  }

  function wireCardPlayButtons() {
    document.querySelectorAll('.cards').forEach(function (container) {
      container.querySelectorAll('.card').forEach(function (card) {
        if (!card.querySelector('img.card__image')) return;
        if (card.querySelector(':scope > .ss-card-play')) return;   // already wired
        var btn = document.createElement('button');
        btn.className = 'ss-card-play';
        btn.setAttribute('aria-label', 'Open slideshow');
        btn.title = 'Slideshow';
        btn.innerHTML = '&#9654;';
        btn.addEventListener('click', function (e) {
          e.preventDefault();
          e.stopPropagation();
          openContainerAt(container, card);
        });
        card.appendChild(btn);
      });
    });
  }

  // ── Init ───────────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', function () {
    buildOverlay();
    wireStatusBarButtons();
    wireSectionButtons();
    wireCardPlayButtons();
  });
  // Infinite scroll appended new cards → wire play buttons on the new ones.
  document.addEventListener('cards:appended', wireCardPlayButtons);

})();
