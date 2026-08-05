"""What an artist signs before leaving work with the gallery.

One agreement per artist per show, covering every piece of theirs in it. Not per artwork,
which would be a signature per piece; not one master agreement signed once, which would go
stale against a venue and a commission rate that change between shows, and would leave two
documents with a standing question about which governs.

The important property is that **a signed agreement never changes**. Everything material is
copied into `snapshot` at the moment of signing and read from there afterwards. Rendering it
live from `Artwork.agreed_value` and the site's commission rate would mean an artist
editing a price next month silently rewrote what they had signed, and so would any change to
the gallery's own rate. A contract that rewrites itself is not one.

There is deliberately no line model. Before signing the artwork list is derived from the
show; after signing it is in the snapshot. A third copy would be a third thing to keep in
step, and the copy that drifts is always the one nobody reads.

See docs/consignment-agreements.md.
"""
import hashlib
import json

from django.conf import settings
from django.db import models


# Bumped when the boilerplate itself changes. Signed agreements keep the version they were
# signed under, so old signatures keep meaning what they meant.
TERMS_VERSION = 3


class Consignment(models.Model):
    """One artist's agreement for one show."""

    STATUS_DRAFT = 'draft'
    STATUS_SIGNED = 'signed'
    STATUS_SUPERSEDED = 'superseded'
    STATUS_CHOICES = [
        (STATUS_DRAFT, 'Not yet signed'),
        (STATUS_SIGNED, 'Signed'),
        (STATUS_SUPERSEDED, 'Superseded by a later version'),
    ]

    show = models.ForeignKey('gallery.Show', on_delete=models.CASCADE,
                             related_name='consignments')
    artist = models.ForeignKey('gallery.Artist', on_delete=models.CASCADE,
                               related_name='consignments')
    version = models.PositiveSmallIntegerField(default=1)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_DRAFT)

    # Copied from the site or the show at signing, never read back through the relation.
    commission_rate = models.DecimalField(max_digits=5, decimal_places=2,
                                          null=True, blank=True)
    # A fixed literal, deliberately not TERMS_VERSION. Django tracks a field's default in
    # migration state, so pointing it at the constant made every wording change demand a
    # migration that alters nothing — defaults are applied in Python and never reach the
    # database. The signing view sets this explicitly, and the snapshot carries its own copy,
    # which is the authoritative one.
    terms_version = models.PositiveSmallIntegerField(default=1)

    # Everything the agreement says, frozen. See `freeze` in gallery/consignment.py for the
    # shape; it is read straight back out to render the PDF, so nothing outside it can
    # change what a signed agreement says.
    snapshot = models.JSONField(default=dict, blank=True)

    # What the snapshot covers, so a later change to the work in the show is detectable
    # without diffing the whole document.
    fingerprint = models.CharField(max_length=64, blank=True, default='')

    # ── The signature ────────────────────────────────────────────────────────
    #
    # Typed name, an explicit affirmation, and enough of a record to attribute it: intent,
    # attribution and a fixed copy of what was signed are what an electronic signature needs
    # to stand up. A drawn squiggle adds perceived weight and no legal weight.
    signed_at = models.DateTimeField(null=True, blank=True)
    signed_name = models.CharField(max_length=255, blank=True, default='')
    signed_ip = models.GenericIPAddressField(null=True, blank=True)
    signed_user_agent = models.CharField(max_length=500, blank=True, default='')
    signed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                  null=True, blank=True, related_name='+')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['show', 'artist', 'version'],
                                    name='one_consignment_version_per_artist_show'),
        ]
        indexes = [models.Index(fields=['show', 'status'])]

    def __str__(self):
        return f'{self.artist} — {self.show} (v{self.version}, {self.status})'

    @property
    def is_signed(self):
        return self.status == self.STATUS_SIGNED

    @property
    def artworks_in_snapshot(self):
        return self.snapshot.get('artworks', [])

    @property
    def total_agreed_value(self):
        """What the gallery is on the hook for under this agreement."""
        return sum(a.get('agreed_value') or 0 for a in self.artworks_in_snapshot)


def fingerprint_of(payload):
    """A stable digest of the material facts, for spotting that a signed agreement is stale.

    Sorted keys and a canonical separator so the same facts always hash the same way — a
    dict that merely reordered would otherwise read as a changed agreement and ask the
    artist to sign again for nothing.
    """
    blob = json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str)
    return hashlib.sha256(blob.encode('utf-8')).hexdigest()
