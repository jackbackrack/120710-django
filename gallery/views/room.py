import json
import urllib.parse

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from gallery.models import Artwork, Show, WallPlacement
from gallery.models.room import RoomConfig, SiteSupport, Support
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
    # "Prefer the crop, else the hero" lives on the model (Artwork.layout_*_url),
    # so the layout editor, 3D viewer, and detail page share one source of truth.
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
        'hang_drop': float(artwork.hang_drop_inches) if artwork.hang_drop_inches is not None else None,
        'img':     artwork.layout_display_url,
        'thumb':   artwork.layout_thumb_url,
        # Placard (wall-label) content — shared by the 2D tool and 3D viewer.
        'price':   artwork.formatted_price or '',
        'sold':    bool(artwork.is_sold),
    }


def _placements_json(placed):
    return json.dumps([
        {'artwork': _artwork_json(wp.artwork), 'wall': wp.wall,
         'x_in': wp.x_in, 'y_in': wp.y_in, 'z_in': wp.z_in, 'rotation': wp.rotation,
         'group': wp.group, 'support': wp.support_id}
        for wp in placed
    ])


def _support_json(s):
    return {'id': s.pk, 'wall': s.wall, 'label': s.label,
            'x_in': s.x_in, 'y_in': s.y_in, 'z_in': s.z_in,
            'w_in': s.w_in, 'h_in': s.h_in, 'd_in': s.d_in, 'rotation': s.rotation,
            'texture': s.texture_url or None}


def _supports_json(show):
    return json.dumps([_support_json(s) for s in show.supports.all()])


def _site_supports_json(config):
    """The site's reusable support catalog (definitions), for the layout palette."""
    if config is None:
        return '[]'
    return json.dumps([
        {'id': c.pk, 'label': c.label,
         'w_in': c.w_in, 'h_in': c.h_in, 'd_in': c.d_in,
         'texture': c.texture.url if c.texture else None}
        for c in config.supports.all()
    ])


@login_required
def room_layout(request, slug):
    show = get_object_or_404(Show, slug=slug)
    if not can_manage_show(request.user, show):
        raise Http404

    config, _site = _room_config(show)
    placed     = WallPlacement.objects.filter(show=show).select_related('artwork')
    placed_ids = {wp.artwork_id for wp in placed}
    pool_qs    = show.artworks.exclude(pk__in=placed_ids).prefetch_related('artists')

    placements_json = _placements_json(placed)
    pool_json   = json.dumps([_artwork_json(a) for a in pool_qs])
    config_json = json.dumps(_config_dict(config))

    return render(request, 'gallery/room_layout.html', {
        'show': show,
        'config_json': config_json,
        'placements_json': placements_json,
        'pool_json': pool_json,
        'supports_json': _supports_json(show),
        'site_supports_json': _site_supports_json(config),
    })


def room_2d(request, slug):
    """Read-only version of the layout editor for artists to see where to install
    their work. Same rendering as the editor, with all editing controls and
    gestures disabled. Visible on published shows (like the 3D viewer) or to a
    curator anytime."""
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
    return render(request, 'gallery/room_layout.html', {
        'show': show,
        'config_json': json.dumps(_config_dict(config)),
        'placements_json': _placements_json(placed),
        'pool_json': '[]',
        'supports_json': _supports_json(show),
        'readonly': True,
    })


def _layout_payload(show):
    """Serialize a show's current layout (room dims + supports + placements) into
    the same JSON shape the save endpoint accepts, so snapshots can be re-applied.
    Supports are keyed by their pk; placements reference that key."""
    config, _site = _room_config(show)
    supports = list(Support.objects.filter(show=show))
    placements = list(WallPlacement.objects.filter(show=show))
    return {
        'room': ({'width_in': config.width_in, 'depth_in': config.depth_in,
                  'height_in': config.height_in} if config is not None else None),
        'supports': [{
            'key': s.pk, 'wall': s.wall, 'label': s.label,
            'x_in': s.x_in, 'y_in': s.y_in, 'z_in': s.z_in,
            'w_in': s.w_in, 'h_in': s.h_in, 'd_in': s.d_in,
            'rotation': s.rotation, 'texture': s.texture_url,
        } for s in supports],
        'placements': [{
            'artwork_id': p.artwork_id, 'wall': p.wall,
            'x_in': p.x_in, 'y_in': p.y_in, 'z_in': p.z_in,
            'rotation': p.rotation, 'group': p.group, 'support': p.support_id,
        } for p in placements],
    }


MAX_AUTO_SNAPSHOTS = 40   # per show; a rolling safety net for accidental overwrites


def _take_snapshot(show, user, kind, name=''):
    """Store the show's CURRENT layout as a snapshot. Best-effort: never raise into
    the caller (a snapshot must never block or corrupt an actual save)."""
    from gallery.models import ShowLayoutSnapshot
    try:
        payload = _layout_payload(show)
        # Skip empty auto snapshots (nothing to protect yet).
        if kind == ShowLayoutSnapshot.AUTO and not payload['placements'] and not payload['supports']:
            return None
        snap = ShowLayoutSnapshot.objects.create(
            show=show, kind=kind, name=name,
            created_by=user if getattr(user, 'is_authenticated', False) else None,
            payload=payload,
        )
        if kind == ShowLayoutSnapshot.AUTO:
            stale = ShowLayoutSnapshot.objects.filter(
                show=show, kind=ShowLayoutSnapshot.AUTO
            ).values_list('pk', flat=True)[MAX_AUTO_SNAPSHOTS:]
            if stale:
                ShowLayoutSnapshot.objects.filter(pk__in=list(stale)).delete()
        return snap
    except Exception:   # noqa: BLE001 — snapshots are a safety net, not critical path
        return None


def _apply_layout(show, data):
    """Replace a show's layout with the given payload. Returns a list of errors."""
    config, _site = _room_config(show)
    if config is not None:
        room_cfg = data.get('room')
        if room_cfg:
            config.width_in  = float(room_cfg.get('width_in',  config.width_in))
            config.depth_in  = float(room_cfg.get('depth_in',  config.depth_in))
            config.height_in = float(room_cfg.get('height_in', config.height_in))
            config.save()

    errors = []
    # Rebuild supports first, mapping each payload's client key → the new row so
    # placements can reference the support they sit on.
    WallPlacement.objects.filter(show=show).delete()
    Support.objects.filter(show=show).delete()
    support_by_key = {}
    for item in data.get('supports', []):
        try:
            s = Support.objects.create(
                show=show, wall=item['wall'], label=item.get('label', '') or '',
                x_in=float(item['x_in']), y_in=float(item['y_in']), z_in=float(item['z_in']),
                w_in=float(item['w_in']), h_in=float(item['h_in']), d_in=float(item['d_in']),
                rotation=(int(item.get('rotation', 0) or 0) % 360) if (int(item.get('rotation', 0) or 0) % 360) in (0, 90, 180, 270) else 0,
                texture_url=(item.get('texture') or '')[:500],
            )
            if item.get('key') is not None:
                support_by_key[str(item['key'])] = s
        except (KeyError, ValueError, TypeError) as e:
            errors.append(str(e))

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
                support=support_by_key.get(str(item.get('support'))) if item.get('support') not in (None, '') else None,
            )
        except (Artwork.DoesNotExist, KeyError, ValueError) as e:
            errors.append(str(e))
    return errors


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

    # Safety net: snapshot the state we're about to overwrite so any bad/stale save
    # (e.g. an old tab) can be rolled back.
    _take_snapshot(show, request.user, kind='auto')

    errors = _apply_layout(show, data)
    return JsonResponse({'ok': True, 'errors': errors})


def _snapshot_dict(snap):
    p = snap.payload or {}
    return {
        'id': snap.pk,
        'name': snap.name or '(unnamed)',
        'kind': snap.kind,
        'created_at': snap.created_at.isoformat(),
        'created_at_display': snap.created_at.strftime('%b %-d, %Y %-I:%M %p'),
        'by': (snap.created_by.email if snap.created_by else ''),
        'n_placements': len(p.get('placements', [])),
        'n_supports': len(p.get('supports', [])),
    }


@login_required
def layout_snapshots(request, slug):
    """GET: list this show's snapshots. POST: save the current layout as a named
    manual snapshot."""
    show = get_object_or_404(Show, slug=slug)
    if not can_manage_show(request.user, show):
        raise Http404
    from gallery.models import ShowLayoutSnapshot

    if request.method == 'POST':
        try:
            data = json.loads(request.body or '{}')
        except (json.JSONDecodeError, ValueError):
            data = {}
        name = (data.get('name') or '').strip()[:200] or 'Manual snapshot'
        snap = _take_snapshot(show, request.user, kind=ShowLayoutSnapshot.MANUAL, name=name)
        if snap is None:
            return JsonResponse({'ok': False, 'error': 'Could not save snapshot.'}, status=400)
        return JsonResponse({'ok': True, 'snapshot': _snapshot_dict(snap)})

    snaps = show.layout_snapshots.select_related('created_by')[:100]
    return JsonResponse({'ok': True, 'snapshots': [_snapshot_dict(s) for s in snaps]})


@login_required
@require_POST
def restore_layout_snapshot(request, slug, pk):
    """Replace the current layout with a snapshot (auto-snapshotting the current
    state first, so a restore is itself undoable)."""
    show = get_object_or_404(Show, slug=slug)
    if not can_manage_show(request.user, show):
        raise Http404
    from gallery.models import ShowLayoutSnapshot
    snap = get_object_or_404(ShowLayoutSnapshot, pk=pk, show=show)
    _take_snapshot(show, request.user, kind=ShowLayoutSnapshot.AUTO)
    errors = _apply_layout(show, snap.payload or {})
    return JsonResponse({'ok': True, 'errors': errors})


@login_required
@require_POST
def save_support_to_catalog(request, slug):
    """Create a reusable SiteSupport (catalog entry) on the show's site from a
    support built in the layout tool."""
    show = get_object_or_404(Show, slug=slug)
    if not can_manage_show(request.user, show):
        raise Http404
    config, _site = _room_config(show)
    if config is None:
        return JsonResponse({'ok': False, 'error': 'This show has no site/room configured.'}, status=400)
    try:
        data = json.loads(request.body)
        cat = SiteSupport.objects.create(
            room_config=config, label=(data.get('label') or '').strip(),
            w_in=float(data['w_in']), h_in=float(data['h_in']), d_in=float(data['d_in']),
        )
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({'ok': False, 'error': 'invalid data'}, status=400)

    # Best-effort: reuse the same texture file the placed support was using, so the
    # catalog copy looks the same. The texture URL points at an existing file in the
    # support_textures/ dir; reference it by relative path (never uploads/overwrites).
    texture_url = (data.get('texture') or '').strip()
    if texture_url:
        try:
            rel = urllib.parse.urlparse(texture_url).path
            marker = 'support_textures/'
            i = rel.find(marker)
            if i != -1:
                rel = rel[i:]
                if cat.texture.storage.exists(rel):
                    cat.texture.name = rel
                    cat.save(update_fields=['texture'])
        except Exception:
            pass   # texture is optional; the label/dimensions are already saved

    return JsonResponse({'ok': True, 'item': {
        'id': cat.pk, 'label': cat.label,
        'w_in': cat.w_in, 'h_in': cat.h_in, 'd_in': cat.d_in,
        'texture': (cat.texture.url if cat.texture else None)}})


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

    placements_json = _placements_json(placed)
    config_json = json.dumps(_config_dict(config))

    return render(request, 'gallery/room_viewer.html', {
        'show': show,
        'config_json': config_json,
        'placements_json': placements_json,
        'supports_json': _supports_json(show),
    })
