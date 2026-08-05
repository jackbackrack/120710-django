from eatart.schemaorg.mappers import artwork_to_schema

import logging
import threading
import time

from django.core import signing

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.mail import EmailMultiAlternatives
from django.db import connection
from django.http import HttpResponse, JsonResponse
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.views.generic import DetailView, ListView
from django.views.generic.edit import CreateView, DeleteView, UpdateView

from django.db.models import Max

from honeypot.decorators import check_honeypot

from gallery import create_tokens
from gallery.forms import ArtworkForm, ArtworkImageFormSet, ArtworkInquiryForm
from gallery.models import Artwork, ArtworkImage, Tag
from gallery.permissions import can_delete_artwork, can_manage_artwork, is_artist_user, is_curator_user, is_staff_user, tag_filter_queryset, visible_artwork_queryset, is_site_director
from gallery.views.mixins import (CanonicalSlugRedirectMixin, StructuredDataMixin,
                                 visible_site_or_404)

logger = logging.getLogger(__name__)


def detail(request, pk):
    artwork = get_object_or_404(Artwork.objects.filter(visible_artwork_queryset(request.user)).distinct(), pk=pk)
    return render(request, 'gallery/artwork_detail.html', {'artwork': artwork})


class ArtworkListView(ListView):
    """Every artwork, or — at /site/<slug>/artworks/ — what has been shown at one venue.

    Site-scoped through the same view and template as the network list; see
    ArtistListView for why the separate scoped view it replaces was a liability.
    """
    model = Artwork
    context_object_name = 'artwork_list'
    template_name = 'gallery/artwork_list.html'
    paginate_by = 48
    site = None

    def get_template_names(self):
        # Infinite scroll fetches subsequent pages and appends just the cards.
        if self.request.GET.get('partial'):
            return ['gallery/_artwork_cards.html']
        return [self.template_name]

    def get_queryset(self):
        queryset = Artwork.objects.filter(visible_artwork_queryset(self.request.user)).prefetch_related('artists', 'shows', 'shows__curators').distinct()
        site_slug = self.kwargs.get('site_slug')
        if site_slug:
            self.site = visible_site_or_404(self.request, site_slug)
            queryset = queryset.filter(shows__sites=self.site)
        return tag_filter_queryset(queryset, self.request.GET.get('tag')).distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        artworks = list(context['object_list'])
        context['artwork_list'] = artworks
        context['available_tags'] = Tag.objects.order_by('name')
        context['active_tag'] = self.request.GET.get('tag', '')
        user = self.request.user
        context['can_manage_artwork'] = {
            a.id for a in artworks if can_manage_artwork(user, a)
        }
        context['can_delete_artwork'] = {
            a.id for a in artworks if can_delete_artwork(user, a)
        }
        context['site'] = self.site
        context['anon_grid_cache_seconds'] = settings.ANON_GRID_CACHE_SECONDS
        return context


class ArtworkDetailView(CanonicalSlugRedirectMixin, StructuredDataMixin, DetailView):
    model = Artwork
    context_object_name = 'artwork'
    schema_mapper = artwork_to_schema
    template_name = 'gallery/artwork_detail.html'

    def get_context_data(self, **kwargs):
        from gallery.permissions import can_delete_show, can_manage_show
        from gallery.models import Artist
        context = super().get_context_data(**kwargs)
        artwork = self.object
        context['can_manage_artwork'] = can_manage_artwork(self.request.user, artwork)
        context['can_delete_artwork'] = can_delete_artwork(self.request.user, artwork)
        shows = list(artwork.shows.all())
        context['can_manage_show'] = {s.id for s in shows if can_manage_show(self.request.user, s)}
        context['can_delete_show'] = {s.id for s in shows if can_delete_show(self.request.user, s)}
        # Inquiries are open to everyone (no account needed); spam is handled by
        # reCAPTCHA + honeypot + time-trap + rate limiting on the inquiry view.
        context['can_inquire'] = (
            artwork.artists.filter(email__isnull=False).exclude(email='').exists()
        )
        context['blind_artist'] = self.request.GET.get('blind') == '1'
        user = self.request.user
        from gallery.models.collection import CollectionPiece
        context['collection_pieces'] = (
            artwork.collection_pieces
            .filter(status__in=[CollectionPiece.STATUS_CONFIRMED, CollectionPiece.STATUS_PENDING])
            .select_related('collector')
            .prefetch_related('collector__artists')
            .order_by('status', '-created_at')
        )
        context['user_collection_piece'] = (
            artwork.collection_pieces.filter(collector=user).first()
            if user.is_authenticated else None
        )
        context['is_own_artwork'] = (
            user.is_authenticated
            and Artist.objects.filter(user=user, artworks=artwork).exists()
        )
        context['can_confirm_piece'] = (
            context['is_own_artwork'] or (user.is_authenticated and is_curator_user(user))
        )
        # Staff/admins can remove any collection claim (e.g. an artist who
        # mistakenly claimed their own work).
        context['can_remove_any_piece'] = is_staff_user(user)
        return context

    def get_queryset(self):
        return Artwork.objects.filter(visible_artwork_queryset(self.request.user)).prefetch_related('shows__curators').distinct()


class ArtworkUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Artwork
    form_class = ArtworkForm
    template_name = 'gallery/artwork_edit.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if 'image_formset' not in context:
            context['image_formset'] = ArtworkImageFormSet(instance=self.object)
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        form = self.get_form()
        image_formset = ArtworkImageFormSet(request.POST, request.FILES, instance=self.object)
        if form.is_valid() and image_formset.is_valid():
            response = self.form_valid(form)
            image_formset.save()
            self._renumber_supplemental_images()
            return response
        return self.render_to_response(
            self.get_context_data(form=form, image_formset=image_formset)
        )

    def _renumber_supplemental_images(self):
        self.object.supplemental_images.filter(image='').delete()
        images = list(self.object.supplemental_images.order_by('order', 'pk'))
        for i, img in enumerate(images, start=1):
            if img.order != i:
                img.order = i
                img.save(update_fields=['order'])

    def form_valid(self, form):
        response = super().form_valid(form)
        # Only link the editing artist to the artwork for non-staff; staff editing
        # an artwork should not inadvertently add themselves as a co-artist.
        if not is_staff_user(self.request.user) and self.request.user.is_authenticated:
            artist = self.request.user.artists.order_by('-created_at').first()
            if artist:
                self.object.artists.add(artist)
        return response

    def test_func(self):
        obj = self.get_object()
        return can_manage_artwork(self.request.user, obj)


class ArtworkDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Artwork
    template_name = 'gallery/artwork_delete.html'
    success_url = reverse_lazy('gallery:artwork_list')

    def test_func(self):
        obj = self.get_object()
        return can_delete_artwork(self.request.user, obj)


def _warn_if_identical(request, artwork):
    """Say so when a new piece matches one the artist already has, exactly.

    A warning and not a refusal, deliberately. "Distinct enough" is the wrong test for
    artwork: untitled work is ordinary, series work differs by a millimetre or not at all,
    and an emerging-artist gallery sees more of both than most. A rule strict enough to
    catch a real duplicate refuses real work, and the cost of that lands on the artist while
    the cost of a missed duplicate is a row somebody deletes.

    So this matches on everything at once — title, year, medium and both dimensions — which
    is rare enough to be worth mentioning and never worth blocking on.
    """
    if not artwork.pk:
        return
    twin = (Artwork.objects
            .filter(artists__in=artwork.artists.all())
            .filter(name__iexact=(artwork.name or '').strip(),
                    end_year=artwork.end_year,
                    medium=artwork.medium,
                    width_inches=artwork.width_inches,
                    height_inches=artwork.height_inches)
            .exclude(pk=artwork.pk)
            .distinct()
            .first())
    if twin is None:
        return
    messages.warning(
        request,
        f'Heads up: “{twin.name}” already has the same title, year, medium and size. '
        f'If this was meant to be one piece rather than two, you can delete either.')


class ArtworkCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    """Creating an artwork, once per rendered form.

    An artist reported duplicates they had not meant to make. Uploading a photograph takes
    seconds with nothing on screen to say so, and a second click makes a second artwork —
    as does a back-button resubmit, or a request the browser retried.

    The form carries a random token and the column is unique, so the second save collides in
    the database and is turned into a redirect to the artwork that already exists. Done here
    rather than only in JavaScript because a back-button resubmit is a fresh page with no
    memory of the first, which no script on the page can see.
    """

    model = Artwork
    form_class = ArtworkForm
    template_name = 'gallery/artwork_new.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Regenerated per render, so a fresh form is always a fresh create and only a
        # *replay* of one submission is refused.
        context.setdefault('create_token', create_tokens.new_token())
        return context

    def form_valid(self, form):
        token = create_tokens.token_from(self.request)
        existing, is_replay = create_tokens.prior_for(
            Artwork, token, form.cleaned_data, create_tokens.ARTWORK_FIELDS)
        if is_replay:
            # Not an error: they pressed Save twice, or came back and resubmitted. They
            # wanted one artwork and they have one, so show it to them.
            messages.info(self.request, 'That was already saved — here it is.')
            return redirect(existing)
        form.instance.create_token = create_tokens.token_to_store(existing, is_replay, token)

        form.instance.created_by = self.request.user
        try:
            with transaction.atomic():
                response = super().form_valid(form)
        except IntegrityError:
            # Two requests raced past the check above and one lost. The winner's artwork is
            # the answer; this one never existed.
            clash = Artwork.objects.filter(create_token=token).first() if token else None
            if clash is None:
                raise
            return redirect(clash)

        artist = self.request.user.artists.order_by('-created_at').first()
        if artist:
            self.object.artists.add(artist)
        _warn_if_identical(self.request, self.object)
        return response

    def test_func(self):
        return (is_artist_user(self.request.user) or is_staff_user(self.request.user)
                or is_site_director(self.request.user))


def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '') or 'unknown'


def _rate_limited(key, limit, window):
    """Best-effort fixed-window rate limit. NOTE: the default cache is
    per-process (LocMemCache), so the effective ceiling scales with the number
    of gunicorn workers; a shared cache (Redis) turns this into a hard limit."""
    if cache.add(key, 1, window):
        return False
    try:
        return cache.incr(key) > limit
    except ValueError:
        cache.set(key, 1, window)
        return False


def _send_inquiry_email_async(subject, body, html, recipient_emails, reply_to):
    """Send the inquiry off the request thread so a POST never ties up a worker
    on SMTP latency (which is what let a flood exhaust all workers)."""
    def _run():
        try:
            msg = EmailMultiAlternatives(
                subject=subject, body=body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=recipient_emails, reply_to=reply_to,
            )
            msg.attach_alternative(html, 'text/html')
            msg.send()
            logger.info('Sent artwork inquiry to %s', recipient_emails)
        except Exception:
            logger.exception('Failed to send artwork inquiry to %s', recipient_emails)
        finally:
            connection.close()
    threading.Thread(target=_run, daemon=True).start()


_INQUIRY_MIN_FILL_SECONDS = 3      # a human takes longer than this to fill the form
_INQUIRY_TOKEN_MAX_AGE = 2 * 60 * 60


def _inquiry_too_fast(request):
    """Time-trap: the form embeds a signed timestamp; reject a POST that arrives
    implausibly fast, or with a missing/forged/stale token (bot signatures)."""
    try:
        started = float(signing.loads(request.POST.get('ts', ''), max_age=_INQUIRY_TOKEN_MAX_AGE))
    except Exception:
        return True
    return (time.time() - started) < _INQUIRY_MIN_FILL_SECONDS


@check_honeypot
def artwork_inquire(request, pk):
    ip = _client_ip(request)
    # Cheap throttle on every hit (defense-in-depth alongside login).
    if _rate_limited(f'inq-req:{ip}', 20, 60):
        logger.warning('Throttled inquiry requests from %s', ip)
        return HttpResponse('Too many requests — please try again in a minute.', status=429)

    artwork = get_object_or_404(
        Artwork.objects.filter(visible_artwork_queryset(request.user)).distinct(),
        pk=pk,
    )
    recipient_emails = [a.email for a in artwork.artists.all() if a.email]
    if not recipient_emails:
        messages.error(request, 'No contact email is available for this artwork.')
        return redirect(artwork.get_absolute_url())

    if request.method == 'POST':
        # Stricter cap on actual sends — nobody legitimately sends many a minute.
        if _rate_limited(f'inq-send:{ip}', 5, 600):
            logger.warning('Throttled inquiry sends from %s', ip)
            return HttpResponse('Too many inquiries — please try again later.', status=429)
        form = ArtworkInquiryForm(request.POST)
        if _inquiry_too_fast(request):
            logger.warning('Inquiry rejected (too fast / bad time-trap token) from %s', ip)
            messages.error(request, 'Your message was submitted too quickly — please try again.')
        elif form.is_valid():
            sender_name = form.cleaned_data['sender_name']
            sender_email = form.cleaned_data['sender_email']
            message_text = form.cleaned_data['message']
            artwork_url = request.build_absolute_uri(artwork.get_absolute_url())
            html = render_to_string('email/artwork_inquiry.html', {
                'artwork': artwork,
                'sender_name': sender_name,
                'sender_email': sender_email,
                'message': message_text,
                'artwork_url': artwork_url,
            })
            body = (
                f'{sender_name} ({sender_email}) sent an inquiry about '
                f'"{artwork.name}":\n\n{message_text}\n\n{artwork_url}'
            )
            _send_inquiry_email_async(
                f'Inquiry about "{artwork.name}"', body, html,
                recipient_emails, [f'{sender_name} <{sender_email}>'],
            )
            messages.success(request, 'Your inquiry has been sent to the artist.')
            return redirect(artwork.get_absolute_url())
    else:
        initial = {}
        if request.user.is_authenticated:
            full_name = request.user.get_full_name()
            if full_name:
                initial['sender_name'] = full_name
            initial['sender_email'] = request.user.email
        form = ArtworkInquiryForm(initial=initial)

    return render(request, 'gallery/artwork_inquire.html', {
        'artwork': artwork,
        'form': form,
        'ts_token': signing.dumps(time.time()),   # fresh time-trap token per render
    })


@login_required
def artwork_add_image(request, pk):
    artwork = get_object_or_404(Artwork, pk=pk)
    if not can_manage_artwork(request.user, artwork):
        raise PermissionDenied
    if request.method == 'POST':
        image = request.FILES.get('image')
        if image:
            if image.size > 50 * 1024 * 1024:
                messages.error(request, 'Image file too large — maximum size is 50 MB.')
            else:
                next_order = (artwork.supplemental_images.aggregate(Max('order'))['order__max'] or 0) + 1
                ArtworkImage.objects.create(artwork=artwork, image=image, order=next_order)
    return redirect(artwork.get_absolute_url())


@login_required
def artwork_reorder_images(request, pk):
    import json
    artwork = get_object_or_404(Artwork, pk=pk)
    if not can_manage_artwork(request.user, artwork):
        raise PermissionDenied
    if request.method != 'POST':
        raise PermissionDenied
    try:
        image_ids = json.loads(request.body)['image_ids']
    except (KeyError, ValueError):
        return JsonResponse({'ok': False, 'error': 'bad request'}, status=400)
    for order, img_id in enumerate(image_ids, start=1):
        ArtworkImage.objects.filter(pk=img_id, artwork=artwork).update(order=order)
    return JsonResponse({'ok': True})


@login_required
def artwork_image_delete(request, pk):
    img = get_object_or_404(ArtworkImage, pk=pk)
    if not can_manage_artwork(request.user, img.artwork):
        raise PermissionDenied
    if request.method == 'POST':
        artwork = img.artwork
        img.delete()
        return redirect(artwork.get_absolute_url())
    raise PermissionDenied
