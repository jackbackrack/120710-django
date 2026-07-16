/* Artist profile form — inline field validation + a loud, obvious summary of
 * what still needs fixing. Shared by the New and Edit artist pages. */
(function () {
  'use strict';

  var form = document.querySelector('form[method="post"]');
  if (!form) return;

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
  function ok(input, fb, msg)  { input.classList.remove('is-invalid'); input.classList.add('is-valid');  fb.style.color = '#198754'; fb.textContent = msg; }
  function bad(input, fb, msg) { input.classList.remove('is-valid');   input.classList.add('is-invalid'); fb.style.color = '#dc3545'; fb.textContent = msg; }
  function neutral(input, fb)  { input.classList.remove('is-valid', 'is-invalid'); fb.textContent = ''; }

  // ── Validators — return true if valid ─────────────────────────────────────
  function requiredText(label) {
    return function (el, fb) {
      if (el.value.trim()) { ok(el, fb, '✓'); return true; }
      bad(el, fb, label + ' is required'); return false;
    };
  }

  function validateEmail(el, fb) {
    var v = el.value.trim();
    if (!v) { bad(el, fb, 'Email is required'); return false; }
    if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) { ok(el, fb, '✓'); return true; }
    bad(el, fb, 'Enter a valid email address'); return false;
  }

  function validateZip(el, fb) {
    var v = el.value.trim();
    if (!v) { bad(el, fb, 'Zip code is required'); return false; }
    if (/^\d{5}(-\d{4})?$/.test(v)) { ok(el, fb, '✓'); return true; }
    bad(el, fb, 'Must be 5 digits (94710) or 9 digits (94710-1234)'); return false;
  }

  // Photo is required, but on the Edit page an existing photo already satisfies
  // it (the file input is empty then). data-has-image="1" marks that case.
  function validateImage(el, fb) {
    var hasNew = el.files && el.files.length > 0;
    var hasExisting = form.dataset.hasImage === '1';
    var clearBox = document.getElementById('id_image-clear');
    var cleared = clearBox && clearBox.checked;
    if (hasNew) { ok(el, fb, '✓'); return true; }
    if (hasExisting && !cleared) { neutral(el, fb); return true; }
    bad(el, fb, 'A profile photo is required'); return false;
  }

  function validatePhone(el, fb) {
    var v = el.value.trim();
    if (!v) { neutral(el, fb); return true; }  // optional
    var international = v.startsWith('+');
    var digits = v.replace(/\D/g, '');
    if (international) {
      if (digits.length >= 7 && digits.length <= 15) { ok(el, fb, '✓'); return true; }
      bad(el, fb, 'International numbers: start with + and country code, e.g. +44 7911 123456'); return false;
    }
    if (digits.length === 11 && digits[0] === '1') digits = digits.slice(1);
    if (digits.length === 10) {
      var pretty = '+1 (' + digits.slice(0,3) + ') ' + digits.slice(3,6) + '-' + digits.slice(6);
      ok(el, fb, 'Will save as: ' + pretty); return true;
    }
    bad(el, fb, 'Enter a 10-digit US number or international number starting with +'); return false;
  }

  function validateWebsite(el, fb) {
    var v = el.value.trim();
    if (!v) { neutral(el, fb); return true; }  // optional
    var url = v.includes('://') ? v : 'https://' + v;
    try {
      var p = new URL(url);
      if (p.hostname.includes('.')) { ok(el, fb, 'Will save as: ' + url); return true; }
    } catch (e) {}
    bad(el, fb, 'Enter a valid URL, e.g. mysite.com'); return false;
  }

  function validateHandle(el, fb) {
    var v = el.value.trim();
    if (!v) { neutral(el, fb); return true; }  // optional
    ok(el, fb, 'Will save as: ' + (v.startsWith('@') ? v : '@' + v)); return true;
  }

  // Required first so the summary/scroll lands on the first missing requirement.
  var fields = [
    { id: 'id_first_name', fn: requiredText('First name') },
    { id: 'id_last_name',  fn: requiredText('Last name') },
    { id: 'id_email',      fn: validateEmail },
    { id: 'id_zipcode',    fn: validateZip },
    { id: 'id_image',      fn: validateImage, event: 'change' },
    { id: 'id_phone',      fn: validatePhone },
    { id: 'id_website',    fn: validateWebsite },
    { id: 'id_instagram',  fn: validateHandle },
    { id: 'id_venmo',      fn: validateHandle },
  ];

  fields.forEach(function (f) {
    var el = document.getElementById(f.id);
    if (!el) return;
    f._el = el;
    f._fb = hint(el);
    el.addEventListener(f.event || 'input', function () {
      f.fn(f._el, f._fb);
      refreshBanner();
    });
  });

  // ── Loud error summary at the top of the form ─────────────────────────────
  function banner() {
    var b = document.getElementById('artist-form-error');
    if (!b) {
      b = document.createElement('div');
      b.id = 'artist-form-error';
      b.className = 'alert alert-danger';
      b.style.display = 'none';
      form.insertBefore(b, form.firstChild);
    }
    return b;
  }
  function countBad() {
    var n = 0;
    fields.forEach(function (f) { if (f._el && !f.fn(f._el, f._fb)) n++; });
    return n;
  }
  // Only *update* an already-shown banner (don't pop one up while still typing).
  function refreshBanner() {
    var b = document.getElementById('artist-form-error');
    if (!b || b.style.display === 'none') return;
    var n = countBad();
    if (n === 0) { b.style.display = 'none'; return; }
    b.textContent = 'Please fix the ' + n + ' highlighted field' + (n === 1 ? '' : 's') + ' below.';
  }

  // ── Highlight fields flagged missing by a server redirect (?highlight=) ────
  (function () {
    var highlight = new URLSearchParams(window.location.search).get('highlight');
    if (!highlight) return;
    var first = null;
    highlight.split(',').forEach(function (name) {
      var el = document.getElementById('id_' + name);
      if (!el) return;
      el.classList.add('is-invalid');
      if (!first) first = el;
    });
    if (first) { first.scrollIntoView({ behavior: 'smooth', block: 'center' }); first.focus(); }
  })();

  // ── Submit: validate all; if anything is wrong, block + show the summary ───
  form.addEventListener('submit', function (e) {
    var firstBad = null, n = 0;
    fields.forEach(function (f) {
      if (!f._el) return;
      if (!f.fn(f._el, f._fb)) { n++; if (!firstBad) firstBad = f._el; }
    });
    if (n > 0) {
      e.preventDefault();
      var b = banner();
      b.textContent = 'Please fix the ' + n + ' highlighted field' + (n === 1 ? '' : 's') + ' below.';
      b.style.display = '';
      b.scrollIntoView({ behavior: 'smooth', block: 'center' });
      if (firstBad) firstBad.focus({ preventScroll: true });
    }
  });
})();
