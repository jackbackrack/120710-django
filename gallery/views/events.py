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
        return tag_filter_queryset(Event.objects.select_related('show', 'managing_curator'), self.request.GET.get('tag')).distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['available_tags'] = Tag.objects.order_by('name')
        context['active_tag'] = self.request.GET.get('tag', '')
        return context


class EventDetailView(CanonicalSlugRedirectMixin, StructuredDataMixin, DetailView):
    model = Event
    schema_mapper = event_to_schema
    template_name = 'gallery/event_detail.html'


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

    def form_valid(self, form):
        if not form.instance.managing_curator_id:
            form.instance.managing_curator = form.cleaned_data['show'].managing_curator
        return super().form_valid(form)

    def test_func(self):
        return is_staff_user(self.request.user)
