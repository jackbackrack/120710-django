import json

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from gallery.models import Artwork, Show, WallPlacement
from gallery.models.room import RoomConfig
from gallery.permissions import can_manage_show

_DEFAULT_CONFIG = {'width_in': 384.0, 'depth_in': 576.0, 'height_in': 120.0,
                   'wall_n_img': None, 'wall_e_img': None, 'wall_s_img': None,
                   'wall_w_img': None, 'floor_img': None, 'ceiling_img': None,
                   'obstacles': []}


def _room_config(show):
    """Return (RoomConfig, site) for the show's first site, or a stub if no site is set."""
    site = show.sites.first()
    if site is None:
        return None, None
    config, _ = RoomConfig.objects.get_or_create(site=site)
    return config, site


def _config_dict(config):
    if config is None:
        return _DEFAULT_CONFIG.copy()
    def _url(field):
        return field.url if field else None
    return {
        'width_in':    config.width_in,
        'depth_in':    config.depth_in,
        'height_in':   config.height_in,
        'wall_n_img':  _url(config.wall_n_image),
        'wall_e_img':  _url(config.wall_e_image),
        'wall_s_img':  _url(config.wall_s_image),
        'wall_w_img':  _url(config.wall_w_image),
        'floor_img':   _url(config.floor_image),
        'ceiling_img': _url(config.ceiling_image),
        'obstacles': [
            {'id': ob.pk, 'wall': ob.wall, 'label': ob.label,
             'x_in': ob.x_in, 'y_in': ob.y_in, 'z_in': ob.z_in,
             'w_in': ob.w_in, 'h_in': ob.h_in}
            for ob in config.obstacles.all()
        ],
    }


def _artwork_json(artwork):
    # Prefer the artist's cropped layout image; fall back to the hero image.
    img_url = ''
    if artwork.layout_image:
        try:
            img_url = artwork.layout_lg.url
        except Exception:
            img_url = artwork.layout_image.url
    if not img_url:
        try:
            img_url = artwork.slideshow.url
        except Exception:
            img_url = artwork.image.url if artwork.image else ''
    try:
        thumb_url = artwork.card_sm.url
    except Exception:
        thumb_url = img_url
    artists = ', '.join(str(a) for a in artwork.artists.all())
    return {
        'id':      artwork.pk,
        'name':    artwork.name,
        'artists': artists,
        'year':    artwork.end_year,
        'medium':  artwork.medium or '',
        'dims':    artwork.placard_dimensions if hasattr(artwork, 'placard_dimensions') else '',
        'w_in':    float(artwork.width_inches)  if artwork.width_inches  else 24.0,
        'h_in':    float(artwork.height_inches) if artwork.height_inches else 24.0,
        'd_in':    float(artwork.depth_inches)  if artwork.depth_inches  else 0.0,
        'img':     img_url,
        'thumb':   thumb_url,
    }


@login_required
def room_layout(request, slug):
    show = get_object_or_404(Show, slug=slug)
    if not can_manage_show(request.user, show):
        raise Http404

    config, _site = _room_config(show)
    placed     = WallPlacement.objects.filter(show=show).select_related('artwork')
    placed_ids = {wp.artwork_id for wp in placed}
    pool_qs    = show.artworks.exclude(pk__in=placed_ids).prefetch_related('artists')

    placements_json = json.dumps([
        {'artwork': _artwork_json(wp.artwork), 'wall': wp.wall,
         'x_in': wp.x_in, 'y_in': wp.y_in, 'z_in': wp.z_in, 'rotation': wp.rotation,
         'group': wp.group}
        for wp in placed
    ])
    pool_json   = json.dumps([_artwork_json(a) for a in pool_qs])
    config_json = json.dumps(_config_dict(config))

    return render(request, 'gallery/room_layout.html', {
        'show': show,
        'config_json': config_json,
        'placements_json': placements_json,
        'pool_json': pool_json,
    })


@login_required
@require_POST
def room_layout_save(request, slug):
    show = get_object_or_404(Show, slug=slug)
    if not can_manage_show(request.user, show):
        raise Http404
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'invalid JSON'}, status=400)

    config, _site = _room_config(show)
    if config is not None:
        room_cfg = data.get('room')
        if room_cfg:
            config.width_in  = float(room_cfg.get('width_in',  config.width_in))
            config.depth_in  = float(room_cfg.get('depth_in',  config.depth_in))
            config.height_in = float(room_cfg.get('height_in', config.height_in))
            config.save()

    WallPlacement.objects.filter(show=show).delete()
    errors = []
    for item in data.get('placements', []):
        try:
            artwork = Artwork.objects.get(pk=item['artwork_id'])
            WallPlacement.objects.create(
                show=show,
                artwork=artwork,
                wall=item['wall'],
                x_in=float(item['x_in']),
                y_in=float(item['y_in']),
                z_in=float(item['z_in']),
                rotation=(int(item.get('rotation', 0) or 0) % 360) if (int(item.get('rotation', 0) or 0) % 360) in (0, 90, 180, 270) else 0,
                group=(int(item['group']) if item.get('group') not in (None, '') else None),
            )
        except (Artwork.DoesNotExist, KeyError, ValueError) as e:
            errors.append(str(e))

    return JsonResponse({'ok': True, 'errors': errors})


def room_viewer(request, slug):
    show = get_object_or_404(Show, slug=slug)
    published = show.status in (Show.STATUS_PUBLISHED, Show.STATUS_CLOSED)
    if not published and not can_manage_show(request.user, show):
        raise Http404
    config, _site = _room_config(show)
    placed = (
        WallPlacement.objects
        .filter(show=show)
        .select_related('artwork')
        .prefetch_related('artwork__artists')
    )

    placements_json = json.dumps([
        {'artwork': _artwork_json(wp.artwork), 'wall': wp.wall,
         'x_in': wp.x_in, 'y_in': wp.y_in, 'z_in': wp.z_in, 'rotation': wp.rotation,
         'group': wp.group}
        for wp in placed
    ])
    config_json = json.dumps(_config_dict(config))

    return render(request, 'gallery/room_viewer.html', {
        'show': show,
        'config_json': config_json,
        'placements_json': placements_json,
    })
