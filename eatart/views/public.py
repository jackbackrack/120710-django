import datetime as dt

from django.conf import settings
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render
from django.utils.text import slugify

from eatart.howto_images import steps_with_images
from eatart.role_docs import GENERAL_GUIDE, HOW_TO_GUIDES, ROLE_DOCUMENTATION
from eatart.schemaorg.mappers import dump_json_ld, gallery_to_schema, schema_to_dict
from gallery.models import LinkTreeEntry, Show
from gallery.submission_cta import submit_ctas
from gallery.views.mixins import visible_site_or_404
from gallery.permissions import can_delete_show, can_manage_show, is_curator_user, is_juror_user, is_staff_user, visible_show_queryset


def index(request):
    today = dt.date.today()
    base = Show.objects.prefetch_related('curators', 'tags', 'events')
    base = visible_show_queryset(base, request.user)
    current_shows = list(base.filter(start__lte=today, end__gte=today).order_by('-start'))
    future_shows = list(base.filter(start__gt=today).order_by('start'))
    past_shows = list(base.filter(end__lt=today).order_by('-start'))

    hero_show = current_shows[0] if current_shows else (future_shows[0] if future_shows else None)
    hero_is_current = bool(current_shows)
    display_future_shows = future_shows if hero_is_current else future_shows[1:]

    all_shows = current_shows + future_shows + past_shows
    manageable_show_ids = {s.id for s in all_shows if can_manage_show(request.user, s)}
    deletable_show_ids = {s.id for s in all_shows if can_delete_show(request.user, s)}

    return render(request, 'public/index.html', {
        'hero_show': hero_show,
        # Same call to action the show page uses, so the home page is a real entry
        # point into the submission flow rather than a dead end that makes a visitor
        # hunt for the show first.
        # hero_show is always in all_shows, so its call to action is already in here under its
        # own id — the hero card reads it from the same place every other card does, and only
        # positions it differently.
        'submit_ctas': submit_ctas(request, all_shows),
        'hero_is_current': hero_is_current,
        'current_shows': current_shows,
        'future_shows': display_future_shows,
        'past_shows': past_shows,
        'can_manage_show': manageable_show_ids,
        'can_delete_show': deletable_show_ids,
        'structured_data_json': dump_json_ld(schema_to_dict(gallery_to_schema(request))),
    })


# The four public info pages take no arguments and read everything from `info_site`,
# which the navigation_roles context processor resolves: the site in the URL when there is
# one (/site/<slug>/about/), otherwise the deployment's default site. So the same view
# serves the network page and each venue's, and neither has anything hard-coded in it.
#
# A scoped URL is validated before rendering. The context processor resolves the site from
# the path without checking status, so without this an unpublished venue's info pages would
# be publicly readable at /site/<slug>/about/ even though the venue is hidden everywhere
# else. Same rule as the site-scoped artist and artwork lists.

def contact(request, site_slug=None):
    if site_slug:
        visible_site_or_404(request, site_slug)
    return render(request, 'public/contact.html')


def visit(request, site_slug=None):
    if site_slug:
        visible_site_or_404(request, site_slug)
    return render(request, 'public/visit.html')


def about(request, site_slug=None):
    if site_slug:
        visible_site_or_404(request, site_slug)
    return render(request, 'public/about.html')


def privacy(request, site_slug=None):
    """One policy for the whole network — same code, same database, same providers, so
    per-venue variation would imply differences that do not exist. The venue's own name
    and contact details come from `info_site`, as on the other info pages."""
    if site_slug:
        visible_site_or_404(request, site_slug)
    return render(request, 'public/privacy.html')


def linktree(request, site_slug=None):
    today = dt.date.today()
    if site_slug:
        visible_site_or_404(request, site_slug)
    site = _info_site(request, site_slug)

    shows = Show.objects.all()
    if site is not None:
        shows = shows.filter(sites=site)
    current_shows = list(
        shows.filter(
            status=Show.STATUS_PUBLISHED,
            start__lte=today,
            end__gte=today,
        ).order_by('-start').distinct()
    )
    open_call_shows = list(
        shows.filter(
            status=Show.STATUS_OPEN_CALL,
            submission_type=Show.SUBMISSION_OPEN,
        ).order_by('start').distinct()
    )
    # A venue's own links, plus the ones with no site — those are network-level and
    # belong on every venue's page. Site-first so a venue leads with itself.
    links = LinkTreeEntry.objects.filter(is_active=True)
    if site is not None:
        links = links.filter(Q(site=site) | Q(site__isnull=True))
    custom_links = sorted(links, key=lambda l: (l.site_id is None, l.order, l.name))
    return render(request, 'public/linktree.html', {
        'current_shows': current_shows,
        'open_call_shows': open_call_shows,
        'custom_links': custom_links,
    })


def _info_site(request, site_slug=None):
    """The site whose content a public info page should show.

    Mirrors `info_site` in the context processor, for the one view that needs to *query*
    by it rather than just print it.
    """
    from gallery.models import Site

    slug = site_slug or getattr(settings, 'GALLERY_DEFAULT_SITE_SLUG', None)
    if not slug:
        return None
    return Site.objects.filter(slug=slug, status=Site.STATUS_PUBLISHED).first()


def _reader_role(user):
    """The single role key a reader's documentation is filtered by, or None.

    First match wins, most privileged first — a staff user who is also a curator sees
    the staff documentation, not both.
    """
    if not user.is_authenticated:
        return None
    if is_staff_user(user):
        return 'staff'
    if is_curator_user(user):
        return 'curator'
    if is_juror_user(user):
        return 'juror'
    return 'artist'


def _visible_guides(user_role):
    """The how-to guides this reader may see, in HOW_TO_GUIDES order.

    Shared by the index and the per-guide page so a guide can never be listed but not
    openable, or openable but not listed. `public_only` guides are hidden from signed-in
    readers on purpose: they are the beginner-facing version of a guide that also exists
    in a role-gated form, and the two are mutually exclusive.
    """
    return [
        g for g in HOW_TO_GUIDES
        if (g['roles'] is None and not (g.get('public_only') and user_role))
        or (user_role and g['roles'] and user_role in g['roles'])
    ]


def guide_anchor(guide):
    """The guide's URL segment — its stable anchor, else a slug of the title.

    Not unique across HOW_TO_GUIDES, and deliberately so: the public and signed-in
    submit-artwork guides share one anchor, so a single link and a single URL serve
    either reader. It is unique *per reader*, which is what `_visible_guides` guarantees
    and what makes routing on it safe.
    """
    return guide.get('anchor') or slugify(guide['title'])


# Index headings, keyed by a guide's *audience* rather than by the reader's role.
#
# Grouping by the reader's role puts every guide they can see under one heading — a staff
# reader would find "How to add artworks" filed under "For staff", because staff is one of
# that guide's four roles. What distinguishes guides is how narrow their audience is, so
# that is what the headings say.
HOWTO_AUDIENCE_LABELS = {
    frozenset({'artist', 'curator', 'juror', 'staff'}): 'For anyone signed in',
    frozenset({'curator', 'juror', 'staff'}): 'For jurors and curators',
    frozenset({'curator', 'staff'}): 'For curators and staff',
    frozenset({'staff'}): 'For staff only',
}
HOWTO_PUBLIC_LABEL = 'For everyone'
HOWTO_ROLE_NAMES = {'artist': 'artists', 'curator': 'curators',
                    'juror': 'jurors', 'staff': 'staff'}


def _audience_label(roles):
    """Heading for a guide's audience, general to specific.

    Falls back to naming the roles rather than dropping the guide, so adding a new role
    combination to HOW_TO_GUIDES puts it under a reasonable heading instead of silently
    omitting it from the index — which, now that the index is the only way to find a
    guide, would make it unreachable.
    """
    if not roles:
        return HOWTO_PUBLIC_LABEL
    key = frozenset(roles)
    if key in HOWTO_AUDIENCE_LABELS:
        return HOWTO_AUDIENCE_LABELS[key]
    named = [HOWTO_ROLE_NAMES.get(r, r) for r in sorted(roles)]
    return 'For ' + (', '.join(named[:-1]) + ' and ' + named[-1]
                     if len(named) > 1 else named[0])


def howto(request):
    """Index: every guide this reader can open, grouped by role, with descriptions.

    Purely navigational. The guides moved to their own pages because illustrating all
    31 would have put a few hundred screenshots on one URL; the reference tables moved
    to `howto_reference` so this page stays scannable.
    """
    user_role = _reader_role(request.user)
    visible = _visible_guides(user_role)

    grouped = {}
    for guide in visible:
        grouped.setdefault(_audience_label(guide['roles']), []).append({
            'title': guide['title'],
            'summary': guide.get('summary', ''),
            'anchor': guide_anchor(guide),
        })

    # Widest audience first, so a reader meets the guides most likely to apply to them
    # before the specialised ones. Public guides lead; anything unrecognised sorts last
    # by name rather than jumping the queue.
    order = [HOWTO_PUBLIC_LABEL] + list(HOWTO_AUDIENCE_LABELS.values())
    sections = [{'heading': heading, 'guides': grouped[heading]}
                for heading in order if heading in grouped]
    sections += [{'heading': heading, 'guides': guides}
                 for heading, guides in sorted(grouped.items())
                 if heading not in order]

    return render(request, 'public/howto.html', {
        'sections': sections,
        'user_role': user_role,
    })


def howto_guide(request, anchor):
    """One guide, with its screenshots.

    404s on a guide this reader may not see, rather than rendering it: the visibility
    rules exist so that exactly one of the two submit-artwork versions resolves for any
    given reader, and honouring them here is what keeps that true.
    """
    for guide in _visible_guides(_reader_role(request.user)):
        if guide_anchor(guide) == anchor:
            return render(request, 'public/howto_guide.html', {
                # Copied, not mutated: HOW_TO_GUIDES is module-level state shared across
                # requests. `steps` stays for the walkthrough widget's data-steps.
                'guide': {**guide, 'steps_with_images': steps_with_images(guide),
                          'anchor': anchor},
            })
    raise Http404(f'No how-to guide "{anchor}" is available to you.')


def howto_reference(request):
    """Account Basics plus the form-and-field tables for this reader's role."""
    user_role = _reader_role(request.user)
    return render(request, 'public/howto_reference.html', {
        'general_guide': GENERAL_GUIDE,
        'role_guides': ([ROLE_DOCUMENTATION[user_role]]
                        if user_role in ROLE_DOCUMENTATION else []),
        'user_role': user_role,
    })
