from django.contrib import admin
from import_export.admin import ImportExportModelAdmin

from gallery.models import Artist, Artwork, Event, Show, Tag


class ImportExportAdmin(ImportExportModelAdmin, admin.ModelAdmin):
    pass


admin.site.register(Artwork, ImportExportAdmin)
admin.site.register(Artist, ImportExportAdmin)
admin.site.register(Show, ImportExportAdmin)
admin.site.register(Event, ImportExportAdmin)
admin.site.register(Tag, ImportExportAdmin)
