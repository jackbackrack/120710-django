from eatart.schemaorg.mappers import event_to_schema

from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.urls import reverse_lazy
from django.views.generic import DetailView, ListView
from django.views.generic.edit import CreateView, DeleteView, UpdateView

from gallery.forms import EventForm
from gallery.models import Event, Tag
from gallery.permissions import can_manage_event, is_staff_user, tag_filter_queryset
from gallery.views.mixins import CanonicalSlugRedirectMixin, StructuredDataMixin


class EventListView(ListView):
    model = Event
    template_name = 'gallery/event_list.html'

    def get_queryset(self):
        return tag_filter_queryset(Event.objects.select_related('show').prefetch_related('show__curators'), self.request.GET.get('tag')).distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        events = context.get('event_list', [])
        context['available_tags'] = Tag.objects.order_by('name')
        context['active_tag'] = self.request.GET.get('tag', '')
        context['manageable_event_ids'] = {
            event.id for event in events if can_manage_event(self.request.user, event)
        }
        return context


class EventDetailView(CanonicalSlugRedirectMixin, StructuredDataMixin, DetailView):
    model = Event
    schema_mapper = event_to_schema
    template_name = 'gallery/event_detail.html'

    def get_queryset(self):
        return (
            Event.objects
            .select_related('show')
            .prefetch_related(
                'show__curators',
                'show__artworks__artists',
                'show__artworks__shows',
            )
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['can_manage_event'] = can_manage_event(self.request.user, self.object)
        return context


class EventUpdateView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = Event
    form_class = EventForm
    template_name = 'gallery/event_edit.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def test_func(self):
        obj = self.get_object()
        return can_manage_event(self.request.user, obj)


class EventDeleteView(LoginRequiredMixin, UserPassesTestMixin, DeleteView):
    model = Event
    template_name = 'gallery/event_delete.html'
    success_url = reverse_lazy('gallery:event_list')

    def test_func(self):
        obj = self.get_object()
        return can_manage_event(self.request.user, obj)


class EventCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
    model = Event
    form_class = EventForm
    template_name = 'gallery/event_new.html'

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['user'] = self.request.user
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        show_pk = self.request.GET.get('show')
        if show_pk:
            from gallery.models import Show
            try:
                initial['show'] = Show.objects.get(pk=show_pk)
            except Show.DoesNotExist:
                pass
        return initial

    def test_func(self):
        from gallery.permissions import is_curator_user
        return is_curator_user(self.request.user)
