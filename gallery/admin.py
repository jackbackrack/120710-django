from django.contrib import admin
from import_export.admin import ImportExportModelAdmin

from gallery.models import Artist, Artwork, ArtworkImage, Event, LinkTreeEntry, Show, ShowInvitation, Tag
from gallery.models.collection import CollectionPiece, SavedArtwork
from gallery.models.room import RoomConfig, WallPlacement
from reviews.models import ShowJuror


class ImportExportAdmin(ImportExportModelAdmin, admin.ModelAdmin):
    pass


class ArtworkInline(admin.TabularInline):
    model = Artwork.shows.through
    extra = 0
    verbose_name = "Artwork"
    verbose_name_plural = "Artworks"


class ArtworkImageInline(admin.TabularInline):
    model = ArtworkImage
    extra = 1
    fields = ['image', 'order']


class ShowJurorInline(admin.TabularInline):
    model = ShowJuror
    extra = 0
    raw_id_fields = ['user', 'assigned_by']
    readonly_fields = ['assigned_at']


class ArtworkAdmin(ImportExportAdmin):
    inlines = [ArtworkImageInline]


class ShowAdmin(ImportExportAdmin):
    filter_horizontal = ['curators', 'tags']
    inlines = [ArtworkInline, ShowJurorInline]


admin.site.register(Artwork, ArtworkAdmin)
admin.site.register(Artist, ImportExportAdmin)
admin.site.register(Show, ShowAdmin)
admin.site.register(ShowInvitation)
admin.site.register(Event, ImportExportAdmin)
admin.site.register(Tag, ImportExportAdmin)


@admin.register(CollectionPiece)
class CollectionPieceAdmin(admin.ModelAdmin):
    list_display = ['artwork', 'collector', 'status', 'confirmed_by', 'purchase_date', 'purchase_price', 'commission_amount', 'created_at']
    list_filter = ['status']
    search_fields = [
        'artwork__name',
        'collector__username', 'collector__first_name', 'collector__last_name',
    ]
    raw_id_fields = ['collector', 'artwork', 'confirmed_by']
    readonly_fields = ['created_at', 'confirmed_at']
    list_editable = ['status']


@admin.register(SavedArtwork)
class SavedArtworkAdmin(admin.ModelAdmin):
    list_display = ['artwork', 'user', 'created_at']
    search_fields = ['artwork__name', 'user__username', 'user__first_name', 'user__last_name']
    raw_id_fields = ['user', 'artwork']
    readonly_fields = ['created_at']


@admin.register(LinkTreeEntry)
class LinkTreeEntryAdmin(admin.ModelAdmin):
    list_display = ('name', 'url', 'order', 'is_active')
    list_editable = ('order', 'is_active')
    ordering = ('order', 'name')


@admin.register(RoomConfig)
class RoomConfigAdmin(admin.ModelAdmin):
    list_display = ('site', 'width_in', 'depth_in', 'height_in')
    raw_id_fields = ('site',)


@admin.register(WallPlacement)
class WallPlacementAdmin(admin.ModelAdmin):
    list_display = ('show', 'artwork', 'wall', 'x_in', 'y_in', 'z_in')
    list_filter = ('wall', 'show')
    raw_id_fields = ('show', 'artwork')
