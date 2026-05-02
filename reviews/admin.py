from django.contrib import admin

from reviews.models import ArtworkReview, ShowJuror


class ShowJurorInline(admin.TabularInline):
    model = ShowJuror
    extra = 1
    autocomplete_fields = ['user']
    readonly_fields = ['assigned_at']


@admin.register(ShowJuror)
class ShowJurorAdmin(admin.ModelAdmin):
    list_display = ['show', 'user', 'assigned_by', 'assigned_at']
    list_filter = ['show']
    search_fields = ['user__username', 'user__first_name', 'user__last_name', 'show__name']
    readonly_fields = ['assigned_at']
    raw_id_fields = ['user', 'assigned_by']


@admin.register(ArtworkReview)
class ArtworkReviewAdmin(admin.ModelAdmin):
    list_display = ['artwork', 'show', 'juror', 'rating', 'created_at', 'updated_at']
    list_filter = ['show', 'rating']
    search_fields = ['artwork__name', 'juror__username', 'juror__first_name', 'juror__last_name']
    readonly_fields = ['created_at', 'updated_at']
    raw_id_fields = ['artwork', 'show', 'juror']
