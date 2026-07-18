from django.db import models


class RoomConfig(models.Model):
    """Physical dimensions of a gallery site (constant across shows at that location)."""
    site      = models.OneToOneField(
        'gallery.Site', on_delete=models.CASCADE, related_name='room_config'
    )
    width_in  = models.FloatField(default=384.0)   # E–W (32 ft)
    depth_in  = models.FloatField(default=576.0)   # N–S (48 ft)
    height_in = models.FloatField(default=120.0)   # 10 ft

    wall_n_image  = models.ImageField(upload_to='room_textures/', blank=True, null=True)
    wall_e_image  = models.ImageField(upload_to='room_textures/', blank=True, null=True)
    wall_s_image  = models.ImageField(upload_to='room_textures/', blank=True, null=True)
    wall_w_image  = models.ImageField(upload_to='room_textures/', blank=True, null=True)
    floor_image   = models.ImageField(upload_to='room_textures/', blank=True, null=True)
    ceiling_image = models.ImageField(upload_to='room_textures/', blank=True, null=True)

    def __str__(self):
        return f'Room for {self.site}'


WALL_CHOICES = [
    ('N', 'North'), ('E', 'East'), ('S', 'South'), ('W', 'West'),
    ('ceiling', 'Ceiling'), ('floor', 'Floor'),
]


class WallObstacle(models.Model):
    """Immovable rectangular obstacle on a wall (e.g. door, window) used as a layout reference."""
    room_config = models.ForeignKey(RoomConfig, on_delete=models.CASCADE, related_name='obstacles')
    wall        = models.CharField(max_length=8, choices=WALL_CHOICES)
    label       = models.CharField(max_length=100, default='Obstacle')
    x_in        = models.FloatField(default=0)    # horiz center from room center (or z for E/W walls)
    y_in        = models.FloatField(default=42)   # height center from floor
    z_in        = models.FloatField(default=0)    # used for E/W wall depth and ceiling/floor
    w_in        = models.FloatField(default=36)   # width
    h_in        = models.FloatField(default=80)   # height

    class Meta:
        ordering = ['wall', 'x_in', 'z_in']

    def __str__(self):
        return f'{self.label} on {self.get_wall_display()} wall of {self.room_config.site}'


class WallPlacement(models.Model):
    WALL_N       = 'N'
    WALL_E       = 'E'
    WALL_S       = 'S'
    WALL_W       = 'W'
    WALL_CEILING = 'ceiling'
    WALL_FLOOR   = 'floor'
    WALL_CHOICES = WALL_CHOICES

    show    = models.ForeignKey(
        'gallery.Show', on_delete=models.CASCADE, related_name='wall_placements'
    )
    artwork = models.ForeignKey(
        'gallery.Artwork', on_delete=models.CASCADE, related_name='wall_placements'
    )
    wall    = models.CharField(max_length=8, choices=WALL_CHOICES)

    # World-space position of the artwork center, in inches.
    # Origin = room centre. Y = height from floor.
    # X east = positive, Z south = positive.
    x_in = models.FloatField()
    y_in = models.FloatField()
    z_in = models.FloatField()

    # Yaw rotation in degrees for floor/ceiling pieces: 0 or 90.
    rotation = models.IntegerField(default=0)

    # Optional grouping id — placements sharing a value move/align/distribute
    # together in the layout editor. Null = ungrouped.
    group = models.IntegerField(null=True, blank=True)

    # The support (pedestal/shelf) this piece sits on, if any. The piece is
    # centered on the support and moves with it; layout ops still use the piece's
    # own geometry. Null = hung/placed directly.
    support = models.ForeignKey(
        'gallery.Support', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='artworks',
    )

    class Meta:
        unique_together = [('show', 'artwork')]

    def __str__(self):
        return f'{self.artwork} on {self.get_wall_display()} wall of {self.show}'


class Support(models.Model):
    """A plain cuboid a piece can sit on — a pedestal when on the floor/ceiling,
    a shelf when on a vertical wall (the term is derived from the wall)."""
    show   = models.ForeignKey('gallery.Show', on_delete=models.CASCADE, related_name='supports')
    wall   = models.CharField(max_length=8, choices=WALL_CHOICES)
    label  = models.CharField(max_length=100, blank=True)

    # Center position (inches), same convention as WallPlacement.
    x_in = models.FloatField(default=0)
    y_in = models.FloatField(default=0)
    z_in = models.FloatField(default=0)

    # Cuboid dimensions: w along the wall, h vertical, d depth into the room.
    w_in = models.FloatField(default=16)
    h_in = models.FloatField(default=40)
    d_in = models.FloatField(default=16)

    rotation = models.IntegerField(default=0)  # yaw for floor/ceiling supports

    # Snapshot of the texture applied to all six faces, copied from the catalog
    # SiteSupport when the support was placed. A plain URL (not a file) so the
    # show stays independent of later catalog edits. Blank = white with a black
    # outline.
    texture_url = models.CharField(max_length=500, blank=True, default='')

    class Meta:
        ordering = ['wall', 'x_in']

    def __str__(self):
        return f'Support on {self.get_wall_display()} of {self.show}'


class SiteSupport(models.Model):
    """A reusable support definition (named cuboid) for a site. Copied into a show
    (as a Support) when placed, so later edits never change past shows."""
    room_config = models.ForeignKey(RoomConfig, on_delete=models.CASCADE, related_name='supports')
    label = models.CharField(max_length=100, blank=True)
    w_in  = models.FloatField(default=16)
    h_in  = models.FloatField(default=40)
    d_in  = models.FloatField(default=16)

    # Optional image applied to all six faces of the support in 2D/3D. Blank =
    # white with a black outline.
    texture = models.ImageField(upload_to='support_textures/', blank=True, null=True)

    class Meta:
        ordering = ['label']

    def __str__(self):
        return f'Support "{self.label}" ({self.room_config.site})'
