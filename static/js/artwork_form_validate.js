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

  // ── Wire fields ────────────────────────────────────────────────────────────

  var fields = [
    { id: 'id_name',             fn: validateName },
    { id: 'id_end_year',         fn: validateEndYear },
    { id: 'id_start_year',       fn: validateStartYear },
    { id: 'id_medium',           fn: validateMedium },
    { id: 'id_width_inches',     fn: validateDimRequired },
    { id: 'id_height_inches',    fn: validateDimRequired },
    { id: 'id_depth_inches',     fn: validateDimOptional },
    { id: 'id_price',            fn: validatePrice },
    { id: 'id_replacement_cost', fn: validatePositiveOptional },
  ];

  fields.forEach(function (f) {
    var el = document.getElementById(f.id);
    if (!el) return;
    var fb = hint(el);
    el.addEventListener('input', function () { f.fn(el, fb); });
    f._el = el;
    f._fb = fb;
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

  // ── Submit: validate all, block + scroll to first error ───────────────────
  // Find the form that owns the artwork name field (handles pages with multiple forms).
  var nameEl = document.getElementById('id_name');
  var form = nameEl ? nameEl.closest('form') : null;
  if (form) {
    form.addEventListener('submit', function (e) {
      var firstBad = null;
      fields.forEach(function (f) {
        if (!f._el) return;
        var valid = f.fn(f._el, f._fb);
        if (!valid && !firstBad) firstBad = f._el;
      });
      if (firstBad) {
        e.preventDefault();
        firstBad.scrollIntoView({ behavior: 'smooth', block: 'center' });
        firstBad.focus();
      }
    });
  }
}());
