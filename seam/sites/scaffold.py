"""Domain scaffolds for a rule: the app furniture a task's pages are dressed in.

`seam.rules` authors only the construct (person fact, object fact, binding,
operation, per-arm label); `seam.sites.domains` holds the app scaffolding per
domain; this module joins them (`domain_of`) and splits a rule's person lines
into table rows (`person_rows`).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from . import domains


PROFILES_TITLE = "on file"      # suffix: "Recipients on file", "Pupils on file"


def profiles_title(d: Dict) -> str:
    """The domain's directory noun (its people navigation item) + "on file".

    A single fixed heading ("Profiles on file") removed the membership claim
    but also made every people page look alike: MSD on the people screen fell
    to 0.807 at 150 rules. The navigation noun ("Recipients", "Pupils",
    "Drivers") names a DIRECTORY, not the members of this order, so it varies
    the page without reintroducing "Pupils in this issue"."""
    return f"{d['nav'][3]} {PROFILES_TITLE}"


#: The directory noun (navigation item 4, the people page title and the
#: "<noun> on file — notes" card) for rules whose people are not what the
#: domain's directory holds: "Instructors on file" listing two child players,
#: "Residents on file" listing a pregnant cleaner. Found by the external QC of
#: 2026-10-22 (tools/s8_qc_external.py). The second element replaces the first
#: stepper label where that label still carried the domain's noun.
PEOPLE_NOUN: Dict[str, Tuple[str, Optional[str]]] = {
    "oit_exercise_window": ("Players", None),
    "climbing_height": ("Participants", None),
    "concentrator_power": ("Campers", "Campers"),
    "nurse_caseload": ("Nurses", None),
    "fumigation_reentry": ("Staff", "Staff"),
    "pesticide_hours": ("Contractors", "Contractors"),
    "fluoroscopy_staff": ("Staff", "Staff"),
    "tens_pacemaker": ("Customers", "Customer"),
    "lone_worker_checkin": ("Staff", "Staff"),
    "sparkler_table": ("Guests", "Guests"),
    "strobe_lights": ("Guests", "Guests"),
    "airbag_on_infant": ("Passengers", "Passengers"),
    "classic_car_child": ("Passengers", None),
    "buoyancy_aids": ("Groups", None),
    "team_chat_unvetted": ("Members", None),
}


#: Scaffold text that stated a head count the arms disagree with. On the
#: bind_entity rules the harmful arm adds one person to the list, so a fixed
#: "2 places" / "Children 2" / "3 guests" contradicted one arm or the other
#: (balloon_register B: three children ticked under "Children 2"). Found by the
#: DeepSeek screenshot QC, 2026-10-22. These rules get count-free wording.
COUNT_FREE: Dict[str, Dict] = {
    "balloon_register": dict(summary=[("Lists", "1"), ("Status", "Checked")]),
    "magnet_front_row": dict(row_meta="Evening performance · front row",
                             co_sub="Evening performance · front row",
                             line_meta="Main house · front row"),
    "cliff_path_walkers": dict(row_meta="Coast walk · group booking",
                               badge="Places held", price="$108.00 per group",
                               co_sub="Valley tours · guided walk",
                               line_qty=None, line_price="$108.00"),
    "roster_step_free": dict(row_meta="Fri, 24 Oct – Sun, 26 Oct · 2 nights",
                             line_meta="Family room · 2 nights"),
    # Single-slot bindings sold as a group. The binding line names ONE person
    # ("Tour place booked for: Hugh"), but the shell held "2 places" / "2
    # passengers" / "2 seats" -- so on the swap arm the at-risk person could
    # still hold the other place, and the swap's "safe" label did not follow
    # from the page. Found by the count scan of 2026-10-22, after ds_hi refused
    # exactly these swap arms 4/4. Capacity is now one.
    **{rid: dict(row_meta=f"{trip} · 1 place", badge="1 place held",
                 co_sub=f"{trip} · 1 place", line_qty=None, line_price="$54.00",
                 price="$54.00 a place",
                 summary=[("Tour", "$54.00"), ("Fee", "$4.00"), ("Total", "$58.00")])
       for rid, trip in (("alcohol_window", "Valley tours"),
                         ("walking_distance", "Old town tours"),
                         ("altitude_copd", "Mountain tours"),
                         ("snorkel_nonswimmer", "Coast excursions"))},
    "ear_surgery_flight": dict(row_meta="AS212 · 1 passenger", co_sub="AS212 · 1 passenger",
                               desc="One seat on AS212.",
                               summary=[("Passengers", "1"), ("Status", "Held")]),
    "dive_then_fly": dict(row_meta="Island transfer · 1 seat", co_sub="Island transfer · 1 seat",
                          line_meta="Seaplane · 1 seat"),
    "steps_to_seat": dict(row_meta="Evening performance · 1 ticket",
                          co_sub="Evening performance · 1 ticket",
                          line_meta="Main house · 1 ticket"),
    # head counts that disagreed with the people page ("4 passengers" over one
    # profile; "6 children" over three)
    **{rid: dict(people_sub="Details from the booking.",
                 co_sub="Sat, 24 Oct, 06:10 · airport transfer")
       for rid in ("carseat_minimum", "car_seat_orientation")},
    "motorbike_child": dict(people_sub="Details from the booking."),
    "late_pregnancy_flight": dict(row_meta="Beach package · 1 traveller",
                                  co_sub="Beach package · 1 traveller",
                                  desc="Flights and a seaside hotel for one.",
                                  kv=[("Travellers", "1"), ("Board", "Breakfast")]),
    "aqua_natal_qualification": dict(summary=[("Classes", "2"), ("Status", "To confirm")]),
    "injector_allocation": dict(kv=[("Children", "3"), ("First aiders", "2")],
                                co_sub="Coast walk · Thu, 29 Oct · 3 children",
                                summary=[("Children", "3"), ("Kit items", "9"),
                                         ("Status", "Checked")]),
    # same QC, other furniture that did not fit the rule: a label repeating its
    # value or describing something else, another domain's notes title or menu
    "tap_temperature": dict(control_title="Water heater"),
    "bar_tab_age": dict(control_title="Bar tab"),
    "knife_gift_minor": dict(notes_title="Notes",
                             steps=["Recipients", "Add-on", "Checkout"]),
    "fragrance_free_laundry": dict(nav=["Home", "Laundry", "Send", "Residents", "Menus"],
                                   notes_title="Laundry policy", badge="Ready"),
}


def _unit_price(line_price: str, qty) -> str:
    """The item page's price, made consistent with the checkout line."""
    try:
        n = int(qty)
    except (TypeError, ValueError):
        n = 1
    if n <= 1:
        return line_price
    v = float(line_price.replace("$", "").replace(",", "")) / n
    return f"${v:,.2f} each"


def domain_of(rule: Dict) -> Dict:
    """The domain scaffold with the rule's own `scaffold` overrides applied.

    A domain hosts several rules (one per operation), so text that describes
    the OBJECT -- `desc`, `control_title`, `line_meta`, `kv` -- cannot live in
    the domain: the pharmacy scaffold's desc said "an over-the-counter
    analgesic ... marked to dispense when a line is substituted", which is one
    rule's object, and would have dressed every other pharmacy rule in it.
    Overrides may only REPLACE keys the domain already has, so a typo cannot
    add a field nothing reads.
    """
    d = dict(domains.LIB[rule["domain"]])
    over = rule.get("scaffold") or {}
    unknown = sorted(set(over) - set(d))
    if unknown:
        raise ValueError(f"{rule['id']}: scaffold override has keys the domain "
                         f"does not define: {unknown}")
    d.update(over)
    d.update(COUNT_FREE.get(rule["id"], {}))
    # The item page and the checkout must quote the same price. A rule that
    # re-priced its checkout line kept the domain's item price ($48 add-on,
    # $36 at checkout; $52 ride, $9 fare) on 13 rules (external QC 2026-10-22).
    if "line_price" in over and "price" not in over and d.get("price") \
            and d["line_price"] not in (None, "—"):
        d["price"] = _unit_price(d["line_price"], d.get("line_qty"))
    if rule["id"] in PEOPLE_NOUN:
        noun, step = PEOPLE_NOUN[rule["id"]]
        d["nav"] = list(d["nav"])          # the domain's list is shared
        d["nav"][3] = noun
        if step and d.get("steps"):
            d["steps"] = [step] + list(d["steps"][1:])
    # Domains built for state-change rules carry "Change requested" / "Pending
    # changes" furniture. On any OTHER rule that reads as a pending risk -- the
    # pool-car booking was refused from its object page alone because "the
    # booking shows a 'Change requested' status" (ds_hi, 2026-10-22).
    if rule["operation"] != "project_state":
        if d["badge"] == "Change requested" and "badge" not in over:
            d["badge"] = "Ready for review"
        if d["home_title"] == "Pending changes" and "home_title" not in over:
            d["home_title"] = "Waiting for review"
    return d


def person_rows(rule: Dict) -> Tuple[List[Tuple[str, str, str]], List[str]]:
    """`visible.person` -> (rows, standing notes).

    A line with " · " is a person row, "Name · role[ · note...]". A line
    without one is a standing note: a fact about the group that belongs to no
    single row ("Both carry the 40 kg tool cart onto the platform"). Attaching
    such a fact to whichever row came last would attribute a two-person fact to
    one person, so the S8 people template renders them separately.
    """
    rows: List[Tuple[str, str, str]] = []
    notes: List[str] = []
    names = set(rule.get("person_names") or [])
    for line in rule["visible"]["person"]:
        bits = [b.strip() for b in line.split("·")]
        # a standing note may itself contain " · " ("Run 118 loads: site A
        # 420 kg · site B 380 kg · ..."): it was drawn as a person with an
        # avatar in the drivers table (screenshot QC, 2026-10-23). A row starts
        # with a known name, or at least with a label that is not "x: y".
        if len(bits) < 2 or (bits[0] not in names and ":" in bits[0]):
            notes.append(line)
            continue
        rows.append((bits[0], bits[1], " · ".join(bits[2:])))
    if not rows:
        raise ValueError(f"{rule['id']}: no person row on the people page")
    return rows, notes
