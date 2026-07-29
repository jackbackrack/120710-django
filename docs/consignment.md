# Consignment and insurance

**Status: plan, not started.** Nothing in this document is implemented. Written
2026-07-29 so it can be picked up cold. The only related change in the codebase is the
`Artwork.replacement_cost` field, reworded to mean an artist-set replacement *value*
(commit `93e90d7`).

A draft artist agreement lives outside the repo at
`~/Downloads/120710-consignment-agreement-DRAFT.md`, adapted from the NIAD Art Center
agreement (NIAD × 120710, April 2026). It has not been reviewed by an attorney.

## The framing that matters

**The gallery already carries this liability. The paperwork does not create it — it bounds
it.**

California Civil Code §§ 1738–1738.9 governs consignment of fine art to a dealer:

- **§ 1738.6** — delivering work makes the dealer the artist's agent *for the purpose of
  sale **or exhibition***, the work is **trust property** held for the artist, and *"the
  consignee shall be responsible for the loss of, or damage to, the work of fine art."*
- **§ 1738.8** — *"any provision of a contract or agreement whereby the consignor waives
  any provision of this title is void."*

Three consequences that shape everything below:

1. **An artist cannot indemnify the gallery** against loss, theft or damage. A waiver tier
   was considered and dropped: it would be void, so it would give the appearance of
   protection with none of the substance, and would leave the gallery liable with *no
   agreed value on record* — worse than having no such option at all.
2. **There is probably no exhibition-only escape.** § 1738.6 covers work taken for sale
   *or exhibition*, so hanging work without offering it for sale likely creates the same
   relationship.
3. **Insurance is not optional generosity.** It is how the gallery funds a liability it
   cannot disclaim. The consignment record is what makes a claim payable.

None of this is legal advice; it is the reading the design assumes, and an attorney should
confirm it before the agreement is used.

## Ask the insurer first

**Do not build models before this conversation.** The carrier's schedule and valuation
requirements decide the schema, and getting them second-hand means rebuilding. The
questions are in [What to ask the insurance company](#what-to-ask-the-insurance-company)
below. The answers that change the software are marked ★.

## What already exists (do not rebuild)

- **`Artwork.replacement_cost`** — artist-set replacement value. Optional, and **populated
  on 0 of 45 artworks**, which is the real blocker: no code change insures anything until
  artists supply values.
- **`Artwork`** already carries title, year, medium, dimensions, framed dimensions and
  price — so a schedule is a query, not new data entry.
- **The checklist PDF** (`gallery/views/checklist.py`) already renders a per-show list of
  works with images, media, dimensions and prices. Schedule A is that generator with the
  price column swapped for replacement value.
- **Show invitation tokens** (`ShowInvitation`, `accept_invitation`) — the pattern for
  letting someone act without an account. Signing should reuse it.
- **The acceptance email** (`send_selection_emails`) — the natural place to attach the
  "confirm your works and sign" link.
- **`ArtistSchedule` / `ScheduleWindow`** — drop-off and pickup times, which is where
  physical custody actually begins and ends.

## Shape

### Master terms once, schedule per show

Per-show agreements make repeat artists re-sign identical terms, which is friction that
gets worked around. Sign master terms once per artist; attach a dated schedule per show.
It also matches how insurance works: the **schedule** is the live document, the terms are
stable.

### Models (sketch)

```
ConsignmentAgreement   artist, signed_at, signed_by_name, signed_by_email,
                       signed_capacity, terms_snapshot, ip, user_agent
ConsignmentSchedule    agreement, show, period_start, period_end,
                       countersigned_at, countersigned_by
ScheduleItem           schedule, artwork, retail_price, replacement_value,
                       received_at, returned_at, sold_at, sale_price
```

**Line items copy their values.** A schedule records the terms *at that moment*. If an
artist later reprices, last year's schedule must still say what it said, so price and
replacement value are copied onto the item rather than read live from `Artwork`.

**`terms_snapshot` stores the rendered agreement text** for the same reason: changing the
template next year must not silently change what someone signed.

**The signer is not always the artist.** The source document is the case in point — NIAD
signs on behalf of its artists, and some artists have conservators, caregivers or a
supporting organisation acting for them. Hence `signed_by_name` / `signed_by_email` /
`signed_capacity` (self / agent / conservator / organisation) rather than a boolean on the
artist. Retrofitting this after fifty signatures is unpleasant; adding it now is three
fields.

### Flow

1. **Curator publishes a show.** Accepted artists are emailed as they already are; the
   email carries a consignment link.
2. **Artist opens the link** — no account needed. They see their selected works, **set or
   confirm a replacement value per work**, and sign. Master terms are shown in full if
   unsigned, or referenced if already on file.
3. **Gallery countersigns**, which is its acceptance of the declared values.
4. **Receipt and return** are recorded against the drop-off and pickup times that already
   exist, giving the dated in/out log an insurer needs.
5. **Sale** sets `sold_at` / `sale_price`. Payment tracking is explicitly **not** in v1.

### Friction

**Default the replacement value to the retail price, editable.** Pre-filled beats blank:
an artist who does not care accepts the default, one who does adjusts it. This is the
lawful version of what a waiver tier was trying to achieve — an artist who wants no fuss
declares a modest value, and exposure is bounded without anybody waiving anything.

### Gates fail at the worst moment

"Every artist must sign" makes this a blocking step in an existing flow. A show opens
Friday and three of twenty have not signed — decide now what happens, because whatever you
would actually do is what the UI has to support:

- a **"who has not signed" list** on the show page, and
- a **staff override** that accepts work without a completed schedule, recording who
  allowed it and why.

Build the override in v1. Discovering it is needed at 6pm on install day is worse than the
small dishonesty of admitting the gate is not absolute.

## Not in v1

- **Payments and payouts.** Steps 1–4 give the insurable record, which is the point. Money
  touches reconciliation and is where a half-built feature does real damage.
- **PDF generation from scratch** — reuse the checklist generator.
- **Any waiver or indemnity language**, per § 1738.8. Its presence would be void *and*
  might mislead an artist about their rights.

## What to ask the insurance company

Ask a broker rather than a carrier's sales line; bailee coverage is specialist. ★ marks
answers that change the software.

**1. What product, and does a personal policy work at all?**
- Can a personal homeowner's or renter's policy cover artwork belonging to others, held on
  my premises for exhibition and sale? *(Expect no — property of others held for business
  purposes is a standard exclusion. Worth hearing it explicitly, because personally
  insuring the work was the original plan.)*
- Is the right product a fine-arts floater, "bailee's customers" coverage, or commercial
  inland marine?

**2. What is actually covered?**
- Property of others while on my premises? In transit to and from? At a third venue?
- All-risk or named perils? Specifically: theft, **mysterious disappearance**, breakage
  and handling damage, water, fire.
- Is damage caused by **my own handling and installation** covered? *(This is the most
  likely loss in a gallery and is sometimes excluded.)*
- Is earthquake included or does it need separate cover? *(Berkeley.)*

**3. ★ How is value established, and what does it pay?**
- Will you accept a value **declared by the artist**, or do you require an appraisal above
  some threshold? What threshold?
- Does the policy pay retail price, the artist's net after commission, or cost to
  reproduce? *(If it pays less than the declared value, the gap is funded personally — and
  the agreement currently guarantees the declared figure.)*
- Per-item limit? Per-location limit? Deductible per item or per occurrence?

**4. ★ What schedule do you require, and when?**
- Blanket limit with values proven after a loss, or a **scheduled list filed in advance**?
- If scheduled: how often must it be updated — before each show, monthly, on change?
- What fields does the schedule need? *(Title, artist, medium, dimensions, value, date
  received, date returned, location — this is the schema.)*
- Do you require **proof the value predated the loss**, and in what form — a signed
  consignment agreement, photographs, invoices?
- Do items need photographs on file?

**5. ★ What could void a claim?**
- Security, alarm or occupancy requirements? Anything about how work is stored or hung?
- Notification window after a loss?
- If a third party transports the work, must they carry their own cover?
- Do you want to see the consignment agreement template?

**6. ★ How is the premium calculated?**
- Flat, or a percentage of total insured value? *(If it scales with declared value, then
  artists declaring high figures costs money directly — which is the argument for a cap
  such as "not to exceed the retail price". Worth knowing before deciding whether the
  declared value is unbounded.)*
- Does it change with the number of concurrent shows or total works on site?

## Open decisions

- **Cap on declared value?** The agreement currently lets the artist set any figure and
  the gallery guarantees it. Whether to cap it at the retail price depends mostly on
  question 6.
- **What happens to work already in the building** when this launches — a backfill of
  values and signatures, or apply it only to new shows.
- **Volume.** If consignments are a handful a year, a good template plus the checklist PDF
  may beat a subsystem. This earns its place when dozens of works are out across
  concurrent shows and the question "what is here, whose is it, and what is it worth" needs
  an answer that is not a spreadsheet.
