from import_export.admin import ImportExportModelAdmin
from django.contrib import admin

from .models import Artist, Piece, Show

class ImportExportAdmin(ImportExportModelAdmin, admin.ModelAdmin) :
    pass

admin.site.register(Piece, ImportExportAdmin)
admin.site.register(Artist, ImportExportAdmin)
admin.site.register(Show, ImportExportAdmin)

# admin.site.register(Piece)
# admin.site.register(Artist)
# admin.site.register(Show)
