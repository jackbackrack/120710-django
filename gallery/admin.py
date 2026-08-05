from django.contrib import admin
from import_export.admin import ImportExportModelAdmin

from gallery.models import Consignment, Artist, ArtistSchedule, Artwork, ArtworkImage, Event, LinkTreeEntry, ScheduleWindow, Show, ShowInvitation, Tag
from gallery.models.collection import CollectionPiece, SavedArtwork
from gallery.models.room import RoomConfig, WallObstacle, WallPlacement
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
admin.site.register(ScheduleWindow)
admin.site.register(ArtistSchedule)


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


class WallObstacleInline(admin.TabularInline):
    model = WallObstacle
    extra = 1
    fields = ['wall', 'label', 'x_in', 'y_in', 'z_in', 'w_in', 'h_in']


@admin.register(RoomConfig)
class RoomConfigAdmin(admin.ModelAdmin):
    list_display = ('site', 'width_in', 'depth_in', 'height_in')
    raw_id_fields = ('site',)
    inlines = [WallObstacleInline]


@admin.register(WallPlacement)
class WallPlacementAdmin(admin.ModelAdmin):
    list_display = ('show', 'artwork', 'wall', 'x_in', 'y_in', 'z_in')
    list_filter = ('wall', 'show')
    raw_id_fields = ('show', 'artwork')


@admin.register(Consignment)
class ConsignmentAdmin(admin.ModelAdmin):
    """A window onto signed agreements, not a way to edit them.

    Everything that constitutes the agreement is read-only, because the point of the record
    is that it cannot change after signing — an editable snapshot would make every signature
    deniable. Voiding is the one thing the gallery may do afterwards, and it belongs on the
    consignments page rather than here: a site director manages this and has no admin access.
    """

    list_display = ['artist', 'show', 'version', 'status', 'commission_rate',
                    'total_agreed_value', 'signed_at', 'signed_name']
    list_filter = ['status', 'show', 'terms_version']
    search_fields = ['artist__name', 'artist__email', 'show__name', 'signed_name']
    date_hierarchy = 'created_at'
    readonly_fields = ['show', 'artist', 'version', 'commission_rate', 'terms_version',
                       'snapshot', 'fingerprint', 'signed_at', 'signed_name', 'signed_ip',
                       'signed_user_agent', 'signed_by', 'voided_at', 'voided_by',
                       'void_reason', 'created_at', 'updated_at']

    def has_add_permission(self, request):
        # A consignment exists because somebody signed one. Adding by hand would create a
        # record of an agreement that was never agreed to.
        return False

    def total_agreed_value(self, obj):
        return obj.total_agreed_value
    total_agreed_value.short_description = 'Agreed value'
