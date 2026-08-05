#!/bin/bash
#
# Everything that should be true before a commit. Run it, do not skim it.
#
#     ./scripts/check.sh
#
# Each check here exists because something got through without it, in one session:
#
#   1. imports        — a stray apostrophe in role_docs.py broke the app at import, and was
#                       committed because the test output had been grepped down to pass/fail
#                       lines and an ImportError prints neither.
#   2. migrations     — a model field changed with no migration made.
#   3. untracked      — two email templates the mail path renders sat untracked through
#                       eleven commits; the tests passed throughout because they read the
#                       working tree, not the index.
#   4. comments       — {# #} is single-line only in Django; a multi-line one renders
#                       verbatim onto the page.
#   5. tests          — the full suite, unfiltered, exit status honoured.
#
# The rule this encodes: never decide something passed by looking at a filtered slice of
# its output.
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
PY=./env/bin/python
failed=0

step () { printf '\n\033[1m== %s\033[0m\n' "$1"; }
fail () { printf '\033[31mFAIL\033[0m  %s\n' "$1"; failed=1; }
ok   () { printf '\033[32mok\033[0m    %s\n' "$1"; }

step "1. Everything imports"
if out=$($PY -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'eatart.settings')
django.setup()
import importlib, pkgutil, pathlib
# role_docs and the form/view modules are where a syntax error hides longest, because
# nothing imports them until a page is served.
for name in ('eatart.role_docs', 'gallery.forms', 'gallery.consignment',
             'gallery.consignment_mail', 'gallery.consignment_pdf', 'gallery.urls'):
    importlib.import_module(name)
print('ok')
" 2>&1); then ok "modules import"; else fail "import error"; echo "$out" | tail -20; fi

step "2. No model change without a migration"
if out=$($PY manage.py makemigrations --check --dry-run 2>&1); then
    ok "migrations up to date"
else
    fail "a model field has no migration"; echo "$out" | grep -v '^20' | tail -10
fi

step "3. Nothing this change needs is untracked"
untracked=$(git status --porcelain --untracked-files=all \
            | grep '^??' \
            | grep -vE '\.claude/|static/img/howto/|^\?\? (logs\.txt|market/|UPGRADING)' || true)
if [ -z "$untracked" ]; then
    ok "no untracked source files"
else
    fail "untracked files — a git add scoped to some paths will miss these"
    echo "$untracked"
fi

step "3b. Nothing this change needs is *ignored*"
# `git status --untracked-files=all` does not list ignored files, so an ignored one is
# invisible to the check above. That is not hypothetical: .gitignore has `/scripts/*`, the
# scripts in there were force-added years ago, and this very file was written, committed
# past, pushed, and found missing from the remote afterwards.
#
# Reported only for directories that already contain tracked files — an ignored file
# sitting among tracked ones is nearly always something that was meant to be committed.
ignored=$(git status --porcelain --ignored=matching \
          | grep '^!!' | sed 's/^!! //' || true)
suspect=""
for path in $ignored; do
    # Matched on the path, not its parent: git reports whole ignored directories with a
    # trailing slash, so `dirname gallery/__pycache__/` is `gallery` — which is full of
    # tracked files, and every cache directory in the project was reported.
    case "$path" in
        env/*|media/*|static/img/howto/*|.claude/*|*__pycache__*|node_modules/*|*.pyc) continue ;;
        # Correctly ignored and deliberately so: a filled-in copy of the MagTag config,
        # holding a real Wi-Fi credential. Its template is tracked as settings.toml.example.
        magtag/settings.base.toml) continue ;;
    esac
    dir=$(dirname "$path")
    case "$dir" in
        .) continue ;;
    esac
    if [ -n "$(git ls-files "$dir" | head -1)" ]; then
        suspect="$suspect$path\n"
    fi
done
if [ -z "$suspect" ]; then
    ok "no ignored files sitting among tracked ones"
else
    fail "ignored, but in a directory of tracked files — force-add or widen .gitignore"
    printf "%b" "$suspect"
fi

step "4. No multi-line {# #} template comments"
if out=$($PY - <<'PYEOF'
import re, pathlib, sys
bad = []
for path in pathlib.Path('.').rglob('*.html'):
    if 'env/' in str(path) or '.claude/' in str(path):
        continue
    src = path.read_text(errors='replace')
    for m in re.finditer(r'\{#', src):
        seg = src[m.start():]
        close, newline = seg.find('#}'), seg.find('\n')
        if close == -1 or (newline != -1 and newline < close):
            bad.append(f'{path}:{src[:m.start()].count(chr(10)) + 1}')
print('\n'.join(bad))
sys.exit(1 if bad else 0)
PYEOF
); then ok "template comments are single-line"; else fail "multi-line {# #}"; echo "$out"; fi

step "5. The full test suite"
# No grep. A filtered pass is how a broken build looks like a green one.
if $PY manage.py test gallery reviews accounts \
        --settings=eatart.settings_test --parallel auto 2>&1 | tail -25; then
    ok "tests passed"
else
    fail "tests failed"
fi

printf '\n'
if [ "$failed" -eq 0 ]; then
    printf '\033[32mAll checks passed.\033[0m\n'
else
    printf '\033[31mSomething failed above. Do not commit.\033[0m\n'
fi
exit "$failed"
