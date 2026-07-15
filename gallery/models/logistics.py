from django.conf import settings
from django.db import models


DROPOFF = 'dropoff'
INSTALL = 'install'
PICKUP = 'pickup'
KIND_CHOICES = [(DROPOFF, 'Drop-off'), (INSTALL, 'Install'), (PICKUP, 'Pickup')]


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

    def google_calendar_url(self, duration_minutes=30):
        """A pre-filled 'Add to Google Calendar' link for this scheduled time.
        Returns None if not yet scheduled. Uses floating local time (the gallery
        clock time), which is what a local drop-off/install/pickup wants."""
        if not (self.window and self.scheduled_time):
            return None
        import datetime
        from urllib.parse import urlencode
        start = datetime.datetime.combine(self.window.date, self.scheduled_time)
        end = start + datetime.timedelta(minutes=duration_minutes)
        fmt = '%Y%m%dT%H%M%S'
        site = self.show.sites.first()
        location = ''
        if site:
            parts = [site.street, site.city, site.state, site.postal_code, site.country]
            location = ', '.join([p for p in parts if p]) or site.name
        label = self.get_kind_display()
        params = {
            'action': 'TEMPLATE',
            'text': f'{label}: {self.show.name}',
            'dates': f'{start.strftime(fmt)}/{end.strftime(fmt)}',
            'details': f'{label} for the show "{self.show.name}".',
            'location': location,
        }
        return 'https://calendar.google.com/calendar/render?' + urlencode(params)

    def _event_parts(self, duration_minutes=30):
        import datetime
        start = datetime.datetime.combine(self.window.date, self.scheduled_time)
        end = start + datetime.timedelta(minutes=duration_minutes)
        site = self.show.sites.first()
        location = ''
        if site:
            parts = [site.street, site.city, site.state, site.postal_code, site.country]
            location = ', '.join([p for p in parts if p]) or site.name
        label = self.get_kind_display()
        return start, end, label, location

    def ics(self, duration_minutes=30):
        """An iCalendar (.ics) event for this scheduled time — opens in Apple
        Calendar, Outlook, or Google. Floating local time. None if unscheduled."""
        if not (self.window and self.scheduled_time):
            return None
        from django.utils import timezone
        start, end, label, location = self._event_parts(duration_minutes)

        def esc(s):
            return (s or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')

        lines = [
            'BEGIN:VCALENDAR',
            'VERSION:2.0',
            'PRODID:-//120710//Art Logistics//EN',
            'CALSCALE:GREGORIAN',
            'METHOD:PUBLISH',
            'BEGIN:VEVENT',
            f'UID:schedule-{self.pk}-{self.kind}@120710.art',
            f'DTSTAMP:{timezone.now().strftime("%Y%m%dT%H%M%SZ")}',
            f'DTSTART:{start.strftime("%Y%m%dT%H%M%S")}',
            f'DTEND:{end.strftime("%Y%m%dT%H%M%S")}',
            f'SUMMARY:{esc(label + ": " + self.show.name)}',
            f'LOCATION:{esc(location)}',
            f'DESCRIPTION:{esc(label + " for the show " + self.show.name)}',
            'END:VEVENT',
            'END:VCALENDAR',
        ]
        return '\r\n'.join(lines) + '\r\n'
