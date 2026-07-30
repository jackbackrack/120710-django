"""Staff pages for composing, previewing, testing and sending a campaign.

The engine is in gallery/campaigns.py; this is only the surface. It deliberately puts
compose, preview, test and send on one page, because the send guard is a sequence — a
test must post-date the last edit — and splitting those across pages hides where you
are in it. `Campaign.blocked_reason` is rendered verbatim, so the page never says "you
can't send" without saying why.

Staff only. Campaigns go to everyone on a list, and there is no undo.
"""
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST

from gallery import campaigns as engine
from gallery.forms import CampaignForm
from gallery.models import Campaign, Show, Site
from gallery.permissions import is_staff_user

logger = logging.getLogger(__name__)


def _staff_only(request):
    if not is_staff_user(request.user):
        raise Http404


def _template_subjects():
    """The default subject for each template, as JSON for the page to fill in with."""
    import json
    return json.dumps({name: spec.get('subject', '')
                       for name, spec in engine.CAMPAIGN_TEMPLATES.items()})


def _percent(campaign):
    """How far a send has got, for the progress bar. Zero rather than a division error."""
    done = campaign.sent_so_far
    total = done + campaign.remaining_count
    return round(100 * done / total) if total else 0


@login_required
def campaign_list(request):
    _staff_only(request)
    # Counted in the query rather than per row: the list is every campaign ever sent, and three
    # property lookups each would be a page of queries by the second year.
    campaigns = (Campaign.objects
                 .select_related('site', 'created_by')
                 .annotate(
                     n_sent=Count('deliveries', filter=Q(deliveries__status='sent')),
                     n_bounced=Count('deliveries', filter=Q(deliveries__outcome='bounced')),
                     n_complained=Count('deliveries',
                                        filter=Q(deliveries__outcome='complained'))))
    return render(request, 'gallery/campaign_list.html', {
        'campaigns': [_with_rates(c) for c in campaigns],
        'complaint_limit': Campaign.COMPLAINT_RATE_LIMIT,
        'template_subjects': _template_subjects(),
    })


def _with_rates(campaign):
    """Rates from the annotated counts, so the template does not query per row."""
    sent = campaign.n_sent
    campaign.complaint_pct = round(100 * campaign.n_complained / sent, 2) if sent else 0.0
    campaign.bounce_pct = round(100 * campaign.n_bounced / sent, 2) if sent else 0.0
    campaign.complaints_high = campaign.complaint_pct > Campaign.COMPLAINT_RATE_LIMIT
    return campaign


@login_required
def campaign_new(request):
    _staff_only(request)
    if request.method == 'POST':
        form = CampaignForm(request.POST)
        if form.is_valid():
            campaign = form.save(commit=False)
            campaign.created_by = request.user
            campaign.save()
            messages.success(request, 'Draft created. Preview it, then send yourself a test.')
            return redirect('gallery:campaign_edit', pk=campaign.pk)
    else:
        form = CampaignForm()
    return render(request, 'gallery/campaign_edit.html', {
        'form': form, 'campaign': None, 'template_subjects': _template_subjects()})


@login_required
def campaign_edit(request, pk):
    _staff_only(request)
    campaign = get_object_or_404(Campaign, pk=pk)

    # A sent campaign is a record of what went out. Editing it would rewrite history and
    # tell you nothing about the mail people actually received.
    editable = campaign.status == Campaign.STATUS_DRAFT

    if request.method == 'POST' and editable:
        form = CampaignForm(request.POST, instance=campaign)
        if form.is_valid():
            form.save()   # Campaign.save() re-arms the send guard if content changed
            messages.success(request, 'Saved.')
            return redirect('gallery:campaign_edit', pk=campaign.pk)
    else:
        form = CampaignForm(instance=campaign)

    return render(request, 'gallery/campaign_edit.html', {
        'form': form,
        'campaign': campaign,
        'editable': editable,
        'recipient_count': engine.recipients(campaign).count(),
        'sent_so_far': campaign.sent_so_far,
        'remaining_count': campaign.remaining_count,
        # A send in flight is not finished, and the page has to be able to say so without
        # the operator reloading by hand and guessing.
        'in_flight': campaign.status == Campaign.STATUS_SENDING and not campaign.is_stalled,
        'send_percent': _percent(campaign),
        'rejected': campaign.rejected,
        'complaint_limit': Campaign.COMPLAINT_RATE_LIMIT,
        'test_address': request.user.email,
    })


@login_required
# Django defaults X_FRAME_OPTIONS to DENY, which blocks framing even from the same origin — so
# without this the iframe shows the browser's "refused to connect", which reads like the server
# being down rather than a header. Relaxed per-view rather than site-wide: everything else on the
# site should still refuse to be framed.
@xframe_options_sameorigin
def campaign_preview(request, pk):
    """The compiled email, served bare for an <iframe>.

    Not embedded in the page: this is a full HTML document with its own styles, and
    inlining it would let email CSS loose on the admin page.
    """
    _staff_only(request)
    campaign = get_object_or_404(Campaign, pk=pk)
    try:
        html = engine.render_preview(campaign, request=request)
    except Exception as exc:   # noqa: BLE001 — a broken template must not 500 the editor
        logger.exception('Campaign %s preview failed', campaign.pk)
        return HttpResponse(
            f'<p style="font-family:sans-serif;color:#b00">This campaign does not render '
            f'yet:</p><pre style="white-space:pre-wrap">{exc}</pre>',
            status=200, content_type='text/html')
    return HttpResponse(html)


@login_required
@xframe_options_sameorigin   # See campaign_preview: DENY is the default and blocks same-origin.
def campaign_template_preview(request):
    """The compiled email for a template and show that have not been saved to anything yet.

    Exists because choosing a template used to show nothing at all until you had committed to a
    draft: the template is not copied into the body field — it *is* the body — so the form looked
    like it had done nothing. Being asked to save before you can see what you picked is a poor
    trade for a decision that is mostly visual.

    Renders an unsaved Campaign, so it writes nothing. Bare HTML for an iframe, like
    campaign_preview.
    """
    _staff_only(request)

    site = Site.objects.filter(pk=request.GET.get('site')).first()
    show = Show.objects.filter(pk=request.GET.get('show')).first()
    draft = Campaign(
        site=site,
        show=show,
        subject=request.GET.get('subject') or 'Subject goes here',
        preheader=request.GET.get('preheader') or '',
        template_name=request.GET.get('template') or '',
        body_markdown=request.GET.get('body') or '',
    )

    # The subject resolves server-side too, so the page shows what will really be sent rather
    # than a second implementation of the same rendering written in JavaScript.
    if request.GET.get('subject_only'):
        return HttpResponse(engine.render_subject(draft, request=request),
                            content_type='text/plain; charset=utf-8')

    if draft.template_name and 'show' in engine.template_needs(draft.template_name) and not show:
        return HttpResponse(
            '<p style="font-family:sans-serif;color:#666;padding:1rem">This template takes its '
            'content from a show. Choose one and the preview will fill in.</p>',
            content_type='text/html')
    if not draft.template_name and not draft.body_markdown.strip():
        return HttpResponse(
            '<p style="font-family:sans-serif;color:#666;padding:1rem">Choose a template, or '
            'write something in the body, and it will appear here.</p>',
            content_type='text/html')

    try:
        html = engine.render_preview(draft, request=request)
    except Exception as exc:   # noqa: BLE001 — a broken template must not 500 the editor
        logger.exception('Template preview failed for %r', draft.template_name)
        return HttpResponse(
            f'<p style="font-family:sans-serif;color:#b00">This does not render yet:</p>'
            f'<pre style="white-space:pre-wrap">{exc}</pre>', content_type='text/html')
    return HttpResponse(html)


@login_required
@require_POST
def campaign_send_test(request, pk):
    _staff_only(request)
    campaign = get_object_or_404(Campaign, pk=pk)
    address = (request.POST.get('address') or request.user.email or '').strip()
    if not address:
        messages.error(request, 'No address to send the test to.')
        return redirect('gallery:campaign_edit', pk=campaign.pk)
    try:
        engine.send_test(campaign, address, request=request)
    except Exception as exc:   # noqa: BLE001 — surface the provider's complaint
        logger.exception('Campaign %s test send failed', campaign.pk)
        messages.error(request, f'Test failed: {exc}')
    else:
        messages.success(request, f'Test sent to {address}. Check how it looks, then send.')
    return redirect('gallery:campaign_edit', pk=campaign.pk)


@login_required
@require_POST
def campaign_duplicate(request, pk):
    """Start a fresh draft from an existing campaign.

    This is what "save as template" is usually reaching for: not a new format, but next month's
    mailing started from last month's. The recurring *structure* is already a template file, so
    what was missing was only the copy step.

    Everything a reader would see is carried over, and everything about the send is not: no
    status, no sent date, no delivery records, and — deliberately — no test. A duplicate has
    never been tested, whatever its original had done, so the guard re-arms and the copy has to
    be looked at before it can go anywhere.

    The subject is copied verbatim rather than prefixed with "Copy of". A prefix would be a
    scaffolding word one forgotten edit away from arriving in nine hundred inboxes, and the
    campaign list already tells drafts from sent ones.
    """
    _staff_only(request)
    original = get_object_or_404(Campaign, pk=pk)

    copy = Campaign.objects.create(
        site=original.site,
        show=original.show,
        subject=original.subject,
        preheader=original.preheader,
        template_name=original.template_name,
        body_markdown=original.body_markdown,
        created_by=request.user,
    )
    logger.info('Campaign %s duplicated from %s by %s', copy.pk, original.pk, request.user)
    messages.success(request, 'Copied to a new draft. Nothing has been sent, and it counts as '
                              'untested — send yourself a test once you have made your changes.')
    return redirect('gallery:campaign_edit', pk=copy.pk)


@login_required
@require_POST
def campaign_send(request, pk):
    _staff_only(request)
    campaign = get_object_or_404(Campaign, pk=pk)

    # The engine refuses too; this is only so the page can say why in words. Never rely on
    # the button being hidden — a stale tab would post anyway.
    if not campaign.can_send:
        messages.error(request, campaign.blocked_reason or 'This campaign cannot be sent.')
        return redirect('gallery:campaign_edit', pk=campaign.pk)

    return _begin(request, campaign, resume=False)


@login_required
@require_POST
def campaign_resume(request, pk):
    """Finish a send that stopped part-way.

    Separate from Send rather than a mode of it, so the button an operator reaches for after
    something went wrong says what it will do. It sends only to people with no delivery
    record, so pressing it when the send had in fact finished mails nobody.
    """
    _staff_only(request)
    campaign = get_object_or_404(Campaign, pk=pk)

    if not campaign.can_resume:
        messages.error(request, 'That campaign is not stopped part-way, so there is nothing '
                                'to resume.')
        return redirect('gallery:campaign_edit', pk=campaign.pk)

    return _begin(request, campaign, resume=True)


def _begin(request, campaign, resume):
    """Hand the send to a background thread and report back immediately.

    The response cannot say how many were sent, because by design the send has barely
    started — the alternative was holding the request open for minutes and timing out. The
    page reports progress from the delivery records instead.
    """
    owed = campaign.remaining_count
    try:
        engine.start_send(campaign, resume=resume)
    except Exception as exc:   # noqa: BLE001 — a refused claim, or the thread not starting
        logger.exception('Campaign %s could not be started', campaign.pk)
        messages.error(request, f'Could not start the send: {exc}')
    else:
        verb = 'Resuming' if resume else 'Sending'
        messages.success(
            request,
            f'{verb} to {owed} subscriber{"s" if owed != 1 else ""}. This page updates as it '
            f'goes — you can leave it, the send does not need the page open.')
    return redirect('gallery:campaign_edit', pk=campaign.pk)
