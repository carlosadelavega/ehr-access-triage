"""People and places: patients with households, staff, devices, schedule, actions."""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

DAY = 86400
HOUR = 3600
EARTH_RADIUS_MILES = 3958.8


def load_synthea(slim_dir: str | Path) -> dict[str, pd.DataFrame]:
    d = Path(slim_dir)
    names = ["patients", "encounters", "medications", "imaging", "labs"]
    return {n: pd.read_csv(d / f"{n}.csv", dtype=str) for n in names}


def street_of(address: str) -> str:
    """Street name without the house number or a trailing unit."""
    s = re.sub(r"^\d+\s+", "", address.strip())
    return re.sub(r"\s+(Suite|Apt|Unit)\s+\S+$", "", s).lower()


def miles_between(lat1, lon1, lat2, lon2):
    """Haversine distance. Inputs broadcast."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))


def build_patients(syn_patients: pd.DataFrame, cfg, rng) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Patients with the household overlay, and the true relationships (labels)."""
    p = syn_patients.iloc[rng.permutation(len(syn_patients))].reset_index(drop=True)
    p = p.rename(
        columns={
            "Id": "synthea_id", "BIRTHDATE": "birth_date", "FIRST": "first_name", "LAST": "last_name",
            "GENDER": "gender", "ADDRESS": "address", "CITY": "city", "ZIP": "zip", "LAT": "lat", "LON": "lon",
        }
    )
    n = len(p)
    p["patient_id"] = [f"P{i + 1:05d}" for i in range(n)]
    p["lat"] = p["lat"].astype(float)
    p["lon"] = p["lon"].astype(float)
    age = ((pd.Timestamp(cfg["window_start"]) - pd.to_datetime(p["birth_date"])).dt.days / 365.25).to_numpy()
    p["age"] = age
    hh = cfg["households"]
    place = ["address", "city", "zip", "lat", "lon"]
    household = np.full(n, -1)
    rel = []  # (a, b, kind, same_household)

    def kind_of(a: int, b: int, spouse_ok: bool) -> str:
        if abs(age[a] - age[b]) >= 18:
            return "parent_child"
        return "spouse" if spouse_ok and min(age[a], age[b]) >= 18 else "sibling"

    # Households: walk a shuffled pool and cut it into groups. The oldest
    # member heads the group and gives it an address and, usually, a surname.
    pool = rng.permutation(np.flatnonzero(rng.random(n) < hh["multi_person_share"]))
    sizes = np.array(list(hh["size_weights"]), dtype=int)
    weights = np.array(list(hh["size_weights"].values()), dtype=float)
    i = 0
    hid = 0
    while i < len(pool):
        size = int(rng.choice(sizes, p=weights / weights.sum()))
        group = pool[i : i + size]
        i += size
        head = group[np.argmax(age[group])]
        if len(group) < 2 or age[head] < 18:
            continue
        others = sorted((m for m in group if m != head), key=lambda m: -age[m])
        household[group] = hid
        hid += 1
        for m in others:
            p.loc[m, place] = p.loc[head, place].to_numpy()
            if rng.random() < hh["same_surname_prob"]:
                p.loc[m, "last_name"] = p.loc[head, "last_name"]
        members = [head] + others
        for x in range(len(members)):
            for y in range(x + 1, len(members)):
                rel.append((members[x], members[y], kind_of(members[x], members[y], x == 0 and y == 1), True))

    # Relatives who live apart. They share a surname only some of the time.
    adults = np.flatnonzero(age >= 18)
    related = {(a, b) for a, b, _, _ in rel} | {(b, a) for a, b, _, _ in rel}
    renamed = set()
    for _ in range(int(hh["relatives_apart_share"] * len(adults))):
        a, b = (int(x) for x in rng.choice(adults, 2, replace=False))
        same_home = household[a] >= 0 and household[a] == household[b]
        if same_home or (a, b) in related:
            continue
        related |= {(a, b), (b, a)}
        rel.append((a, b, kind_of(a, b, False), False))
        if rng.random() < hh["apart_same_surname_prob"] and household[b] < 0 and b not in renamed:
            p.loc[b, "last_name"] = p.loc[a, "last_name"]
            renamed.add(b)

    # A hospital knows an emergency contact for only some true ties.
    contact = np.full(n, -1)
    for a, b, _, _ in rel:
        for x, y in ((a, b), (b, a)):
            if contact[x] < 0 and rng.random() < hh["emergency_contact_prob"]:
                contact[x] = y
    ids = p["patient_id"].to_numpy()
    p["emergency_contact_id"] = [ids[c] if c >= 0 else "" for c in contact]
    p["vip"] = rng.random(n) < cfg["vip_share"]
    p["household"] = household
    relationships = pd.DataFrame(rel, columns=["a", "b", "kind", "same_household"])
    return p, relationships


def build_staff(patients: pd.DataFrame, cfg, rng) -> pd.DataFrame:
    """Staff are Synthea adults, so every staff member also has a patient record."""
    lo, hi = cfg["staff_age_range"]
    slots = [(dept, role) for dept, roles in cfg["staffing"].items() for role, k in roles.items() for _ in range(k)]
    eligible = np.flatnonzero((patients["age"] >= lo) & (patients["age"] < hi))
    people = rng.choice(eligible, len(slots), replace=False)
    names = list(cfg["shift_weights"])
    probs = np.array(list(cfg["shift_weights"].values()))
    probs = probs / probs.sum()
    rows = []
    turn: dict[tuple, int] = {}
    for (dept, role), pidx in zip(slots, people):
        k = turn.get((dept, role), 0)
        turn[(dept, role)] = k + 1
        if cfg["departments"][dept]["hours"] == "office":
            shift = "office"
        elif role == "physician":
            pattern = cfg["physician_shift_pattern"]
            pattern = pattern.get(dept, pattern["default"])
            shift = pattern[k % len(pattern)]
        else:
            # Spread each group over the shifts in proportion to the weights,
            # so no unit ends up with an empty shift by chance.
            size = cfg["staffing"][dept][role]
            shift = names[int(np.searchsorted(np.cumsum(probs), (k + 0.5) / size))]
        rows.append((int(pidx), role, dept, shift))
    staff = pd.DataFrame(rows, columns=["pidx", "role", "dept", "usual_shift"])
    # Shuffle before numbering so a user ID says nothing about the department.
    staff = staff.iloc[rng.permutation(len(staff))].reset_index(drop=True)
    staff["user_id"] = [f"U{i + 1:04d}" for i in range(len(staff))]
    return staff


def build_devices(staff: pd.DataFrame, cfg, rng) -> tuple[pd.DataFrame, list, list, dict]:
    """Devices, each user's usual devices with weights, and workstations by department."""
    rows = []  # (dept, kind)
    by_dept: dict[str, list[int]] = {}
    for dept in cfg["departments"]:
        k = max(3, round(cfg["devices_per_staff"] * int((staff["dept"] == dept).sum())))
        by_dept[dept] = list(range(len(rows), len(rows) + k))
        rows += [(dept, "shared workstation" if j % 4 == 3 else "workstation") for j in range(k)]
    weights = np.array(cfg["usual_device_weights"], dtype=float)
    usual, usual_cum = [], []
    for role, dept in zip(staff["role"], staff["dept"]):
        mine = [int(d) for d in rng.choice(by_dept[dept], min(3, len(by_dept[dept])), replace=False)]
        w = weights[: len(mine)].copy()
        if role == "physician":
            rows.append((dept, "mobile"))
            mine, w = [mine[0], len(rows) - 1] + mine[1:2], np.array([0.6, 0.3, 0.1])[: len(mine[:1]) + 1 + len(mine[1:2])]
        elif cfg["departments"][dept]["kind"] == "back_office" and rng.random() < cfg["remote_share"]:
            rows.append((dept, "remote"))
            mine, w = [len(rows) - 1, mine[0]], np.array([0.7, 0.3])
        usual.append(np.array(mine))
        usual_cum.append(np.cumsum(w / w.sum()))
    devices = pd.DataFrame(rows, columns=["dept", "kind"])
    # Number devices in a shuffled order for the same reason as users.
    number = rng.permutation(len(devices))
    devices["device_id"] = [f"D{k + 1:04d}" for k in number]
    return devices, usual, usual_cum, {d: np.array(v) for d, v in by_dept.items()}


def build_shifts(staff: pd.DataFrame, cfg, rng) -> pd.DataFrame:
    """One row per worked shift, in seconds from the window start.

    `work_dept` is where the person actually worked. It differs from `dept`
    when a nurse covers another unit, and `float_documented` says whether
    the schedule shows that.
    """
    days = cfg["window_days"]
    hours = cfg["shifts"]
    weekday0 = pd.Timestamp(cfg["window_start"]).dayofweek
    float_targets = [d for d, v in cfg["departments"].items() if v["kind"] in ("inpatient", "emergency")]
    rows = []
    for u, (role, dept, shift) in enumerate(zip(staff["role"], staff["dept"], staff["usual_shift"])):
        start_h, end_h = hours[shift]
        if shift == "office":
            worked = [d for d in range(days) if (weekday0 + d) % 7 < 5]
        else:
            worked = []
            for week in range(0, days, 7):
                block = np.arange(week, min(week + 7, days))
                off = rng.choice(block, min(7 - cfg["shifts_per_week"], len(block)), replace=False)
                worked += [int(d) for d in block if d not in off]
        if shift == "night" and rng.random() < cfg["shifts_per_week"] / 7:
            worked = [-1] + worked  # the night that runs into the first morning
        can_float = role == "nurse" and cfg["departments"][dept]["kind"] == "inpatient"
        for d in worked:
            work_dept, floated, documented = dept, False, False
            if can_float and rng.random() < cfg["float_prob"]:
                work_dept = str(rng.choice([t for t in float_targets if t != dept]))
                floated, documented = True, bool(rng.random() < cfg["float_documented_prob"])
            rows.append((u, d, d * DAY + start_h * HOUR, d * DAY + end_h * HOUR, dept, work_dept, floated, documented, role))
    cols = ["u", "day", "start", "end", "dept", "work_dept", "floated", "float_documented", "role"]
    return pd.DataFrame(rows, columns=cols)


def build_actions(cfg, rng) -> tuple[pd.DataFrame, dict[str, np.ndarray]]:
    """Action catalogue and, per role, the probability of each code."""
    n = cfg["action_code_count"]
    top_n, top_share = cfg["action_top_n"], cfg["action_top_share"]
    ranks = np.arange(1, n + 1, dtype=float)
    lo, hi = 0.0, 5.0
    for _ in range(60):  # bisect the power-law exponent
        s = (lo + hi) / 2
        w = ranks**-s
        lo, hi = (s, hi) if w[:top_n].sum() / w.sum() < top_share else (lo, s)
    weight = ranks**-s
    cats = list(cfg["action_categories"])
    shares = np.array(list(cfg["action_categories"].values()), dtype=float)
    category = rng.choice(cats, n, p=shares / shares.sum())
    category[: len(cats)] = cats  # every category has at least one code
    order = rng.permutation(n)  # the code number does not reveal popularity
    actions = pd.DataFrame(
        {
            "action_code": [f"A{k + 1:04d}" for k in order],
            "category": category,
            "description": [f"{c} action {k + 1}" for c, k in zip(category, order)],
            "weight": weight,
        }
    )
    # Rescale so the categories carry their configured shares before role tilts.
    base = actions["weight"].to_numpy().copy()
    for c, share in zip(cats, shares / shares.sum()):
        m = category == c
        base[m] *= share / base[m].sum()
    probs = {}
    roles = list(cfg["role_action_tilt"])
    for role in roles:
        tilt = cfg["role_action_tilt"][role]
        v = base * np.array([tilt.get(c, 1.0) for c in category])
        probs[role] = v / v.sum()
    view = base * np.isin(category, ["read", "query"])
    probs["_view"] = view / view.sum()
    create = base * (category == "create")
    probs["_create"] = create / create.sum()
    return actions, probs
