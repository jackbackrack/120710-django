from eatart.schemaorg.mappers import dump_json_ld, schema_to_dict

from django.shortcuts import get_object_or_404
from django.shortcuts import redirect
from django.urls import reverse


class SlugOrPkLookupMixin:
    slug_field = 'slug'
    slug_url_kwarg = 'slug'

    def get_object(self, queryset=None):
        queryset = queryset or self.get_queryset()
        pk = self.kwargs.get(self.pk_url_kwarg)
        if pk is not None:
            return get_object_or_404(queryset, pk=pk)

        slug = self.kwargs.get(self.slug_url_kwarg)
        if slug is None:
            return super().get_object(queryset)

        if slug.isdigit():
            return get_object_or_404(queryset, pk=int(slug))

        return get_object_or_404(queryset, **{self.slug_field: slug})


class CanonicalSlugRedirectMixin(SlugOrPkLookupMixin):
    canonical_url_name = None

    def get_canonical_url(self, obj):
        if self.canonical_url_name:
            return reverse(self.canonical_url_name, kwargs={'slug': obj.slug})
        return obj.get_absolute_url()

    def get(self, request, *args, **kwargs):
        if self.kwargs.get(self.pk_url_kwarg) is not None:
            obj = self.get_object()
            return redirect(self.get_canonical_url(obj))
        return super().get(request, *args, **kwargs)


class StructuredDataMixin:
    schema_mapper = None

    def get_schema_object(self):
        mapper = self.__class__.schema_mapper
        if mapper is None:
            return None
        return mapper(self.object, self.request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        schema_object = self.get_schema_object()
        if schema_object is not None:
            context['structured_data_json'] = dump_json_ld(schema_to_dict(schema_object))
        return context


def visible_site_or_404(request, slug):
    """A venue by slug, hiding drafts from everyone but staff.

    Shared by the site-scoped artist and artwork lists. Deliberately stricter than
    ShowListView's plain get_object_or_404: those two URLs already behaved this way before
    they were folded into the network list views, and loosening it would quietly make an
    unpublished venue publicly browsable.
    """
    from gallery.models import Site
    from gallery.permissions import is_staff_user

    qs = Site.objects.all()
    if not (request.user.is_authenticated and is_staff_user(request.user)):
        qs = qs.filter(status=Site.STATUS_PUBLISHED)
    return get_object_or_404(qs, slug=slug)
