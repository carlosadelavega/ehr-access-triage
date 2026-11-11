"""Injected misuse (M1-M6) and self-access (P1).

Scenarios are added as chart requests next to the legitimate ones and go
through the same event machinery. What sets them apart is who opens whose
chart, never how the rows are made.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .encounters import Requests
from .population import DAY, HOUR, miles_between, street_of

SCENARIO_COLUMNS = [
    "scenario_id", "type", "group_id", "actor_user", "borrower_user", "target_patient",
    "start", "end", "covered", "evidence_in_context",
]


def inject(req: pd.DataFrame, staff: pd.DataFrame, patients: pd.DataFrame, rel: pd.DataFrame, enc: pd.DataFrame,
           orders: pd.DataFrame, shifts: pd.DataFrame, cfg, rng):
    """Return (extra requests, scenarios, covering requests, public events, notes for the manifest)."""
    W = cfg["window_days"] * DAY
    S, P = len(staff), len(patients)
    counts = cfg["scenario_counts"]
    lo_ev, hi_ev = cfg["misuse_events_per_visit"]
    out = Requests()
    scenarios: list[tuple] = []
    public_events: list[tuple] = []
    notes: dict = {}

    person = staff["pidx"].to_numpy()  # each staff member's own patient record
    home = staff["dept"].to_numpy()
    role = staff["role"].to_numpy()
    staff_of = np.full(P, -1)
    staff_of[person] = np.arange(S)

    # What legitimate work already connects a user to a patient.
    linked = np.zeros((S, P), dtype=bool)
    linked[req["actor"].to_numpy(), req["p"].to_numpy()] = True
    dept_link = np.zeros((S, P), dtype=bool)  # patient was seen in, or had an order sent to, the user's department
    for dept in cfg["departments"]:
        seen = np.zeros(P, dtype=bool)
        seen[enc.loc[enc["dept"] == dept, "p"].to_numpy()] = True
        seen[orders.loc[orders["performing_dept"] == dept, "p"].to_numpy()] = True
        dept_link[home == dept] = seen

    lat, lon = patients["lat"].to_numpy(), patients["lon"].to_numpy()
    miles = miles_between(lat[person][:, None], lon[person][:, None], lat[None, :], lon[None, :])
    street = pd.factorize(patients["address"].map(street_of) + "|" + patients["city"])[0]
    address = pd.factorize(patients["address"] + "|" + patients["city"])[0]
    surname = patients["last_name"].to_numpy()
    contact = patients["emergency_contact_id"].to_numpy()
    pid = patients["patient_id"].to_numpy()
    related = np.zeros((S, P), dtype=bool)
    for a, b in zip(rel["a"].to_numpy(), rel["b"].to_numpy()):
        for x, y in ((a, b), (b, a)):
            if staff_of[x] >= 0:
                related[staff_of[x], y] = True
    itself = np.zeros((S, P), dtype=bool)
    itself[np.arange(S), person] = True

    in_window = shifts[(shifts["start"] < W - 1200) & (shifts["end"] > 1200)]
    my_shifts = {u: g[["start", "end"]].to_numpy() for u, g in in_window.groupby("u")}

    def when(u: int, within: tuple | None = None) -> float:
        """A moment during one of the user's shifts, inside `within` if possible."""
        spans = my_shifts[u]
        if within is not None:
            hit = spans[(spans[:, 0] < within[1]) & (spans[:, 1] > within[0])]
            if len(hit):
                a, b = hit[rng.integers(len(hit))]
                a, b = max(a, within[0]), min(b, within[1])
                return float(a + rng.random() * (b - a))
        a, b = spans[rng.integers(len(spans))]
        a, b = max(a, 0) + 600, min(b, W) - 600
        return float(a + rng.random() * (b - a))

    def add(kind, u, p, times, evidence, group=""):
        sid = f"S{len(scenarios) + 1:03d}"
        for t in times:
            out.add(u, p, -1, t, t + 60, 1, kind, False, mode="view", scenario=sid, max_events=hi_ev)
        scenarios.append((sid, kind, group, u, -1, p, min(times), max(times), False, evidence))

    def snoop(kind, pairs, n, evidence, repeat=False, variant=True):
        """Inject n scenarios from candidate (user, patient) pairs with no legitimate link."""
        pairs = pairs[~linked[pairs[:, 0], pairs[:, 1]]]
        in_dept = dept_link[pairs[:, 0], pairs[:, 1]]
        pools = {True: pairs[in_dept], False: pairs[~in_dept]}
        pools = {k: v[rng.permutation(len(v))] for k, v in pools.items()}
        taken = {True: 0, False: 0}
        actors: set = set()
        made = hard = 0
        while made < n:
            want_hard = variant and rng.random() < cfg["target_in_actor_department_share"]
            flavour = want_hard if taken[want_hard] < len(pools[want_hard]) else not want_hard
            if taken[flavour] >= len(pools[flavour]):
                break
            u, p = (int(x) for x in pools[flavour][taken[flavour]])
            taken[flavour] += 1
            if u in actors or u not in my_shifts:
                continue
            actors.add(u)
            window = None
            if flavour:  # look while the patient is, or just was, in the actor's department
                stay = enc[(enc["p"] == p) & (enc["dept"] == home[u])]
                if len(stay):
                    window = (max(float(stay["start"].iloc[0]), 0.0), min(float(stay["stop"].iloc[0]) + DAY, W))
            visits = int(rng.integers(2, 5)) if repeat and rng.random() < cfg["misuse_repeat_prob"] else 1
            times = sorted(when(u, window) for _ in range(visits))
            add(kind, u, p, times, evidence(u, p) + ("; target seen in actor's department" if flavour else ""))
            made += 1
            hard += flavour
        notes[kind] = {"made": made, "target_in_actor_department": int(hard)}

    def pairs_where(mask):
        return np.argwhere(mask & ~itself)

    # M1: a colleague from the actor's own department.
    coworker = np.zeros((S, P), dtype=bool)
    for dept in cfg["departments"]:
        members = np.flatnonzero(home == dept)
        coworker[np.ix_(members, person[members])] = True
    snoop("M1", pairs_where(coworker), counts["M1"], lambda u, p: "same department in staff.csv")

    # M2a and M2b: relatives, with and without a shared surname and address.
    same_name = surname[person][:, None] == surname[None, :]
    same_home = address[person][:, None] == address[None, :]
    snoop("M2a", pairs_where(related & same_name & same_home), counts["M2a"],
          lambda u, p: "shared surname and address", repeat=True, variant=False)

    def contact_evidence(u, p):
        known = contact[person[u]] == pid[p] or contact[p] == pid[person[u]]
        return "emergency contact recorded" if known else "none"

    snoop("M2b", pairs_where(related & ~same_name & ~same_home), counts["M2b"], contact_evidence, variant=False)

    # M3: a neighbor, by the two Hedda et al. rules.
    near = (miles <= cfg["proximity_miles"]) | (street[person][:, None] == street[None, :])
    stranger = ~related & ~same_home & (staff_of < 0)[None, :]
    snoop("M3", pairs_where(near & stranger), counts["M3"],
          lambda u, p: f"lives {miles[u, p]:.2f} miles away" + (", same street" if street[person[u]] == street[p] else ""))

    # M4: a VIP.
    vip = np.zeros((S, P), dtype=bool)
    vip[:, patients["vip"].to_numpy()] = True
    snoop("M4", pairs_where(vip & ~related), counts["M4"], lambda u, p: "vip flag in patients.csv", repeat=True)

    # M5: after a news event, people across the hospital open one chart.
    newsworthy = enc[(enc["cls"] != "clinic") & (enc["start"] > 0) & (enc["start"] < W - 1.5 * DAY)]
    lo, hi = cfg["m5_users_per_event"]
    for k in range(counts["M5_events"]):
        row = newsworthy.iloc[int(rng.integers(len(newsworthy)))]
        p, t_event = int(row["p"]), float(row["start"]) + (1 + 5 * rng.random()) * HOUR
        span = (t_event, t_event + 12 * HOUR)
        around = [u for u, s in my_shifts.items() if not linked[u, p] and ((s[:, 0] < span[1]) & (s[:, 1] > span[0])).any()]
        for _ in range(20):
            crowd = rng.choice(around, min(int(rng.integers(lo, hi + 1)), len(around)), replace=False)
            if len(set(home[crowd])) >= 3:
                break
        group = f"N{k + 1}"
        public_events.append((group, p, t_event))
        for u in crowd:
            add("M5", int(u), p, [when(int(u), span)], "none, the news event is not in context", group)

    # M6: one account, two people. The borrower works their own patients
    # under the owner's login, from the borrower's own devices.
    r_actor, r_p = req["actor"].to_numpy(), req["p"].to_numpy()
    r_t0, r_t1 = req["t0"].to_numpy(), req["t1"].to_numpy()
    clinical = np.isin(role, ["nurse", "physician"])
    lo, hi = cfg["shared_credential_visits"]
    span_len = 1.5 * HOUR

    def borrow(kind, owner, borrower, t, evidence):
        mine = np.unique(r_p[(r_actor == borrower) & (r_t0 < t + span_len) & (r_t1 > t)])
        mine = mine[mine != person[owner]]  # the log must not show the owner in their own chart
        if not len(mine):
            return False
        sid = f"S{len(scenarios) + 1:03d}"
        times = t + np.sort(rng.random(int(rng.integers(lo, hi + 1)))) * span_len
        for x in times:
            out.add(borrower, int(rng.choice(mine)), -1, x, x + 60, 1, kind, False, account=owner, scenario=sid)
        scenarios.append((sid, kind, "", owner, borrower, -1, float(times[0]), float(times[-1]), False, evidence))
        return True

    def busy_with(t):
        """For each user, how many patients they are working around time t."""
        live = (r_t0 < t + span_len) & (r_t1 > t)
        pairs = np.unique(np.stack([r_actor[live], r_p[live]]), axis=1)
        return np.bincount(pairs[0], minlength=S)

    made = tries = 0
    while made < counts["M6a"] and tries < 500:  # overlap: owner and borrower both active
        tries += 1
        j = int(rng.integers(len(req)))
        owner, t = int(r_actor[j]), float(r_t0[j] + rng.random() * (r_t1[j] - r_t0[j]))
        if t > W - 2 * span_len:
            continue
        load = busy_with(t)
        ok = np.flatnonzero((load >= 3) & clinical & (home != home[owner]))
        if load[owner] < 3 or not len(ok):
            continue
        made += borrow("M6a", owner, int(rng.choice(ok)), t, "overlapping activity on devices in two departments")
    notes["M6a"] = {"made": made}

    made = tries = 0
    while made < counts["M6b"] and tries < 500:  # off shift: the owner is not at work
        tries += 1
        borrower = int(rng.choice(np.flatnonzero(clinical)))
        if borrower not in my_shifts:
            continue
        a, b = my_shifts[borrower][rng.integers(len(my_shifts[borrower]))]
        t = float(max(a, 0) + rng.random() * max(min(b, W) - span_len - max(a, 0), 1))
        if busy_with(t)[borrower] < 1:
            continue
        absent = [
            u for u in np.flatnonzero((home == home[borrower]) & (role != role[borrower]))
            if u not in my_shifts or not ((my_shifts[u][:, 0] < t + 3.5 * HOUR) & (my_shifts[u][:, 1] > t - 2 * HOUR)).any()
        ]
        if not absent:
            continue
        made += borrow("M6b", int(rng.choice(absent)), borrower, t, "account active while its owner is not scheduled")
    notes["M6b"] = {"made": made}

    # P1: staff opening their own record, sized to the published share.
    share = cfg["self_access_share"]
    legit_events = req["n"].sum() / (1 - cfg["visit_continue_prob"])
    visits = int(round(share / (1 - share) * legit_events / ((1 + hi_ev) / 2)))
    weight = rng.gamma(0.5, size=S) * np.array([u in my_shifts for u in range(S)])
    for u in rng.choice(S, visits, p=weight / weight.sum()):
        t = when(int(u))
        out.add(int(u), int(person[u]), -1, t, t + 60, 1, "self", True, mode="view", max_events=hi_ev)

    # A1: covering accesses, after Xu et al. 2023. Before the target access,
    # the actor opens the charts that look most like the target and most
    # like their own usual patients, judged by who else opens them. They
    # draw from their own random stream, so everything above is identical
    # with and without them.
    covers = Requests()
    table = pd.DataFrame(scenarios, columns=SCENARIO_COLUMNS)
    cover = cfg["covering"]
    if cover["enabled"]:
        cover_rng, main_rng = np.random.default_rng([cfg["seed"], 1]), rng
        rng = cover_rng  # `when` draws from rng
        opened = linked.astype(np.float32)  # users by patients
        size = opened.sum(axis=0)
        for row in table[table["type"].isin(["M1", "M2a", "M2b", "M3", "M4"])].itertuples():
            u, target = row.actor_user, row.target_patient

            def similarity(columns):
                """Jaccard index of accessor sets, every patient against the given ones."""
                both = opened.T @ opened[:, columns]
                union = size[:, None] + size[columns][None, :] - both
                return np.divide(both, union, out=np.zeros_like(both), where=union > 0)

            usual = np.flatnonzero(linked[u])
            score = (1 - cover["alpha"]) * similarity([target])[:, 0]
            if len(usual):
                score = score + cover["alpha"] * similarity(usual).mean(axis=1)
            score[usual] = score[target] = score[person[u]] = -1
            for p in np.argsort(-score, kind="stable")[: cover["accesses"]]:
                t = when(u, (max(row.start - cover["days_before"] * DAY, 0.0), row.start))
                covers.add(u, int(p), -1, t, t + 60, 1, "cover", False, scenario=row.scenario_id)
            table.loc[row.Index, "covered"] = True
        rng = main_rng

    return out.frame(), table, covers.frame(), \
        pd.DataFrame(public_events, columns=["public_event_id", "p", "t"]), notes
