"""Carry 120710's public info from the templates into its Site row.

The Info, Visit, Contact and Links pages were hard-coded markup naming one gallery. The
templates now read from the Site, so without this the deployed pages would come back blank
— the content exists only in git history at that point.

Every write is guarded on the field being empty, so this is safe to re-run and will not
overwrite anything edited through the site form afterwards. Reversing it clears only the
fields whose contents still match what was moved.
"""
from django.db import migrations

SITE_SLUG = '120710'

HOURS = 'Sun 1-4p or by Appt MWF 12-6p'

# Parking and transit, previously loose <p> content on the Visit page.
VISIT_NOTES = (
    '<p>Street parking available</p>'
    '<p>Nearby AC Transit bus stop at San Pablo and Gilman</p>'
)

ABOUT = '''\
<h1>Mission</h1>
Quite simply, 120710 is a catalyst for art. We believe that a gallery is the fulcrum between the artists and the community. Our mission is to nurture artists, foster the love of art, and build community around art. We feel that the fastest way for artistic growth is through experimentation and iteration. We provide an artistic platform and a creative safe-house to try new ideas and celebrate art. We are a no-profit with 100% of the art sales going to the artists because we want to ensure curatorial integrity. We don’t make money from art sales; we make artists from art sales. 120710 is an experimental gallery showing experimental work and we are always exploring ways to modernize and improve the art-going experience. We are striving to show the best possible art to better inspire and push artists to do their best and make hearts beat the fastest. We do group shows to maximize the number of artists showing and the growth of our community. Furthermore, every show has a new set of curators to ensure diversity. We choose curators, not artists. If you have a strong curatorial idea aligned with our philosophy, send a proposal to <a href="mailto:info@120710.art">info@120710.art</a>.  We are ambitious. We believe in the power of a gallery, in artists and the Bay Area, and more generally the potential for catalyzing art.
<br><br>
<h1>Story</h1>
<img loading="lazy" width=400px src="/static/img/120710-former-cal-professor.jpg">
<br>
‘A creative safe house’: Former Cal computer science professor opens experimental art gallery in West Berkeley, West Berkeley’s newest art gallery wants to encourage artists to take risks.
<a href="https://www.berkeleyside.org/2023/08/04/jonathan-bachrach-former-cal-professor-experimental-art-gallery-120710">Berkeleyside Aug 4, 2023</a>
<br><br>
<h1>People</h1>
<table>
    <thead>
      <tr>
        <th scope="col">Name</th>
        <th scope="col">Title</th>
        <th scope="col">Contributions</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td>Jonathan Bachrach</td>
        <td>Director</td>
        <td>Vision + Web + Social Media + Production + Crew + BarnRaising</td>
      </tr>
      <tr>
        <td>Alicia Duque</td>
        <td>Assistant</td>
        <td>Crew</td>
      </tr>
      <tr>
        <td>Mathew Galvin</td>
        <td>Volunteer</td>
        <td>Crew + Web</td>
      </tr>
      <tr>
        <td>Dave Bizer </td>
        <td>Designer</td>
        <td>Design + BarnRaising + Advisor</td>
      </tr>
      <tr>
        <td>Fran Trainor</td>
        <td>Advisor</td>
        <td>BarnRaising + Advisor</td>
      </tr>
      <tr>
        <td>Bibi Koenig</td>
        <td>Alum / Intern</td>
        <td>Crew + Social Media + Production + Video + etc</td>
      </tr>
      <tr>
        <td>Josh Hash </td>
        <td>Alum / Advisor</td>
        <td>Early liftoff including First 2 shows + early Web/Store</td>
      </tr>
    </tbody>
  </table>
'''

# The two buttons the Links page hard-coded. reset.art carries no site: it is the
# network, so it belongs on every venue's page, not only this one.
LINKS = [
    ('120710.art', 'https://120710.art', 10, SITE_SLUG),
    ('reset.art', 'https://reset.art', 20, None),
]


def forwards(apps, schema_editor):
    Site = apps.get_model('gallery', 'Site')
    LinkTreeEntry = apps.get_model('gallery', 'LinkTreeEntry')

    site = Site.objects.filter(slug=SITE_SLUG).first()
    if site is None:
        # A fresh database, or a deployment whose default venue is named something else.
        # Nothing to carry across, and nothing to complain about.
        return

    changed = []
    for field, value in (('hours', HOURS), ('visit_notes', VISIT_NOTES), ('about', ABOUT)):
        if not getattr(site, field, ''):
            setattr(site, field, value)
            changed.append(field)
    if changed:
        site.save(update_fields=changed)

    for name, url, order, slug in LINKS:
        owner = site if slug else None
        # Keyed on the URL so a re-run updates rather than duplicates.
        LinkTreeEntry.objects.get_or_create(
            url=url, defaults={'name': name, 'order': order, 'site': owner,
                               'is_active': True})


def backwards(apps, schema_editor):
    Site = apps.get_model('gallery', 'Site')
    LinkTreeEntry = apps.get_model('gallery', 'LinkTreeEntry')

    site = Site.objects.filter(slug=SITE_SLUG).first()
    if site is not None:
        changed = []
        # Only clear what still matches; anything edited since is somebody's work.
        for field, value in (('hours', HOURS), ('visit_notes', VISIT_NOTES),
                             ('about', ABOUT)):
            if getattr(site, field, '') == value:
                setattr(site, field, '')
                changed.append(field)
        if changed:
            site.save(update_fields=changed)

    for name, url, _order, _slug in LINKS:
        LinkTreeEntry.objects.filter(url=url, name=name).delete()


class Migration(migrations.Migration):

    dependencies = [('gallery', '0069_site_public_info')]

    operations = [migrations.RunPython(forwards, backwards)]
