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
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from gallery import campaigns as engine
from gallery.forms import CampaignForm
from gallery.models import Campaign
from gallery.permissions import is_staff_user

logger = logging.getLogger(__name__)


def _staff_only(request):
    if not is_staff_user(request.user):
        raise Http404


@login_required
def campaign_list(request):
    _staff_only(request)
    return render(request, 'gallery/campaign_list.html', {
        'campaigns': Campaign.objects.select_related('site', 'created_by'),
    })


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
    return render(request, 'gallery/campaign_edit.html', {'form': form, 'campaign': None})


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
        'test_address': request.user.email,
    })


@login_required
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
def campaign_send(request, pk):
    _staff_only(request)
    campaign = get_object_or_404(Campaign, pk=pk)

    # The engine refuses too; this is only so the page can say why in words. Never rely on
    # the button being hidden — a stale tab would post anyway.
    if not campaign.can_send:
        messages.error(request, campaign.blocked_reason or 'This campaign cannot be sent.')
        return redirect('gallery:campaign_edit', pk=campaign.pk)

    try:
        sent = engine.send_campaign(campaign, request=request)
    except Exception as exc:   # noqa: BLE001
        logger.exception('Campaign %s send failed', campaign.pk)
        messages.error(request, f'Send failed: {exc}')
    else:
        messages.success(request, f'Sent to {sent} subscriber{"s" if sent != 1 else ""}.')
    return redirect('gallery:campaign_edit', pk=campaign.pk)
