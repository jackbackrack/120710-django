# Site directors

A director is an **admin for one venue and nothing beyond it**. Assigned by an admin on the
venue's edit page (`Site.directors`), and one person may direct several venues.

## What they can do

| | |
| --- | --- |
| Shows at their venue | create, edit, **delete**, transition status |
| Curators and jurors | assign, including themselves |
| Curation and jurying | score, select, reject, view reviews |
| Pickups and dropoffs | scheduling windows and the tracker |
| Artists and artworks | anything shown *or submitted* at their venue, plus anything they created |
| Their venue's settings | hours, closures, visit booking, About/Visit/Contact copy |
| Visits and event RSVPs | their venue's only, including its calendar feed |

And what they cannot: another venue's anything, creating or deleting venues, appointing
directors, campaigns and subscribers, Django admin.

**Mailing lists are deliberately not included yet.** When they are, the seam is
`Campaign.site` — the scoping already exists, it is the sending under the gallery's name
that wanted a decision first.

## Why the role hangs off User, not Artist

`Show.curators` is a many-to-many to **Artist**; `ShowJuror` keys on **User**. That split is
not an accident, and directing belongs on the second side of it:

- A curator is a public **credit** — the name on the show page — so it needs an Artist.
- Jurying and directing are **access**, and neither is printed anywhere.
- Artist profiles are public and demand a photo, country and postal code. Appointing a
  director should not publish them in the artists directory.
- Decisively: `ensure_signup_profile` links an unclaimed Artist to a new account **by
  matching email**. Rights held on an Artist row would transfer to whoever next signed up
  with that address.

## Two derived rules worth knowing

**Only `Show` knows about `Site`.** `Artist` and `Artwork` have no venue of their own — an
artist belongs to a venue because work of theirs was in a show there. So a director's reach
over people and pieces is derived, in `_directs_artists_work` and `_directs_artworks_show`.

Both check **`shows` and `submissions`**. `Artwork.shows` is the *accepted* relation,
populated when a submission is promoted; `submissions` is what is still being juried. An
earlier version checked only the first, which locked a director out of every open call they
were running — the state the work is in for the whole time they are handling it.

**`created_by` covers the gap before either exists.** A just-created artist or artwork is in
no show, so it matches no venue. `Artwork.created_by` already existed; `Artist.created_by`
was added for this. Without it a director could add somebody on an artist's behalf and be
unable to edit the result a second later.

A consequence, accepted deliberately: an artist who has shown at two venues can be edited by
either director. That is the same shared record two admins have always had.

## One curator, one definition

`views/visits.py` used to ask `user.groups.filter(name='curator')` while `permissions.py`
derived curator from curated shows — two disagreeing definitions of one role. The group
check is gone; **curator means "curates a show"** everywhere. If you have a Django group
called `curator`, nothing reads it any more.
