from django.conf import settings
from django.db import models


DROPOFF = 'dropoff'
PICKUP = 'pickup'
KIND_CHOICES = [(DROPOFF, 'Drop-off'), (PICKUP, 'Pickup')]


class ScheduleWindow(models.Model):
    """A curator-defined date + time range during which artists may drop off
    work before a show (kind='dropoff') or pick it up after (kind='pickup').
    A show can have many of each."""

    show  = models.ForeignKey('gallery.Show', on_delete=models.CASCADE, related_name='schedule_windows')
    kind  = models.CharField(max_length=8, choices=KIND_CHOICES)
    date  = models.DateField()
    start = models.TimeField()
    end   = models.TimeField()

    class Meta:
        ordering = ['date', 'start']

    def __str__(self):
        return f'{self.show.name} {self.get_kind_display()} {self.date} {self.start}-{self.end}'


class ArtistSchedule(models.Model):
    """An artist's chosen drop-off or pickup time for a show, plus the curator's
    check-off once the work has actually arrived / been collected. One per
    (show, artist, kind)."""

    show           = models.ForeignKey('gallery.Show', on_delete=models.CASCADE, related_name='artist_schedules')
    artist         = models.ForeignKey('gallery.Artist', on_delete=models.CASCADE, related_name='schedules')
    kind           = models.CharField(max_length=8, choices=KIND_CHOICES)
    window         = models.ForeignKey(ScheduleWindow, on_delete=models.SET_NULL, null=True, blank=True, related_name='schedules')
    scheduled_time = models.TimeField(null=True, blank=True)   # a time on window.date
    done           = models.BooleanField(default=False)        # dropped off / picked up
    done_at        = models.DateTimeField(null=True, blank=True)
    done_by        = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    notes          = models.TextField(blank=True)

    class Meta:
        unique_together = [('show', 'artist', 'kind')]

    def __str__(self):
        return f'{self.artist} {self.get_kind_display()} for {self.show.name}'
