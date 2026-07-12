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


class WallPlacement(models.Model):
    WALL_N       = 'N'
    WALL_E       = 'E'
    WALL_S       = 'S'
    WALL_W       = 'W'
    WALL_CEILING = 'ceiling'
    WALL_FLOOR   = 'floor'
    WALL_CHOICES = [
        (WALL_N, 'North'), (WALL_E, 'East'), (WALL_S, 'South'), (WALL_W, 'West'),
        (WALL_CEILING, 'Ceiling'), (WALL_FLOOR, 'Floor'),
    ]

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

    class Meta:
        unique_together = [('show', 'artwork')]

    def __str__(self):
        return f'{self.artwork} on {self.get_wall_display()} wall of {self.show}'
