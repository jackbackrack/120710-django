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

    // Physics swipe — finger follows drag; commit slides off and in; cancel springs back
    // Horizontal drag navigates; tap (barely moved) toggles info overlay.
    var swipePanel = overlay.querySelector('#ss-stage');
    var swipeStartX = 0, swipeStartY = 0, swipeStartTime = 0, swipeDragging = false;
    overlay.addEventListener('touchstart', function (e) {
      if (e.touches.length !== 1) return;
      swipeStartX = e.touches[0].clientX;
      swipeStartY = e.touches[0].clientY;
      swipeStartTime = Date.now();
      swipeDragging = true;
      swipePanel.style.transition = 'none';
    }, { passive: true });
    overlay.addEventListener('touchmove', function (e) {
      if (!swipeDragging) return;
      var dx = e.touches[0].clientX - swipeStartX;
      var dy = Math.abs(e.touches[0].clientY - swipeStartY);
      if (dy > Math.abs(dx) && Math.abs(dx) < 12) { swipeDragging = false; swipePanel.style.transform = ''; return; }
      e.preventDefault();
      swipePanel.style.transform = 'translateX(' + dx + 'px)';
    }, { passive: false });
    overlay.addEventListener('touchend', function (e) {
      if (!swipeDragging) return;
      swipeDragging = false;
      var dx = e.changedTouches[0].clientX - swipeStartX;
      var elapsed = Math.max(Date.now() - swipeStartTime, 1);
      var vel = dx / elapsed;
      var W = swipePanel.offsetWidth || window.innerWidth;
      var commit = Math.abs(dx) > W * 0.25 || Math.abs(vel) > 0.4;
      if (commit) {
        var dir = dx < 0 ? -1 : 1;
        swipePanel.style.transition = 'transform 0.2s ease-in';
        swipePanel.style.transform = 'translateX(' + (dir * W * 1.05) + 'px)';
        setTimeout(function () {
          if (dir < 0) next(); else prev();
          swipePanel.style.transition = 'none';
          swipePanel.style.transform = 'translateX(' + (-dir * W * 1.05) + 'px)';
          requestAnimationFrame(function () {
            requestAnimationFrame(function () {
              swipePanel.style.transition = 'transform 0.28s cubic-bezier(0.25, 0.46, 0.45, 0.94)';
              swipePanel.style.transform = 'translateX(0)';
            });
          });
        }, 185);
      } else {
        swipePanel.style.transition = 'transform 0.38s cubic-bezier(0.34, 1.56, 0.64, 1)';
        swipePanel.style.transform = 'translateX(0)';
        // Tap (barely moved) toggles info
        if (Math.abs(dx) < 8) {
          var isControl = e.target.closest('#ss-topbar, #ss-footer, #ss-prev, #ss-next, #ss-help');
          if (!isControl) toggleInfo();
        }
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

  // ── Wire pre-placed [data-ss-first-cards] buttons (in status bar / block_title)
  // Collects items from ALL .cards containers on the page into one slideshow.
  function wireStatusBarButtons() {
    document.querySelectorAll('[data-ss-first-cards]').forEach(function (btn) {
      var allItems = [];
      document.querySelectorAll('.cards').forEach(function (c) {
        allItems = allItems.concat(collectItems(c));
      });
      if (allItems.length < 2) { btn.style.display = 'none'; return; }
      btn.addEventListener('click', function () { open(allItems, 0); });
    });
  }

  // ── Wire template-placed [data-ss-section] buttons in section-label h2s ────
  // Each button is inside a .section-label div; finds the next .cards sibling.
  function wireSectionButtons() {
    document.querySelectorAll('[data-ss-section]').forEach(function (btn) {
      var label = btn.closest('.section-label');
      if (!label) { btn.style.display = 'none'; return; }
      var sib = label.nextElementSibling;
      while (sib) {
        if (sib.classList.contains('cards')) {
          var si = collectItems(sib);
          if (si.length < 1) { btn.style.display = 'none'; return; }
          btn.addEventListener('click', (function (items) {
            return function () { open(items, 0); };
          })(si));
          return;
        }
        if (sib.classList.contains('section-label')) break; // reached next section
        sib = sib.nextElementSibling;
      }
      btn.style.display = 'none';
    });
  }

  // ── Wire per-card ▶ play buttons ───────────────────────────────────────────
  // Inserts a small play button into each card that has an image.
  // Clicking it opens the section slideshow starting at that card's index.
  function wireCardPlayButtons() {
    document.querySelectorAll('.cards').forEach(function (container) {
      var containerItems = collectItems(container);
      if (!containerItems.length) return;
      var slideIdx = 0;
      container.querySelectorAll('.card').forEach(function (card) {
        if (!card.querySelector('img.card__image')) return;
        var myIdx = slideIdx++;
        var btn = document.createElement('button');
        btn.className = 'ss-card-play';
        btn.setAttribute('aria-label', 'Open slideshow');
        btn.title = 'Slideshow';
        btn.innerHTML = '&#9654;';
        btn.addEventListener('click', function (e) {
          e.preventDefault();
          e.stopPropagation();
          open(containerItems, myIdx);
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

})();
