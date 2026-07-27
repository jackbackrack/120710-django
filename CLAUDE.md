# Working in this repo

## Keep docs and links current with the change

User-facing documentation and in-app links are **part of a change**, not follow-up
work. Update them in the same commit as the behaviour.

Before committing, check whether the change touches anything described in:

- **`eatart/role_docs.py`** — `HOW_TO_GUIDES` (step-by-step task walkthroughs) and
  `ROLE_DOCUMENTATION` (per-role descriptions of pages and form fields). Field labels,
  button names, whether something is required, and where a control lives all appear
  here verbatim, so renaming or moving anything in the UI usually means editing this
  file too.
- **`README.md`** and **`docs/*.md`**.
- **`{% url 'howto' %}#anchor` links in templates**, and any hard-coded path in a
  template or an email body.

### How-to anchors

A guide's anchor is `guide['anchor']` if set, otherwise `slugify(guide['title'])`.
**Retitling a guide therefore breaks every link pointing at it**, silently — the
browser just lands at the top of the help page. Prefer giving a guide a stable
`'anchor'` key over relying on its title.

Note that some guides exist in two mutually exclusive versions (a `public_only` one
for signed-out readers and a role-gated one for signed-in readers). Both need the same
`'anchor'` so a single link works for either reader.

`gallery/tests.py::HowToAnchorTests` scans the templates and fails if any
`{% url 'howto' %}#anchor` points at a section that does not exist.

## Running the tests

```
./env/bin/python manage.py test gallery reviews accounts \
    --settings=eatart.settings_test --parallel auto
```

`eatart/settings_test.py` swaps in MD5 password hashing and disables reCAPTCHA. Without
it the suite is far slower and `ArtworkInquireTests` fails spuriously, because the dev
environment has live reCAPTCHA keys.

## Template comments

`{# ... #}` is **single-line only** — Django's lexer regex is not `DOTALL`, so a
multi-line `{# #}` is never recognised as a comment and renders verbatim onto the page.
Use `{% comment %}` / `{% endcomment %}` for anything spanning more than one line.

## Static files in local dev

`DEBUG` uses plain, un-hashed static storage so edits show without `collectstatic`.
That means the browser caches JS and CSS by filename: if a change appears not to have
taken effect, hard-reload before suspecting the code. Production uses manifest storage
on S3, where filenames are content-hashed and caches bust on their own.
