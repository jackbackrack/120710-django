from eatart.schemaorg.mappers import artwork_to_schema

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import PermissionDenied
from django.core.mail import EmailMultiAlternatives
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse_lazy
from django.views.generic import DetailView, ListView
from django.views.generic.edit import CreateView, DeleteView, UpdateView

from django.db.models import Max

from gallery.forms import ArtworkForm, ArtworkImageFormSet, ArtworkInquiryForm
from gallery.models import Artwork, ArtworkImage, Tag
from gallery.permissions import can_delete_artwork, can_manage_artwork, is_artist_user, is_curator_user, is_staff_user, tag_filter_queryset, visible_artwork_queryset
from gallery.views.mixins import CanonicalSlugRedirectMixin, StructuredDataMixin


def detail(request, pk):
    artwork = get_object_or_404(Artwork.objects.filter(visible_artwork_queryset(request.user)).distinct(), pk=pk)
    return render(request, 'gallery/artwork_detail.html', {'artwork': artwork})


class ArtworkListView(ListView):
    model = Artwork
    context_object_name = 'artwork_list'
    template_name = 'gallery/artwork_list.html'
    paginate_by = 100

    def get_queryset(self):
        queryset = Artwork.objects.filter(visible_artwork_queryset(self.request.user)).prefetch_related('artists', 'shows', 'shows__curators').distinct()
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
        context['can_inquire'] = artwork.artists.filter(email__isnull=False).exclude(email='').exists()
        context['blind_artist'] = self.request.GET.get('blind') == '1'
        user = self.request.user
        context['can_manage_collection'] = (
            user.is_authenticated and (is_staff_user(user) or is_curator_user(user))
        )
        context['collected_by'] = artwork.collected_by.order_by('name')
        context['all_artists'] = Artist.objects.exclude(artworks=artwork).exclude(collection=artwork).order_by('name')
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


class ArtworkCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Artwork
    form_class = ArtworkForm
    template_name = 'gallery/artwork_new.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        artist = self.request.user.artists.order_by('-created_at').first()
        if artist:
            self.object.artists.add(artist)
        return response

    def test_func(self):
        return is_artist_user(self.request.user)


def artwork_inquire(request, pk):
    artwork = get_object_or_404(
        Artwork.objects.filter(visible_artwork_queryset(request.user)).distinct(),
        pk=pk,
    )
    recipient_emails = [a.email for a in artwork.artists.all() if a.email]
    if not recipient_emails:
        messages.error(request, 'No contact email is available for this artwork.')
        return redirect(artwork.get_absolute_url())

    if request.method == 'POST':
        form = ArtworkInquiryForm(request.POST)
        if form.is_valid():
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
            email = EmailMultiAlternatives(
                subject=f'Inquiry about "{artwork.name}"',
                body=(
                    f'{sender_name} ({sender_email}) sent an inquiry about '
                    f'"{artwork.name}":\n\n{message_text}\n\n{artwork_url}'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=recipient_emails,
                reply_to=[f'{sender_name} <{sender_email}>'],
            )
            email.attach_alternative(html, 'text/html')
            email.send()
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
