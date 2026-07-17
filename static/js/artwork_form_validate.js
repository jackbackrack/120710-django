/* Real-time validation + UX helpers for artwork add/edit/submit forms */
(function () {
  var THIS_YEAR = new Date().getFullYear();

  function hint(input) {
    var fb = input.parentNode.querySelector('.ae-hint');
    if (!fb) {
      fb = document.createElement('div');
      fb.className = 'ae-hint form-text mt-1';
      fb.style.fontSize = '0.82em';
      input.insertAdjacentElement('afterend', fb);
    }
    return fb;
  }

  function ok(input, fb, msg) {
    input.classList.remove('is-invalid');
    input.classList.add('is-valid');
    fb.style.color = '#198754';
    fb.textContent = msg || '✓';
  }

  function bad(input, fb, msg) {
    input.classList.remove('is-valid');
    input.classList.add('is-invalid');
    fb.style.color = '#dc3545';
    fb.textContent = msg;
  }

  function neutral(input, fb) {
    input.classList.remove('is-valid', 'is-invalid');
    fb.textContent = '';
  }

  // ── Validators ─────────────────────────────────────────────────────────────

  function validateName(el, fb) {
    var v = el.value.trim();
    if (!v) { bad(el, fb, 'Title is required'); return false; }
    ok(el, fb); return true;
  }

  // Only present for staff without a linked artist; required when shown.
  function validateArtists(el, fb) {
    var has = el.selectedOptions ? el.selectedOptions.length > 0 : !!el.value;
    if (has) { ok(el, fb); return true; }
    bad(el, fb, 'Select at least one artist'); return false;
  }

  function validateEndYear(el, fb) {
    var v = el.value.trim();
    if (!v) { bad(el, fb, 'Year completed is required'); return false; }
    var n = parseInt(v, 10);
    if (isNaN(n) || n !== +v) { bad(el, fb, 'Enter a whole number'); return false; }
    if (n < 1000 || n > THIS_YEAR + 5) {
      bad(el, fb, 'Enter a year between 1000 and ' + (THIS_YEAR + 5)); return false;
    }
    ok(el, fb); return true;
  }

  function validateStartYear(el, fb) {
    var v = el.value.trim();
    if (!v) { neutral(el, fb); return true; }
    var n = parseInt(v, 10);
    if (isNaN(n) || n !== +v) { bad(el, fb, 'Enter a whole number'); return false; }
    if (n < 1000 || n > THIS_YEAR + 5) {
      bad(el, fb, 'Enter a year between 1000 and ' + (THIS_YEAR + 5)); return false;
    }
    var endEl = document.getElementById('id_end_year');
    var endVal = endEl ? parseInt(endEl.value.trim(), 10) : NaN;
    if (!isNaN(endVal) && n > endVal) {
      bad(el, fb, 'Start year must be ≤ end year (' + endVal + ')'); return false;
    }
    ok(el, fb); return true;
  }

  function validateMedium(el, fb) {
    var v = el.value.trim();
    if (!v) { bad(el, fb, 'Medium is required'); return false; }
    ok(el, fb); return true;
  }

  function validateDimRequired(el, fb) {
    var v = el.value.trim();
    if (!v) { bad(el, fb, 'Required'); return false; }
    var n = parseFloat(v);
    if (isNaN(n) || n < 0) { bad(el, fb, 'Enter a positive number'); return false; }
    ok(el, fb); return true;
  }

  function validateDimOptional(el, fb) {
    var v = el.value.trim();
    if (!v) { neutral(el, fb); return true; }
    var n = parseFloat(v);
    if (isNaN(n) || n < 0) { bad(el, fb, 'Enter a positive number'); return false; }
    ok(el, fb); return true;
  }

  function validatePrice(el, fb) {
    var typeEl = document.getElementById('id_pricing_type');
    var pricingType = typeEl ? typeEl.value : '';
    var v = el.value.trim();
    if (pricingType === 'for_sale' && !v) {
      bad(el, fb, 'Price is required when "For Sale" is selected'); return false;
    }
    if (!v) { neutral(el, fb); return true; }
    var n = parseFloat(v);
    if (isNaN(n) || n < 0) { bad(el, fb, 'Enter a positive number'); return false; }
    ok(el, fb); return true;
  }

  function validatePositiveOptional(el, fb) {
    var v = el.value.trim();
    if (!v) { neutral(el, fb); return true; }
    var n = parseFloat(v);
    if (isNaN(n) || n < 0) { bad(el, fb, 'Enter a positive number'); return false; }
    ok(el, fb); return true;
  }

  // Image is required on the New form, optional on Edit (an existing image counts).
  // data-image-required="1" marks the required (New) case; data-has-image="1" marks
  // an existing image on Edit.
  function validateImage(el, fb) {
    var f = el.closest('form');
    var hasNew = el.files && el.files.length > 0;
    var hasExisting = f && f.dataset.hasImage === '1';
    var required = f && f.dataset.imageRequired === '1';
    var clearBox = document.getElementById('id_image-clear');
    var cleared = clearBox && clearBox.checked;
    if (hasNew) { ok(el, fb, '✓'); return true; }
    if (hasExisting && !cleared) { neutral(el, fb); return true; }
    if (required) { bad(el, fb, 'An image is required'); return false; }
    neutral(el, fb); return true;
  }

  // ── Wire fields ────────────────────────────────────────────────────────────

  var fields = [
    { id: 'id_name',             fn: validateName },
    { id: 'id_artists',          fn: validateArtists, event: 'change' },
    { id: 'id_end_year',         fn: validateEndYear },
    { id: 'id_start_year',       fn: validateStartYear },
    { id: 'id_medium',           fn: validateMedium },
    { id: 'id_width_inches',     fn: validateDimRequired },
    { id: 'id_height_inches',    fn: validateDimRequired },
    { id: 'id_depth_inches',     fn: validateDimOptional },
    { id: 'id_image',            fn: validateImage, event: 'change' },
    { id: 'id_price',            fn: validatePrice },
    { id: 'id_replacement_cost', fn: validatePositiveOptional },
  ];

  fields.forEach(function (f) {
    var el = document.getElementById(f.id);
    if (!el) return;
    var fb = hint(el);
    f._el = el;
    f._fb = fb;
    el.addEventListener(f.event || 'input', function () { f.fn(el, fb); refreshBanner(); });
  });

  // Re-validate price when pricing type changes
  var typeEl = document.getElementById('id_pricing_type');
  if (typeEl) {
    typeEl.addEventListener('change', function () {
      var priceEl = document.getElementById('id_price');
      if (priceEl) validatePrice(priceEl, hint(priceEl));
    });
  }

  // Re-validate start_year when end_year changes (cross-field dependency)
  var endYearEl = document.getElementById('id_end_year');
  if (endYearEl) {
    endYearEl.addEventListener('input', function () {
      var startEl = document.getElementById('id_start_year');
      if (startEl && startEl.value.trim()) validateStartYear(startEl, hint(startEl));
    });
  }

  // ── Pricing type: show/hide price row ─────────────────────────────────────
  function updatePriceField() {
    var typeEl = document.getElementById('id_pricing_type');
    var priceRow = document.getElementById('div_id_price');
    var priceInput = document.getElementById('id_price');
    var priceLabel = priceRow ? priceRow.querySelector('label') : null;
    if (!typeEl || !priceRow) return;
    var val = typeEl.value;
    if (val === 'nfs' || val === 'on_request') {
      priceRow.style.display = 'none';
      if (priceInput) { priceInput.value = ''; priceInput.required = false; }
    } else if (val === 'best_offer') {
      priceRow.style.display = '';
      if (priceLabel) priceLabel.textContent = 'Minimum offer ($) — optional';
      if (priceInput) priceInput.required = false;
    } else {
      priceRow.style.display = '';
      if (priceLabel) priceLabel.textContent = 'Price ($)';
      if (priceInput) priceInput.required = true;
    }
  }
  var pricingTypeEl = document.getElementById('id_pricing_type');
  if (pricingTypeEl) {
    pricingTypeEl.addEventListener('change', updatePriceField);
    updatePriceField();
  }

  // ── Dimension keyboard navigation: x / × / / advances to next dim field ──
  var dims = ['id_width_inches', 'id_height_inches', 'id_depth_inches'];
  dims.forEach(function (id, i) {
    var el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('keydown', function (e) {
      if ((e.key === 'x' || e.key === 'X' || e.key === '×' || e.key === '/') && el.value.trim() !== '') {
        e.preventDefault();
        var next = document.getElementById(dims[i + 1]);
        if (next) { next.focus(); next.select(); }
      }
    });
  });

  // ── Loud error summary at the top of the form ─────────────────────────────
  // Find the form that owns the artwork name field (handles pages with multiple forms).
  var nameEl = document.getElementById('id_name');
  var form = nameEl ? nameEl.closest('form') : null;

  function countBad() {
    var n = 0;
    fields.forEach(function (f) { if (f._el && !f.fn(f._el, f._fb)) n++; });
    return n;
  }
  function banner() {
    if (!form) return null;
    var b = document.getElementById('artwork-form-error');
    if (!b) {
      b = document.createElement('div');
      b.id = 'artwork-form-error';
      b.className = 'alert alert-danger';
      b.style.display = 'none';
      form.insertBefore(b, form.firstChild);
    }
    return b;
  }
  // Only update an already-shown banner (don't pop one up while still typing).
  function refreshBanner() {
    var b = document.getElementById('artwork-form-error');
    if (!b || b.style.display === 'none') return;
    var n = countBad();
    if (n === 0) { b.style.display = 'none'; return; }
    b.textContent = 'Please fix the ' + n + ' highlighted field' + (n === 1 ? '' : 's') + ' below.';
  }

  // ── Submit: validate all; if anything is wrong, block + show the summary ───
  if (form) {
    form.addEventListener('submit', function (e) {
      var firstBad = null, n = 0;
      fields.forEach(function (f) {
        if (!f._el) return;
        if (!f.fn(f._el, f._fb)) { n++; if (!firstBad) firstBad = f._el; }
      });
      if (n > 0) {
        e.preventDefault();
        var b = banner();
        if (b) {
          b.textContent = 'Please fix the ' + n + ' highlighted field' + (n === 1 ? '' : 's') + ' below.';
          b.style.display = '';
          b.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
        if (firstBad) firstBad.focus({ preventScroll: true });
      }
    });
  }
}());
