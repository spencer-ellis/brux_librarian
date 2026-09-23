#!/bin/env python3
"""Bin HTCondor usage per user and emit plots + JSON for the dashboard.

Two views are produced from the same job table:

  averaged  - fixed TIME_RES bins over the full TIME_WINDOW. Each bin holds the
              time-weighted average resource each user occupied during it.
  snapshot  - exact usage sampled at every job start/end event over the last
              SNAPSHOT_WINDOW. Higher fidelity, shorter reach.
"""

import datetime
import json
import math
import sys

import matplotlib
matplotlib.use("Agg")           # no display on a submit node
import matplotlib.pyplot as plt
import pandas as pd
import pytz

import condor_source
import config

POOL = config.POOL_NAME

#----------------------------------------
# Time window
#----------------------------------------
tz = pytz.timezone(config.TIMEZONE)
current_time = datetime.datetime.now(tz)

# Round up to the next 10-minute mark so successive runs land on a stable grid.
nearest_10_minutes = math.ceil(current_time.minute / 10) * 10
if nearest_10_minutes == 60:
    nearest_10_minutes = 59
rounded_time = current_time.replace(minute=nearest_10_minutes, second=0, microsecond=0)

to_time = int(rounded_time.timestamp())
from_time = to_time - config.TIME_WINDOW
nbins = int(config.TIME_WINDOW / config.TIME_RES)

# Bins are labeled by their END, not their start: bin i covers
# [from_time + TIME_RES*i, from_time + TIME_RES*(i+1)) and carries the later
# timestamp. Start-labeling is the usual convention for binned series, but it
# makes the hover readout on a live dashboard look a full bin stale -- the
# newest point covers the present yet reads an hour ago. Shifting by TIME_RES
# (not a hardcoded hour) keeps this correct if the bin size changes.
#
# Only the labels move. The binning loop below derives its own bt/et from
# from_time, so which jobs land in which bin is unaffected.
time_idxs = [from_time + config.TIME_RES * (x + 1) for x in range(nbins)]

print("window: %s -> %s (%d bins of %ds)" % (
    datetime.datetime.fromtimestamp(from_time, tz).strftime("%Y-%m-%d %H:%M"),
    datetime.datetime.fromtimestamp(to_time, tz).strftime("%Y-%m-%d %H:%M"),
    nbins, config.TIME_RES))

#----------------------------------------
# Job data
#----------------------------------------
jobs = condor_source.load_jobs(from_time, now=to_time)
thresholds = condor_source.resolve_thresholds()

users = jobs["User"].value_counts().index.tolist() if len(jobs) else []
print("%d jobs, %d users" % (len(jobs), len(users)))

observables = config.OBSERVABLES

#----------------------------------------
# Averaged bins
#----------------------------------------
d = {user: {obs: [0] * nbins for obs in observables} for user in users}

for i in range(nbins):
    bt = from_time + config.TIME_RES * i
    et = bt + config.TIME_RES

    # Jobs overlapping this bin
    df_timebin = jobs.loc[(jobs.Start < et) & (jobs.End > bt)].copy()
    if df_timebin.empty:
        continue

    # How much of this bin each job actually occupied
    df_timebin["WithinTimeBinEnd"] = df_timebin["End"].clip(upper=et)
    df_timebin["WithinTimeBinStart"] = df_timebin["Start"].clip(lower=bt)
    df_timebin["WithinTimeBinElapsed"] = (
        df_timebin["WithinTimeBinEnd"] - df_timebin["WithinTimeBinStart"]
    )

    weight = df_timebin["WithinTimeBinElapsed"] / config.TIME_RES
    for obs in observables:
        weighted = df_timebin[obs] * weight
        for user, value in weighted.groupby(df_timebin["User"]).sum().items():
            d[user][obs][i] = value

#----------------------------------------
# Event-driven snapshots (last SNAPSHOT_WINDOW)
#----------------------------------------
snapshot_from_time = to_time - config.SNAPSHOT_WINDOW

events = set()
if len(jobs):
    events.update(jobs["Start"].tolist())
    events.update(jobs["End"].tolist())

# Sample strictly before to_time. Running jobs have End clamped to to_time, so a
# sample taken exactly at to_time sees them as already finished and the chart
# would always dip to zero at its right edge. Instead the final sample sits just
# inside the window, where currently-running jobs still count.
snapshot_time_idxs = sorted(t for t in events if snapshot_from_time <= t < to_time)
if snapshot_time_idxs and snapshot_time_idxs[-1] != to_time - 1:
    snapshot_time_idxs.append(to_time - 1)

d_snapshot = {
    user: {obs: [0] * len(snapshot_time_idxs) for obs in observables}
    for user in users
}

for i, t in enumerate(snapshot_time_idxs):
    df_snapshot = jobs.loc[(jobs.Start <= t) & (jobs.End > t)]
    if df_snapshot.empty:
        continue
    for obs in observables:
        for user, value in df_snapshot.groupby("User")[obs].sum().items():
            if user in d_snapshot:
                d_snapshot[user][obs][i] = value

print("snapshot: %d event samples" % len(snapshot_time_idxs))

#----------------------------------------
# Static plots
#----------------------------------------
for obs in observables:
    plot_df = pd.DataFrame({user: d[user][obs] for user in users}, index=time_idxs)
    # pd.to_datetime(unit="s") yields UTC-naive timestamps, which would label the
    # x-axis 4 hours off in EDT. Localize to the configured zone so the static
    # plots agree with the dashboard, whose JSON carries explicit offsets.
    plot_df.index = pd.to_datetime(plot_df.index, unit="s", utc=True
                                   ).tz_convert(config.TIMEZONE)

    max_value = plot_df.max().max() if len(users) else 0
    threshold_value = thresholds.get(obs, 0)

    plt.figure(figsize=(10, 6))
    if len(users):
        plt.stackplot(plot_df.index, [plot_df[c] for c in users], labels=users)

    nice = config.NICE_NAMES[obs]
    # Autoscale to the data so the shape stays readable. On a pool whose capacity
    # far exceeds its usage the threshold line would sit off the top of the axes;
    # rather than squash the plot (or leave a legend entry for an invisible line)
    # the capacity is reported in the title alone.
    ymax = 1.7 * max_value if max_value else 1
    if threshold_value:
        pct = (100.0 * max_value / threshold_value) if threshold_value else 0
        plt.title("%s Usage Over Time (capacity: %s, peak: %.0f%%)"
                  % (nice, threshold_value, pct))
        if threshold_value <= ymax:
            plt.axhline(y=threshold_value, color="red", linestyle="--",
                        label="Capacity (%s)" % threshold_value)
    else:
        plt.title("%s Usage Over Time" % nice)

    plt.xlabel("Time")
    plt.ylabel("%s Usage" % nice)
    plt.grid(True)
    plt.legend(loc="upper left")
    plt.xticks(rotation=45)
    plt.tight_layout()
    plt.ylim(0, ymax)
    plt.yscale("linear")
    plt.savefig("%s_%s.png" % (obs, POOL))
    plt.savefig("%s_%s.pdf" % (obs, POOL))
    plt.close()

#----------------------------------------
# JSON for the dashboard
#----------------------------------------
def iso(ts):
    return datetime.datetime.fromtimestamp(ts, tz=tz).strftime("%Y-%m-%dT%H:%M:%S%z")


json_data = {
    "metadata": {
        "pool": POOL,
        "last_updated": datetime.datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S%z"),
        "time_resolution_seconds": config.TIME_RES,
        "njobs": int(len(jobs)),
    },
    "time_idxs": [iso(t) for t in time_idxs],
    "time_idxs_snapshot": [iso(t) for t in snapshot_time_idxs],
    "users": users,
    "observables": {
        obs: {user: [round(float(v), 2) for v in d[user][obs]] for user in users}
        for obs in observables
    },
    "observables_snapshot": {
        obs: {user: [round(float(v), 2) for v in d_snapshot[user][obs]] for user in users}
        for obs in observables
    },
    "thresholds": {obs: thresholds.get(obs, 0) for obs in observables},
    "nice_names": config.NICE_NAMES,
}

outfile = "data_%s.json" % POOL
with open(outfile, "w") as f:
    json.dump(json_data, f)

print("wrote %s" % outfile)
