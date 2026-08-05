# Consignment agreements

The document an artist signs before dropping work off: what the gallery is liable for while
it holds the work, and what it takes if the work sells.

Scope is deliberately narrow. This covers **artists consigning their own work directly**.
Artists represented by another gallery are detected and stopped, not modelled — see
[Represented artists](#represented-artists) for why.

## What already existed

Most of the data was here before any of this, and two earlier decisions were made in
anticipation of it:

- `Artwork.replacement_cost` — collected on the artwork form, optional, with help text that
  already said *"Required before a piece can be consigned"*.
- `Artist.street/city/state` — optional at submission on purpose. `forms.py` says outright
  that *"the consignment flow is where they get asked"*, because requiring a home address
  from every entrant to an open call collects hundreds and uses almost none.
- `ArtistSchedule` and `ScheduleWindow` — drop-off and pickup, per show per artist, which is
  what the agreement's custody period is defined against.
- ReportLab, already generating placards.

What was missing is a commission **rate**, and the agreement itself.

## Where the rate lives

    Site.commission_rate     percent, nullable
    Show.commission_rate     percent, nullable — overrides the site's

The site sets the normal rate; a show overrides it for a benefit or members' show.

**Null is not zero.** Null means nobody has decided, and no agreement generates at all until
somebody does — a contract is the wrong place to discover what the gallery takes. Zero means a
show that deliberately takes nothing, and it is a real answer: the agreement then says "no
commission — the artist receives the full sale price" rather than printing "0%" and a row of
"gallery $0", which reads like a bug.

A show can be at more than one site. If it is, and the sites disagree, the rate is not
guessable and no agreement generates until a show-level rate is set. Failing loudly is the
only safe answer: the alternative is picking one at random and putting it in a contract.

That clause is temporary. `Show.sites` is a many-to-many that has never been used — every
show has exactly one site — and it is being collapsed to a foreign key, after which the rate
is simply the show's site's. This is kept until then rather than assuming, because "no show
has two sites today" is not the same as "no show can".

## Agreed value, not replacement cost

**The insured figure is the agreed value, and it defaults to the artwork's retail price.**

"Replacement cost" was the wrong name for what this number does. It reads to any artist as
materials-and-labour, and the help text on `Artwork.replacement_cost` has already been
rewritten once for exactly that reason — the old wording *"invited artists to state their
materials cost and under-value their own work"*. A field fighting its own label will keep
producing low numbers however it is worded.

Agreed value is also the term an actual policy would use, so the contract and any insurance
behind it can describe the same thing.

Defaulting to the retail price means:

- most artists type nothing, which is the point;
- the anchor is the artist's own asking price, so the default errs high rather than low;
- only works that are not for sale, or priced on request, need a number typed, because they
  have no price to inherit.

The field is `Artwork.agreed_value`, named for the one document that uses it. It was
`replacement_cost`, and the rename is the point: a field called replacement cost will keep
producing materials-and-labour figures however its label is worded, and having the artwork
form say one thing while the agreement says another is how an artist comes to believe they
are two different numbers.

It is the **override** now, not the primary — blank means "use my asking price", which is
usually right. Only work with no price needs a figure typed.

The old column is still there, unused and `editable=False`, and is dropped in a later deploy
alongside the `Show.sites` collapse. Railway runs migrations in pre-deploy while the old code
is still serving, and old code selecting a column that no longer exists 500s every artwork
page for the length of the deploy — see [railway-deploys.md](railway-deploys.md).

### Paid in full

A destroyed piece is paid at its full agreed value, **not** the agreed value less commission.

The standard practice is to deduct commission, on the reasoning that the artist should be
made whole for what a sale would have put in their pocket and the gallery should not profit
from a loss. This does the opposite deliberately: at these price points the difference is
tens to a few hundred dollars on an event that should be rare, and "we pay what we agreed,
not what we agreed minus our cut" is worth more to an emerging artist than the money is to
the gallery.

## An implausible value

There is **no cap**, because a cap refuses honest work that happens to be priced unusually.
Instead the agreement says the value is *agreed* — the artist proposes it, and the gallery may
query it or decline the piece before it is dropped off — and the staff dashboard flags any
figure more than `OUTLIER_RATIO` times the asking price, or over `OUTLIER_ABSOLUTE`, so
somebody sees it while refusing is still possible. Once the work has been accepted and the
agreement signed, the figure binds.

The artist never sees the threshold. It marks a row, it does not block anything.

## The signed thing is frozen

**The agreement renders live until it is signed, and never again.**

If the signed document read from `Artwork.replacement_cost` and `Site.commission_rate`, then
an artist editing a price next month would silently rewrite what they signed, and so would
any change to the gallery's own rate. A contract that rewrites itself is not a contract.

So signing captures a `snapshot`: the artist's name and address, every artwork with title,
year, medium, dimensions, price and replacement value, the commission rate, the show's dates
and venue, the drop-off and pickup dates, and the full rendered text of the terms. From that
moment the snapshot *is* the agreement. Live data is only ever used to build the next one.

There is deliberately **no `ConsignmentLine` model**. Before signing, the artwork list is
derived from the show; after signing it is in the snapshot. A third copy in a side table
would be a third thing to keep in step, and the one that drifts is always the one nobody
reads.

## There is no PDF in S3

`AWS_DEFAULT_ACL = 'public-read'` and `AWS_QUERYSTRING_AUTH = False`: everything in media is
world-readable at a stable URL. A signed agreement carries a home address and a signature, so
it cannot live there.

The PDF is therefore **rendered on demand from the snapshot**, behind the same permission
check as the artist's other private details — the artist themselves, curators of the show,
staff, and the site's directors, exactly as with `venmo`, email and phone. Nothing is written
to storage, so nothing can leak from it, and the rendering is deterministic because the
snapshot cannot change.

The artist gets their copy attached to the confirmation email. That is a copy sent to the
person it is about, which is the one distribution that needs no gate.

## The one page

The whole flow is a single page at `/show/<slug>/consign/`, reachable with a signed token so
no account is needed — the same `django.core.signing` pattern already used by RSVPs, visits
and campaigns, with the address in the token so a recycled primary key cannot open somebody
else's agreement. Requiring a login here would block the artists who most need to complete
it, and a mailed token link is how e-signature works everywhere else.

    Consignment — AFTER ALBERS, 120710
    Jules Bachrach · [1207 Tenth St] [Berkeley] [CA]        ← editable inline

    Untitled, 2026, oil on panel, 24 × 36 in
      Price $2,000    Replacement value [$1,500]            ← editable inline
      If it sells: gallery $500 · you $1,500

    Drop off by Aug 11 · Pick up by Oct 1
    [terms]
    ☐ I have read and agree.   Signed: [Jules Bachrach]     [Sign]

**Everything missing is fillable here.** The two things that will be absent are the street
address and the replacement values, and if the page sends the artist to the profile form and
then to each artwork form it has become a three-stop errand that people abandon. Saving those
inline writes them back to `Artist` and `Artwork`, so the answer is given once.

Showing the split **in dollars per piece** rather than as a percentage is what makes the
commission real to somebody reading it on a phone at a drop-off.

### What blocks signing

- a complete street address
- a replacement value on every listed work
- the representation question answered

Nothing else. In particular an unsigned agreement does **not** block drop-off: it shows as
outstanding on the staff dashboard and is chased by email, because turning an artist away at
the door over paperwork is worse than accepting the work and following up.

## The signature

Typed full name, an explicit affirmation checkbox, and the time, IP address and user agent
recorded alongside a hash of the snapshot. That is what an electronic signature needs to hold
up under ESIGN/UETA: intent to sign, attribution, and a fixed record of what was signed.

A drawn signature is not implemented. It adds perceived weight and no legal weight, and it is
worse on a laptop than typing.

## Versions, and what happens when the work changes

    status:  draft → signed → superseded
    version: 1, 2, 3 …

A signed agreement records the fingerprint of the facts it covers. If the artwork set, the
prices or the rate later differ, the agreement is shown as **out of date** and a new version
is generated for signing; signing v2 supersedes v1, and v1 is kept, because the whole point
of a signed record is that it survives.

Silent staleness is the alternative and it is worse: an agreement that covers three works
while five are on the wall is a document that will be produced in the one situation where it
matters and found not to cover the piece in question.

## When responsibility ends

    Site.custody_grace_days   default 7

"Until it is collected" has no end. An artist who never comes back would leave the gallery
liable for their work indefinitely, so the agreement states two dates: the pickup date the
artist is asked to meet, and a cutoff a set number of days later after which uncollected work
is held **at the artist's risk**. The gallery still looks after it and still gets in touch;
it just no longer pays the agreed value if something happens to it.

Both dates are frozen into the snapshot and stated on the page before anybody signs, not only
in the PDF afterwards — the cutoff is the term an artist is most likely to be surprised by
later.

## The same work in several shows

Most of this collection has been shown more than once, and `Artwork.replacement_cost` is a
single field shared across every show the piece is in. Setting an agreed value while signing
for this autumn's show therefore changes the number last year's agreement was measured
against.

The freeze means the old signed document is unaffected — but the staleness check would still
notice the difference and ask the artist to re-sign an agreement for a show that ended months
ago, about work the gallery has already given back. So `is_out_of_date` stops looking once
custody has ended. There is nothing left to agree about a show that is over.

## Collaborative works

A work credited to two artists appears on both artists' agreements and both must sign. The
agreement is per artist, so each is agreeing about their own liability and their own share,
and neither can bind the other.

## Represented artists

    Artist.is_represented        BooleanField(null=True)   — null means never asked
    Artist.representing_gallery  CharField(blank=True)

Asked on the artist profile, and asked again on the consignment page when it has never been
answered. Null and false are kept distinct on purpose: "we have not asked" and "they said no"
are different states, and only one of them is safe to act on.

When an artist is represented, **no artist-direct agreement is generated**. This is not
tidiness. Under exclusive agency the representing gallery holds sole authority to consign, so
an artist-direct agreement signed by that artist is worth nothing. The software flags it and
the consignment is handled on paper, which is what already happens.

The organisation-to-organisation case is **not modelled**, on evidence: the NIAD agreement for
AFTER ALBERS is gallery-to-gallery — from NIAD to 120710, signed by NIAD as "the sole and
exclusive representative and agent of Artist", with the artist not a party. It prices work by
a **net price per artwork** that the consignee owes, not by a commission rate; it requires
insurance at **full retail value including transit**, not at an artist-set replacement value;
and one agreement covers several artists at once. None of that fits the artist-direct shape,
all of it would have to be built to fit one show, and that show is already papered.

If it ever becomes routine, the things to build are a consignor organisation with standing
terms, net price per work, and the ability to attach their counter-signed PDF rather than
generate one — a partner gallery will not use this form. Their obligations are also mostly
things the platform could track and a filed PDF cannot: a credit line on labels (placards
carry none today), payment within N days of sale, return of unsold work within N days,
collector name and city.

## Deliberately not built

Representation splits and net prices; framing costs deducted before commission; reproduction
and promotional-use permissions; provenance and collector visibility, which belong with the
sale flow rather than here; storage fees for uncollected work, which is a policy decision
before it is a feature.

## Liability, which is not a code question

The agreement commits the gallery to paying the stated replacement value for loss, theft or
damage between drop-off and pickup. Two things are worth confirming outside the software:
whether a policy actually covers **property of others** at those values — many small
commercial policies exclude it — and whether the wording should say the gallery *assumes
liability up to* the stated value rather than *insures*, which implies a policy exists.

The artist sets the figure and nothing bounds it, so a show's exposure is the sum of
self-declared values. The staff dashboard totals it per show for exactly this reason. A
per-piece cap or approval above a threshold is possible but puts a rejection in the artist's
path, so it is not built until the totals justify it.
