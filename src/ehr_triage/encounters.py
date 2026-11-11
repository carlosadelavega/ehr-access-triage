"""Encounters in the window and the legitimate chart work they cause.

Work is recorded as requests: this user opens this patient's chart about n
times between t0 and t1, for this reason. `events.py` turns requests into
log events. Times are seconds from the window start.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .population import DAY, HOUR

CLASS_MAP = {
    "inpatient": "inpatient", "emergency": "emergency", "urgentcare": "emergency",
    "ambulatory": "clinic", "wellness": "clinic", "outpatient": "clinic",
}
ORDER_DEPT = {"medication": "PHARM", "lab": "LAB", "imaging": "RAD"}
REQUEST_COLUMNS = [
    "account", "actor", "p", "e", "t0", "t1", "n", "reason", "documented",
    "device_dept", "mode", "scenario", "max_events",
]


class Requests:
    """Append-only table of chart work."""

    def __init__(self):
        self.rows: list[tuple] = []

    def add(self, u, p, e, t0, t1, n, reason, documented, *, account=None, device_dept="",
            mode="role", scenario="", max_events=0):
        if n > 0 and t1 > t0:
            self.rows.append((u if account is None else account, u, p, e, t0, t1, int(n), reason,
                              bool(documented), device_dept, mode, scenario, max_events))

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.rows, columns=REQUEST_COLUMNS)


class Roster:
    """Who is working where and when."""

    def __init__(self, shifts: pd.DataFrame, weight: np.ndarray):
        self.shifts = shifts
        self.weight = weight  # per-person workload weight
        self._cache: dict = {}
        self.slot = {
            key: (g["u"].to_numpy(), g["floated"].to_numpy(), g["float_documented"].to_numpy())
            for key, g in shifts.groupby(["work_dept", "role", "start"])
        }

    def _group(self, dept: str, roles: tuple | None):
        key = (dept, roles)
        if key not in self._cache:
            g = self.shifts[self.shifts["work_dept"] == dept]
            if roles is not None:
                g = g[g["role"].isin(roles)]
            g = g.sort_values("start", kind="stable")
            self._cache[key] = tuple(g[c].to_numpy() for c in ("start", "end", "u", "floated", "float_documented"))
        return self._cache[key]

    def pick(self, dept: str, roles: tuple | None, t: float, rng, spread: float = HOUR, wait: bool = True):
        """Someone from `dept` on shift at `t`, else the first to start after it.

        Returns (user, effective time, floated, float_documented) or None.
        With wait=False, returns None when nobody is on shift at `t`.
        """
        start, end, u, floated, documented = self._group(dept, roles)
        def choose(candidates):
            w = self.weight[u[candidates]]
            return candidates[rng.choice(len(candidates), p=w / w.sum())]

        on = np.flatnonzero((start <= t) & (t < end))
        if len(on):
            i = choose(on)
            return int(u[i]), t, bool(floated[i]), bool(documented[i])
        if not wait:
            return None
        k = np.searchsorted(start, t)
        if k >= len(start):
            return None
        i = choose(np.flatnonzero(start == start[k]))
        return int(u[i]), float(start[i] + rng.random() * spread), bool(floated[i]), bool(documented[i])


def build_encounters(syn: dict, patients: pd.DataFrame, staff: pd.DataFrame, cfg, rng) -> tuple[pd.DataFrame, dict]:
    """Re-date a sample of each patient's recent Synthea encounters into the window."""
    days = cfg["window_days"]
    W = days * DAY
    ws = pd.Timestamp(cfg["window_start"])
    e = syn["encounters"]
    start = pd.to_datetime(e["START"], utc=True).dt.tz_localize(None)
    stop = pd.to_datetime(e["STOP"], utc=True).dt.tz_localize(None)
    pidx = e["PATIENT"].map(pd.Series(np.arange(len(patients)), index=patients["synthea_id"]))
    cls = e["ENCOUNTERCLASS"].map(CLASS_MAP)
    keep = (start >= ws - pd.DateOffset(years=cfg["encounter_lookback_years"])) & (start < ws) & cls.notna() & pidx.notna()
    pool = pd.DataFrame(
        {
            "syn_id": e["Id"], "p": pidx, "cls": cls, "dur": (stop - start).dt.total_seconds(),
            "reason": e["REASONDESCRIPTION"].fillna(""), "description": e["DESCRIPTION"].fillna(""),
        }
    )[keep]
    pool = pool.iloc[rng.permutation(len(pool))].reset_index(drop=True)
    pool["p"] = pool["p"].astype(int)
    stats: dict = {}
    parts = []

    # Inpatient: keep drawing stays until the units hold the target census.
    ip = pool[pool["cls"] == "inpatient"]
    lo, hi = (h * HOUR for h in cfg["inpatient_los_clip_hours"])
    units = list(cfg["inpatient_census"])
    census = np.array([cfg["inpatient_census"][u] for u in units], dtype=float)
    need = census.sum() * W
    got, taken, busy = 0.0, [], {}
    for row in ip.itertuples():
        if got >= need:
            break
        los = float(np.clip(row.dur, lo, hi))
        s = -los + rng.random() * (W + los)
        if any(s < b and s + los > a for a, b in busy.get(row.p, [])):
            continue  # one patient cannot be in two beds at once
        busy.setdefault(row.p, []).append((s, s + los))
        unit = units[int(rng.choice(len(units), p=census / census.sum()))]
        taken.append((row.Index, unit, s, s + los))
        got += min(s + los, W) - max(s, 0.0)
    t = pd.DataFrame(taken, columns=["i", "dept", "start", "stop"]).set_index("i")
    parts.append(pool.loc[t.index].assign(dept=t["dept"], start=t["start"], stop=t["stop"]))
    stats["inpatient_pool_used"] = round(len(taken) / max(len(ip), 1), 3)
    stats["inpatient_census_reached"] = round(got / need, 3)

    # Emergency: a fixed number of arrivals a day, fewer at night.
    ed = pool[pool["cls"] == "emergency"]
    n = min(int(cfg["ed_visits_per_day"] * days), len(ed))
    hour_w = np.array([cfg["ed_night_relative_rate"] if (h >= 23 or h < 7) else 1.0 for h in range(24)])
    s = rng.integers(days, size=n) * DAY + rng.choice(24, n, p=hour_w / hour_w.sum()) * HOUR + rng.random(n) * HOUR
    lo, hi = (h * HOUR for h in cfg["ed_los_clip_hours"])
    part = ed.iloc[:n].assign(dept="ED", start=s)
    parts.append(part.assign(stop=part["start"] + part["dur"].clip(lo, hi)))

    # Clinics: appointments per physician on weekdays. A clinic first takes
    # encounters whose reason matches its specialty, then any others.
    cl = pool[pool["cls"] == "clinic"]
    reason = cl["reason"].str.lower()
    spec = pd.Series("", index=cl.index)
    for dept, words in cfg["specialty_keywords"].items():
        hit = np.zeros(len(cl), dtype=bool)
        for w in words:
            hit |= reason.str.contains(w, regex=False).to_numpy()
        spec[hit & (spec == "").to_numpy()] = dept
    general = cl.index[(spec == "").to_numpy()]
    used = 0
    weekdays = np.array([d for d in range(days) if (ws.dayofweek + d) % 7 < 5])
    lo, hi = (m * 60 for m in cfg["clinic_los_clip_minutes"])
    matched = {}
    for dept, rate in cfg["clinic_visits_per_physician_day"].items():
        per_day = int(round(rate * cfg["staffing"][dept]["physician"]))  # per physician FTE
        total = per_day * len(weekdays)
        own = cl.index[(spec == dept).to_numpy()][:total]
        fill = general[used : used + total - len(own)]
        used += len(fill)
        idx = np.concatenate([own, fill])
        matched[dept] = round(len(own) / max(len(idx), 1), 3)
        s = np.repeat(weekdays, per_day)[: len(idx)] * DAY + 8 * HOUR + rng.random(len(idx)) * 8.5 * HOUR
        part = cl.loc[idx].assign(dept=dept, start=s)
        parts.append(part.assign(stop=part["start"] + part["dur"].clip(lo, hi)))
    stats["clinic_specialty_match"] = matched

    enc = pd.concat(parts)
    enc = enc.iloc[rng.permutation(len(enc))].reset_index(drop=True)
    enc["encounter_id"] = [f"E{i + 1:06d}" for i in range(len(enc))]
    stats["encounters"] = enc["cls"].value_counts().to_dict()
    return enc, stats


def assign_work(enc: pd.DataFrame, staff: pd.DataFrame, shifts: pd.DataFrame, syn: dict, cfg, rng):
    """Care teams, orders and every legitimate chart request."""
    W = cfg["window_days"] * DAY
    rates = cfg["visit_rates"]
    doc_p = cfg["documentation_prob"]
    weight = staff["load_weight"].to_numpy()
    roster = Roster(shifts, weight)
    wide = [d for d, v in cfg["departments"].items() if v["kind"] == "hospital_wide"]
    review = cfg["hospital_wide_review_prob"]
    away_prob = cfg["away_device_prob"]
    req = Requests()
    care_team: list[tuple] = []  # (e, u, team_role, start, stop)
    orders: list[tuple] = []  # (e, p, kind, ordered_by, performing_dept, t, description)
    role = staff["role"].to_numpy()
    home = staff["dept"].to_numpy()
    usual = staff["usual_shift"].to_numpy()
    users = np.arange(len(staff))
    by = {(d, r): users[(home == d) & (role == r)] for d in cfg["departments"] for r in set(role)}
    documented: dict[tuple, bool] = {}

    def weighted(users):
        w = weight[users]
        return int(rng.choice(users, p=w / w.sum()))

    def on_duty(dept: str, role: str, t: float) -> int:
        """Someone of this role at work in the department now, else anyone of that role."""
        got = roster.pick(dept, (role,), t, rng, wait=False)
        return got[0] if got else weighted(by[(dept, role)])

    def away(kind: str, e: int) -> str:
        """Department whose workstation is used, when the work is done at the patient's side."""
        return enc_dept[e] if enc_cls[e] != "clinic" and rng.random() < away_prob[kind] else ""

    def is_documented(u: int, e: int, p: float) -> bool:
        if (u, e) not in documented:
            documented[(u, e)] = bool(rng.random() < p)
        return documented[(u, e)]

    def nurse_work(u, floated, float_doc, e, p, t0, t1, n, prob, slot=None):
        """A nurse works a patient. Covering nurses never get a care-team row."""
        if floated:
            req.add(u, p, e, t0, t1, n, "coverage", float_doc, device_dept=enc_dept[e])
        elif is_documented(u, e, prob):
            req.add(u, p, e, t0, t1, n, "care_team", True)
            care_team.append((e, u, "nurse") + (slot or (t0, t1)))
        else:
            req.add(u, p, e, t0, t1, n, "undocumented_team", False)

    E = len(enc)
    enc_p = enc["p"].to_numpy()
    enc_dept = enc["dept"].to_numpy()
    enc_cls = enc["cls"].to_numpy()
    enc_start = enc["start"].to_numpy()
    enc_stop = enc["stop"].to_numpy()
    physician = np.full(E, -1)

    # ---- inpatient: shift by shift ----
    slot_len = 8 * HOUR
    slots = [d * DAY + h * HOUR for d in range(-1, cfg["window_days"]) for h in (7, 15, 23)]
    for unit in cfg["inpatient_census"]:
        mine = np.flatnonzero(enc_dept == unit)
        day_docs = by[(unit, "physician")][usual[by[(unit, "physician")]] == "day"]
        physician[mine] = [weighted(day_docs) for _ in mine]
        for e in mine:
            care_team.append((e, physician[e], "attending", enc_start[e], enc_stop[e]))
        affinity = rng.random((len(staff), len(mine)))  # keeps a nurse on the same patients across days
        for s0 in slots:
            s1 = s0 + slot_len
            if s1 <= 0 or s0 >= W:
                continue
            shift_name = {7: "day", 15: "evening", 23: "night"}[(s0 // HOUR) % 24]
            is_day = shift_name == "day"
            nurse_rate = rates["inpatient_nurse_per_shift"][shift_name]
            nurses, n_floated, n_doc = roster.slot.get((unit, "nurse", s0), (np.array([], int),) * 3)
            docs = roster.slot.get((unit, "physician", s0), (np.array([], int),) * 3)[0]
            clerks = roster.slot.get((unit, "clerk", s0), (np.array([], int),) * 3)[0]
            present = np.flatnonzero((enc_start[mine] < s1) & (enc_stop[mine] > s0))
            load = np.zeros(len(nurses), dtype=int)
            share = weight[nurses] / weight[nurses].sum() if len(nurses) else weight[nurses]
            cap = np.ceil(len(present) * share)  # each nurse's share of the unit, by workload weight
            for k in present:
                e = mine[k]
                p = enc_p[e]
                t0, t1 = max(s0, enc_start[e], 0.0), min(s1, enc_stop[e], W)
                if t1 <= t0:
                    continue
                f = (t1 - t0) / slot_len
                if len(nurses):
                    order = np.argsort(-affinity[nurses, k])
                    i = int(next((j for j in order if load[j] < cap[j]), order[0]))
                    load[i] += 1
                    nurse_work(nurses[i], n_floated[i], n_doc[i], e, p, t0, t1,
                               rng.poisson(nurse_rate * f), doc_p["inpatient_nurse"], (s0, s1))
                    if len(nurses) > 1 and rng.random() < cfg["helper_prob"]:
                        j = int(rng.choice(np.delete(np.arange(len(nurses)), i)))
                        n = rng.poisson(rates["inpatient_helper_per_shift"] * f)
                        if n_floated[j]:
                            req.add(nurses[j], p, e, t0, t1, n, "coverage", n_doc[j], device_dept=unit)
                        else:
                            req.add(nurses[j], p, e, t0, t1, n, "undocumented_team", False)
                att = physician[e]
                if is_day:
                    n = rng.poisson(rates["inpatient_attending_per_day"] * f)
                    if att in docs or len(docs) == 0:
                        req.add(att, p, e, t0, t1, n, "care_team", True)
                    else:  # the attending is off today, a colleague rounds instead
                        req.add(int(rng.choice(docs)), p, e, t0, t1, n, "undocumented_team", False)
                    if len(clerks):
                        req.add(int(rng.choice(clerks)), p, e, t0, t1, rng.poisson(rates["inpatient_clerk_per_day"] * f),
                                "undocumented_team", False)
                elif len(docs):
                    u = int(rng.choice(docs))
                    n = rng.poisson(rates["inpatient_covering_physician_per_shift"] * f)
                    req.add(u, p, e, t0, t1, n, "care_team" if u == att else "undocumented_team", u == att)

    # ---- emergency and clinic: visit by visit ----
    for e in np.flatnonzero(enc_cls != "inpatient"):
        p, dept, s, x = enc_p[e], enc_dept[e], enc_start[e], enc_stop[e]
        if enc_cls[e] == "emergency":
            got = roster.pick("ED", ("physician",), s, rng)
            if got:
                u, t, _, _ = got
                physician[e] = u
                care_team.append((e, u, "attending", s, x))
                req.add(u, p, e, t, min(max(x, t + HOUR), W), max(1, rng.poisson(rates["ed_physician"])), "care_team", True)
            got = roster.pick("ED", ("nurse",), s, rng)
            if got:
                u, t, floated, float_doc = got
                nurse_work(u, floated, float_doc, e, p, t, min(max(x, t + HOUR), W),
                           max(1, rng.poisson(rates["ed_nurse"])), doc_p["ed_nurse"])
            got = roster.pick("ED", ("clerk",), s, rng)
            if got:
                req.add(got[0], p, e, got[1], min(max(x, got[1] + HOUR), W), rng.poisson(rates["ed_clerk"]),
                        "undocumented_team", False)
        else:
            u = on_duty(dept, "physician", s)
            physician[e] = u
            care_team.append((e, u, "attending", s, x))
            req.add(u, p, e, max(s - 0.5 * HOUR, 0), min(x + 2 * HOUR, W), max(1, rng.poisson(rates["clinic_physician"])),
                    "care_team", True)
            u = on_duty(dept, "nurse", s)
            nurse_work(u, False, False, e, p, max(s - 0.25 * HOUR, 0), min(x, W),
                       max(1, rng.poisson(rates["clinic_nurse"])), doc_p["clinic_nurse"])
            u = on_duty(dept, "clerk", s)
            req.add(u, p, e, max(s - 20 * 60, 0), min(x + 15 * 60, W), max(1, rng.poisson(rates["clinic_clerk"])),
                    "undocumented_team", False)

    # ---- orders, consults and back-office work, for every encounter ----
    used = set(enc["syn_id"])
    meds = syn["medications"][syn["medications"]["ENCOUNTER"].isin(used)].groupby("ENCOUNTER")["DESCRIPTION"].apply(list).to_dict()
    imaging = syn["imaging"][syn["imaging"]["ENCOUNTER"].isin(used)].groupby("ENCOUNTER")["DESCRIPTION"].apply(list).to_dict()
    labs = set(syn["labs"]["ENCOUNTER"]) & used
    syn_id = enc["syn_id"].to_numpy()
    reason = enc["reason"].str.lower().to_numpy()
    caps = cfg["order_caps"]
    daily = cfg["inpatient_daily_orders"]
    generic = {"medication": "Inpatient medication order", "lab": "Daily laboratory panel", "imaging": "Inpatient imaging study"}
    consult_lo, consult_hi = cfg["consult_visits"]
    on_team = {(u, enc_p[e]) for e, u, *_ in care_team}

    def back_office(dept, roles, t, e, n):
        # Work that lands outside office hours is picked up some time in the
        # next two days, so weekends do not all pile onto Monday morning.
        got = None
        for _ in range(6):
            got = roster.pick(dept, roles, t, rng, wait=False)
            if got:
                break
            t += rng.random() * 2 * DAY
        got = got or roster.pick(dept, roles, t, rng, spread=4 * HOUR)
        if got and got[1] < W:
            req.add(got[0], enc_p[e], e, got[1], min(got[1] + 2 * HOUR, W), n, "back_office", False)

    for e in range(E):
        p, s, x, cls = enc_p[e], enc_start[e], enc_stop[e], enc_cls[e]
        t0, t1 = max(s, 0.0), min(x, W)
        items = []
        if s >= 0:  # orders written at the start of an encounter that began before the window are history
            first = min(x, s + 4 * HOUR)
            items += [("medication", d, s + rng.random() * (first - s)) for d in meds.get(syn_id[e], [])[: caps["medication"]]]
            items += [("imaging", d, s + rng.random() * (first - s)) for d in imaging.get(syn_id[e], [])[: caps["imaging"]]]
            if syn_id[e] in labs:
                items.append(("lab", "Laboratory panel", s + rng.random() * (first - s)))
        if cls == "inpatient":
            stay_days = (t1 - t0) / DAY
            for kind, rate in daily.items():
                items += [(kind, generic[kind], t0 + rng.random() * (t1 - t0)) for _ in range(rng.poisson(rate * stay_days))]
        for kind, description, t in items:
            if t >= W:
                continue
            orders.append((e, p, kind, physician[e], ORDER_DEPT[kind], t, description))
            got = roster.pick(ORDER_DEPT[kind], None, t, rng)
            if got and got[1] < W:
                n = 1 + int(rng.random() < cfg["order_second_visit_prob"])
                req.add(got[0], p, e, got[1], min(got[1] + 2 * HOUR, W), n, "order", True, device_dept=away("order", e))

        # Care management looks across the whole house. Nothing but the role documents it.
        looks = []
        if wide and cls == "inpatient":
            for d in range(int(np.ceil((t1 - t0) / DAY))):
                if rng.random() < review["inpatient_per_day"]:
                    a = t0 + d * DAY
                    looks.append(a + rng.random() * (min(a + DAY, t1) - a))
        elif wide and cls == "emergency" and rng.random() < review["emergency"]:
            looks.append(t0 + rng.random() * (t1 - t0))
        for t in looks:
            got = roster.pick(wide[0], None, t, rng)
            if got and got[1] < W:
                req.add(got[0], p, e, got[1], min(got[1] + 2 * HOUR, W), max(1, rng.poisson(rates["hospital_wide_review"])),
                        "hospital_wide", False, device_dept=away("hospital_wide", e))

        if cls != "clinic" and rng.random() < cfg["consult_prob"][cls]:
            match = [d for d, words in cfg["specialty_keywords"].items() if any(w in reason[e] for w in words)]
            if match and rng.random() < cfg["consult_specialty_match_prob"]:
                dept = match[0]
            else:
                dept = str(rng.choice(cfg["consult_departments"]))
            a = t0 + rng.random() * (t1 - t0)
            b = min(a + (12 + 24 * rng.random()) * HOUR, x + 12 * HOUR, W)
            # A consultant who is already on one of the patient's care teams
            # would be explained by that row, so pick one who is not.
            free = [u for u in by[(dept, "physician")] if (u, p) not in on_team]
            if free:
                req.add(weighted(np.array(free)), p, e, a, b, int(rng.integers(consult_lo, consult_hi + 1)),
                        "consult", False, mode="consult", device_dept=away("consult", e))

        if s >= 0 and (cls != "clinic" or rng.random() < cfg["registration_prob_clinic"]):
            got = roster.pick("REG", ("clerk",), s, rng)
            if got and got[1] < W:
                req.add(got[0], p, e, got[1], min(got[1] + 0.5 * HOUR, W), max(1, rng.poisson(rates["registration"])),
                        "back_office", False, device_dept=away("registration", e))
        if x < W:
            lo, hi = cfg["coding_delay_days"]
            back_office("HIM", ("coder",), x + (lo + rng.random() * (hi - lo)) * DAY, e, max(1, rng.poisson(rates["coding"])))
            lo, hi = cfg["billing_delay_days"]
            back_office("BILL", ("coder",), x + (lo + rng.random() * (hi - lo)) * DAY, e, max(1, rng.poisson(rates["billing"])))

    care = pd.DataFrame(care_team, columns=["e", "u", "team_role", "start", "stop"]).drop_duplicates(["e", "u", "team_role", "start"])
    order_df = pd.DataFrame(orders, columns=["e", "p", "kind", "ordered_by", "performing_dept", "t", "description"])
    return req, care, order_df, physician
