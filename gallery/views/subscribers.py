"""Staff pages for the mailing list itself: who is on it, and acting on one person.

The operational need this exists for is small and unavoidable: somebody emails asking to
be taken off, and there has to be somewhere to do that other than a Django shell. It also
honours the promise the privacy page makes about deletion on request.

Everything here is per-person rather than bulk. Bulk import is `import_subscribers`, bulk
sending is a campaign; a page that could unsubscribe or delete many people at once would be
one misclick away from destroying a list, and no operational need calls for it.

Staff only, like campaigns.
"""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Exists, OuterRef, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from gallery.models import Artist, Site, Subscriber, Subscription
from gallery.models.subscribers import segment_q
from gallery.permissions import is_staff_user

logger = logging.getLogger(__name__)

PER_PAGE = 100


def _staff_only(request):
    if not is_staff_user(request.user):
        raise Http404


def _back(request):
    """Return to the list with the operator's filters intact."""
    query = request.POST.get('back', '')
    url = '/subscribers/'
    return redirect(f'{url}?{query}' if query else url)


@login_required
def subscriber_list(request):
    _staff_only(request)

    search = (request.GET.get('q') or '').strip()
    list_filter = (request.GET.get('list') or '').strip()
    status = (request.GET.get('status') or '').strip()
    segment = (request.GET.get('segment') or '').strip()

    # Annotated rather than left to the property, which would be one query per row: the
    # name matches Subscriber.in_artist_directory, and an annotation shadows the
    # cached_property of the same name.
    people = (Subscriber.objects.prefetch_related('subscriptions__site')
              .annotate(in_artist_directory=Exists(
                  Artist.objects.filter(email__iexact=OuterRef('email')))))

    if segment:
        people = people.filter(segment_q(segment))

    if search:
        people = people.filter(Q(email__icontains=search)
                               | Q(first_name__icontains=search)
                               | Q(last_name__icontains=search))
    if list_filter == 'network':
        people = people.filter(subscriptions__site__isnull=True)
    elif list_filter:
        people = people.filter(subscriptions__site__slug=list_filter)
    if status == 'subscribed':
        people = people.filter(subscriptions__is_subscribed=True)
    elif status == 'unsubscribed':
        # Someone with nothing active. Not "has an inactive row" — a person on two lists
        # who left one is still a subscriber, and showing them under "unsubscribed" would
        # misrepresent that.
        people = people.annotate(
            active=Count('subscriptions', filter=Q(subscriptions__is_subscribed=True))
        ).filter(active=0)

    people = people.distinct().order_by('email')
    page = Paginator(people, PER_PAGE).get_page(request.GET.get('page'))

    # Per-list totals, so the numbers a campaign will send to are visible here too. The
    # deployment's own venue comes first and the network-wide list last: this venue's list is
    # the one being worked with day to day, and the network-wide one is not even sendable yet.
    default = Subscriber.default_site()
    counts = []
    ordered = list(Site.objects.order_by('name'))
    if default is not None:
        ordered = [default] + [s for s in ordered if s.pk != default.pk]
    for site in ordered:
        counts.append({
            'label': site.name, 'value': site.slug,
            'total': Subscription.objects.filter(site=site, is_subscribed=True).count(),
        })
    counts.append({
        'label': 'reset.art (network-wide)', 'value': 'network',
        'total': Subscription.objects.filter(site__isnull=True, is_subscribed=True).count(),
    })

    # Segment sizes across everyone still on a list, so the number a narrowed campaign would
    # reach is visible before anybody writes it.
    on_a_list = Subscriber.objects.filter(subscriptions__is_subscribed=True)
    segments = [{'label': label, 'value': value,
                 'total': on_a_list.filter(segment_q(value)).distinct().count()}
                for value, label in Subscriber.SEGMENT_CHOICES]

    return render(request, 'gallery/subscriber_list.html', {
        'page_obj': page,
        'counts': counts,
        'segments': segments,
        'segment': segment,
        'interest_choices': Subscriber.INTEREST_CHOICES,
        'search': search,
        'list_filter': list_filter,
        'status': status,
        'sites': ordered,
        'default_site': default,
        'query_string': request.GET.urlencode(),
    })


@login_required
@require_POST
def subscriber_interests(request, pk):
    """Record what somebody is, from the staff list.

    Not additive, unlike the subscribe form: an operator who unticks a box means to remove
    it, whereas a subscribe form arrives empty far more often than it means "forget what I
    said". Being in the artist directory is not settable here — it is derived from having an
    artist profile, and a checkbox that silently loses its own value would be worse than no
    checkbox.
    """
    _staff_only(request)
    subscriber = get_object_or_404(Subscriber, pk=pk)
    changed = subscriber.set_interests(request.POST.getlist('interests'), additive=False)
    if changed:
        subscriber.save(update_fields=changed + ['updated_at'])
        logger.info('Subscriber %s interests set to %s', subscriber.email,
                    ', '.join(subscriber.segments))
    messages.success(request, f'{subscriber.email}: '
                              f'{", ".join(subscriber.segment_labels)}.')
    return _back(request)


@login_required
@require_POST
def subscription_unsubscribe(request, pk):
    """Take one person off one list, at their request."""
    _staff_only(request)
    subscription = get_object_or_404(Subscription.objects.select_related('subscriber'), pk=pk)
    if subscription.unsubscribe(reason=Subscription.UNSUB_REQUESTED):
        messages.success(request, f'{subscription.subscriber.email} removed from '
                                  f'{subscription.list_name}.')
    else:
        messages.info(request, f'{subscription.subscriber.email} was already off '
                               f'{subscription.list_name}.')
    return _back(request)


@login_required
@require_POST
def subscription_resubscribe(request, pk):
    """Put someone back, when they ask to be.

    Only ever on request. A bounce or complaint is not something to undo from here just
    because an address looks fixed — that is how a suppression list stops meaning anything.
    """
    _staff_only(request)
    subscription = get_object_or_404(Subscription.objects.select_related('subscriber'), pk=pk)
    Subscription.subscribe(subscription.subscriber, subscription.site,
                           source=Subscription.SOURCE_MANUAL)
    messages.success(request, f'{subscription.subscriber.email} added back to '
                              f'{subscription.list_name}.')
    return _back(request)


@login_required
@require_POST
def subscriber_unsubscribe_all(request, pk):
    _staff_only(request)
    subscriber = get_object_or_404(Subscriber, pk=pk)
    stopped = subscriber.unsubscribe_all(reason=Subscription.UNSUB_REQUESTED)
    messages.success(request, f'{subscriber.email} removed from {stopped} list(s).')
    return _back(request)


@login_required
@require_POST
def subscriber_delete(request, pk):
    """Erase someone entirely, which the privacy page offers on request.

    Deliberately separate from unsubscribing, and the riskier of the two: an unsubscribed
    row is what stops a later import of an old export from mailing them again. Deleting
    removes that protection along with the person, so the template says so before asking.
    """
    _staff_only(request)
    subscriber = get_object_or_404(Subscriber, pk=pk)
    email = subscriber.email
    subscriber.delete()
    logger.info('Subscriber %s deleted by %s', email, request.user)
    messages.success(request, f'{email} deleted. Note that a future import of an old '
                              f'export could add them again — there is no longer a record '
                              f'of their opting out.')
    return _back(request)


@login_required
@require_POST
def subscriber_add(request):
    """Add one person by hand — someone who asked in person or by email.

    Records SOURCE_MANUAL so it is later distinguishable from a signup they made
    themselves, which matters if anyone ever asks where consent came from.
    """
    _staff_only(request)
    email = (request.POST.get('email') or '').strip().lower()
    if not email or '@' not in email:
        messages.error(request, 'That does not look like an email address.')
        return _back(request)

    site = None
    slug = (request.POST.get('site') or '').strip()
    if slug and slug != 'network':
        site = Site.objects.filter(slug=slug).first()
        if site is None:
            messages.error(request, 'No such list.')
            return _back(request)

    existing = Subscriber.objects.filter(email=email).first()
    already = existing and existing.subscriptions.filter(
        site=site, is_subscribed=True).exists()

    Subscriber.opt_in(email=email,
                      first_name=(request.POST.get('first_name') or '').strip(),
                      last_name=(request.POST.get('last_name') or '').strip(),
                      sites=[site], source=Subscription.SOURCE_MANUAL)
    where = site.name if site else 'the network-wide list'
    if already:
        messages.info(request, f'{email} was already on {where}.')
    else:
        messages.success(request, f'{email} added to {where}.')
    return _back(request)
