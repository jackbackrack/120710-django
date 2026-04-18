from django.contrib import admin
from import_export.admin import ImportExportModelAdmin

from gallery.models import Artist, Artwork, Event, Show, Tag


class ImportExportAdmin(ImportExportModelAdmin, admin.ModelAdmin):
    pass


class ArtworkInline(admin.TabularInline):
    model = Artwork.shows.through
    extra = 0
    verbose_name = "Artwork"
    verbose_name_plural = "Artworks"


class ShowAdmin(ImportExportAdmin):
    filter_horizontal = ['artists', 'curators', 'tags']
    inlines = [ArtworkInline]


admin.site.register(Artwork, ImportExportAdmin)
admin.site.register(Artist, ImportExportAdmin)
admin.site.register(Show, ShowAdmin)
admin.site.register(Event, ImportExportAdmin)
admin.site.register(Tag, ImportExportAdmin)
