"""Turn chart requests into timed log events."""
from __future__ import annotations

import numpy as np
import pandas as pd


def expand(req: pd.DataFrame, staff: pd.DataFrame, usual: list, usual_cum: list, by_dept: dict,
           action_probs: dict, cfg, rng) -> pd.DataFrame:
    """One row per event. Label columns ride along and are split off on write."""
    W = cfg["window_days"] * 86400
    visit = req.loc[np.repeat(req.index.to_numpy(), req["n"].to_numpy())].reset_index(drop=True)
    V = len(visit)
    actor = visit["actor"].to_numpy()
    mode = visit["mode"].to_numpy()
    want = visit["t0"].to_numpy() + rng.random(V) * (visit["t1"].to_numpy() - visit["t0"].to_numpy())

    # Events per visit. A visit continues with a fixed probability, which
    # gives the share of events that immediately repeat the same patient.
    n_events = rng.geometric(1 - cfg["visit_continue_prob"], V)
    cap = visit["max_events"].to_numpy()
    limited = cap > 0
    n_events[limited] = rng.integers(1, cap[limited] + 1)
    vid = np.repeat(np.arange(V), n_events)
    N = len(vid)

    # Durations: lognormal through the published median and 95th percentile.
    mu = np.log(cfg["duration_median_s"])
    sigma = (np.log(cfg["duration_p95_s"]) - mu) / 1.6449
    duration = np.exp(mu + sigma * rng.standard_normal(N))
    duration[mode[vid] == "consult"] *= cfg["consult_duration_factor"]
    limit = np.where(mode[vid] == "view", cfg["misuse_duration_cap_s"], cfg["duration_cap_s"])
    duration = np.maximum(1, np.round(np.minimum(duration, limit))).astype(np.int64)

    # A person works one chart at a time. Visits queue up behind each other,
    # per person, not per account, so a borrowed account can overlap its owner.
    total = np.bincount(vid, weights=duration, minlength=V)
    start = np.empty(V)
    free, previous = -np.inf, -1
    backlog = cfg["max_backlog_s"]
    for i in np.lexsort((want, actor)):
        if actor[i] != previous:
            free, previous = -np.inf, actor[i]
        start[i] = want[i] if free - want[i] > backlog else max(want[i], free)
        free = start[i] + total[i]
    before = np.cumsum(duration) - duration
    first = np.cumsum(n_events) - n_events
    ts = np.floor(start[vid] + before - before[first][vid]).astype(np.int64)

    # Device: one of the person's usual devices, or a workstation of the unit
    # they are covering.
    device = np.empty(V, dtype=np.int64)
    r = rng.random(V)
    for a in np.unique(actor):
        idx = np.flatnonzero(actor == a)
        k = np.minimum(np.searchsorted(usual_cum[a], r[idx]), len(usual[a]) - 1)
        device[idx] = usual[a][k]
    home = staff["dept"].to_numpy()
    away = visit["device_dept"].to_numpy()
    for i in np.flatnonzero((away != "") & (away != home[actor])):
        device[i] = rng.choice(by_dept[away[i]])

    # Action: drawn from the acting person's role mix. Snooping only looks.
    role = staff["role"].to_numpy()[actor][vid]
    ev_mode = mode[vid]
    action = np.empty(N, dtype=np.int64)
    codes = len(action_probs["_view"])
    for name in np.unique(role):
        m = np.flatnonzero((role == name) & (ev_mode != "view"))
        action[m] = rng.choice(codes, len(m), p=action_probs[name])
    m = np.flatnonzero(ev_mode == "view")
    action[m] = rng.choice(codes, len(m), p=action_probs["_view"])
    last = (first + n_events - 1)[mode == "consult"]  # a consult ends with a note
    action[last] = rng.choice(codes, len(last), p=action_probs["_create"])

    events = pd.DataFrame(
        {
            "ts": ts, "u": visit["account"].to_numpy()[vid], "actor": actor[vid], "p": visit["p"].to_numpy()[vid],
            "device": device[vid], "action": action, "duration_s": duration,
            "reason": visit["reason"].to_numpy()[vid], "documented": visit["documented"].to_numpy()[vid],
            "scenario": visit["scenario"].to_numpy()[vid],
        }
    )
    events = events[(events["ts"] >= 0) & (events["ts"] < W)]
    # Sort by time with random tie-breaks, then number. An event ID therefore
    # says nothing about how the event was made.
    order = np.lexsort((rng.random(len(events)), events["ts"].to_numpy()))
    events = events.iloc[order].reset_index(drop=True)
    events.insert(0, "event_id", np.arange(1, len(events) + 1))
    return events
