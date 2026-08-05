"""What a given viewer can do with a show, as data rather than markup.

The show page's controls had grown to 21, laid out by four hard-coded full-width
spacers. Position depended on where a tag sat in the template, so a group that was
empty for one viewer still left a gap and a full one wrapped unpredictably. Defining
them here means order is the list order, empty groups disappear along with their
spacing, and adding a control is one line rather than finding the right slot.

Plain buttons are for everyone — few, and self-explanatory. Curator and admin tools
are grouped into small menus (4–5 items) so the page does not turn into a wall of
links for the people who happen to have the most permissions.
"""
from django.urls import reverse


def _link(label, url, title=''):
    return {'label': label, 'url': url, 'title': title}


def _post(label, url, confirm, title=''):
    """An action that submits rather than navigates (rendered as a form button)."""
    return {'label': label, 'post_url': url, 'confirm': confirm, 'title': title}


def show_actions(show, *, can_manage=False, can_delete=False, can_view_reviews=False,
                 can_assign_jurors=False, can_view_3d=False, can_view_checklist=False,
                 can_schedule_dropoff=False, can_print_controls=False,
                 emails_pending=0, emails_sent=0):
    """Return {'buttons': [...], 'menus': [{'label', 'items'}, ...]}.

    Every entry is already permission-filtered, so the template renders whatever it
    is handed without repeating the conditions.
    """
    slug, pk = show.slug, show.pk
    is_invited = show.submission_type == show.SUBMISSION_INVITED
    is_open = show.submission_type == show.SUBMISSION_OPEN

    # ── Plain buttons: anyone who can see them ────────────────────────────────
    buttons = []
    if can_view_3d:
        buttons.append(_link('2D View', reverse('gallery:room_2d', kwargs={'slug': slug}),
                             'Where the work hangs, as a flat plan'))
        buttons.append(_link('3D View', reverse('gallery:room_viewer', kwargs={'slug': slug}),
                             'Walk through the show'))
    if can_view_checklist:
        buttons.append(_link('Checklist', reverse('gallery:show_checklist', kwargs={'slug': slug}),
                             'Works, credits and artist bios'))
    if can_schedule_dropoff:
        word = 'Install' if show.self_install else 'Drop-off'
        buttons.append(_link(f'Schedule My {word} & Pickup',
                             reverse('gallery:artist_schedule', kwargs={'slug': slug})))

    # ── Menus: curator and admin tools ────────────────────────────────────────
    curate = []
    if can_view_reviews:
        curate.append(_link('Submissions', reverse('gallery:show_submissions', kwargs={'slug': slug})))
    if is_open and can_view_reviews:
        curate.append(_link('Reviews', reverse('reviews:show_review_dashboard',
                                               kwargs={'show_slug': slug})))
    if is_open and can_assign_jurors:
        curate.append(_link('Assign Jurors', reverse('reviews:show_juror_assignment',
                                                     kwargs={'show_slug': slug})))
    if is_invited and can_manage:
        curate.append(_link('Invite Artists', reverse('gallery:invite_artists', kwargs={'slug': slug})))

    produce = []
    if can_manage:
        produce.append(_link('Layout', reverse('gallery:room_layout', kwargs={'slug': slug})))
        produce.append(_link('Placards PDF',
                             reverse('gallery:show_placards_detail', kwargs={'slug': slug}),
                             'Choose which placards to print (Avery 5376)'))
        produce.append(_link('Checklist PDF',
                             reverse('gallery:show_checklist_pdf', kwargs={'slug': slug}),
                             'Cover, works with images, artist and curator bios'))
    if can_print_controls:
        # Instagram is a curator tool for preparing posts, not a way to view the show.
        produce.append(_link('Instagram', show.get_instagram_url(),
                             'Post text and images for social'))
        produce.append(_post('Renumber', reverse('gallery:renumber_artworks', kwargs={'slug': slug}),
                             'Reassign artwork numbers from scratch?'))

    logistics = []
    if can_manage:
        if emails_pending or emails_sent:
            sent = f', {emails_sent} sent' if emails_sent else ''
            logistics.append(_post(f'Send Emails ({emails_pending} pending{sent})',
                                   reverse('gallery:send_selection_emails', kwargs={'slug': slug}),
                                   f'Send acceptance/rejection emails to {emails_pending} artist(s)?'))
        logistics.append(_link('Emails', reverse('gallery:show_artist_emails', kwargs={'slug': slug})))
        logistics.append(_link('Schedule Windows',
                               reverse('gallery:show_schedule_windows', kwargs={'slug': slug})))
        logistics.append(_link('Schedule Tracker',
                               reverse('gallery:show_schedule_tracker', kwargs={'slug': slug})))
        # Beside the schedule, because a consignment is signed on or before drop-off and
        # the two are the same errand from the artist's side.
        logistics.append(_link('Consignments',
                               reverse('gallery:show_consignments', kwargs={'slug': slug})))

    manage = []
    if can_manage:
        manage.append(_link('Edit', reverse('gallery:show_edit', kwargs={'pk': pk})))
        manage.append(_link('New Event', reverse('gallery:event_new') + f'?show={pk}'))
    if can_delete:
        manage.append(_link('Delete', reverse('gallery:show_delete', kwargs={'pk': pk})))

    # Manage sits first, straight after the Checklist button: editing the show is the
    # action a curator reaches for most, so it belongs nearest the plain buttons.
    menus = [{'label': label, 'items': items} for label, items in (
        ('Manage', manage), ('Curate', curate),
        ('Produce', produce), ('Logistics', logistics),
    ) if items]

    return {'buttons': buttons, 'menus': menus}
