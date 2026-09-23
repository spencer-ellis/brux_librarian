"""Pull job records out of HTCondor and normalize them for the binner.

HTCondor splits what Slurm's `sacct` gives you in one shot across two commands:

    condor_history  -> jobs that have left the queue (completed/removed)
    condor_q        -> jobs still in the queue (idle/running/held)

A job that is running right now only exists in `condor_q`, so both are queried
and merged. The result is a DataFrame with the same shape the binner expects:

    User | Submit | Start | End | NCPUS | NGPUS | MEMORY | NJOBS | State | JobID

Times are integer unix timestamps. End is clamped to "now" for running jobs.
"""

import json
import os
import shutil
import subprocess
import time

import pandas as pd

import config


class CondorError(RuntimeError):
    """A condor_* command failed or returned something unparseable."""


# Attributes we ask for, in the order they come back from -autoformat.
#
# These are fetched with `-af` (autoformat) rather than `-json` on purpose.
# `-json` dumps the raw ad, so an attribute stored as a ClassAd *expression*
# arrives unevaluated -- brux stores RequestMemory as
#     ifThenElse(NumJobStarts == 0, 4096, ifThenElse(NumJobStarts == 1, 8192, 16384))
# which is the standard "retry with more memory" idiom. `-af` evaluates each
# attribute in the context of its own job ad, so we get the number that job
# actually asked for. Undefined attributes come back as the literal "undefined".
JOB_FIELDS = [
    "Owner",
    "ClusterId",
    "ProcId",
    "JobStatus",
    "QDate",
    "JobStartDate",
    "JobCurrentStartDate",
    "CompletionDate",
    "EnteredCurrentStatus",
    "RemoteWallClockTime",
    "RequestCpus",
    "CpusProvisioned",
    "RequestMemory",
    "MemoryProvisioned",
    "MemoryUsage",
    "RequestGpus",
    "GpusProvisioned",
    "ExitCode",
]

# Values -af prints for an attribute it could not resolve.
UNDEFINED_VALUES = {"undefined", "error", "", "(null)"}

MACHINE_ATTRS = [
    "Machine",
    "SlotType",
    "DetectedCpus",
    "DetectedMemory",
    "DetectedGpus",
    "TotalCpus",
    "TotalMemory",
    "TotalGpus",
]

# HTCondor JobStatus -> the state codes the hpg dashboard already understands.
# 0 pending, 1 running, 2 completed, 3 timeout, 4 cancelled, 5 failed
CONDOR_STATUS_MAP = {
    1: 0,   # Idle
    2: 1,   # Running
    3: 4,   # Removed
    4: 2,   # Completed
    5: 5,   # Held
    6: 1,   # Transferring output
    7: 1,   # Suspended
}


#----------------------------------------
# Command plumbing
#----------------------------------------

def _condor_cmd(name):
    """Absolute path to a condor_* binary, honouring CONDOR_BIN_DIR."""
    if config.CONDOR_BIN_DIR:
        path = os.path.join(config.CONDOR_BIN_DIR, name)
        if os.path.exists(path):
            return path
    found = shutil.which(name)
    if found:
        return found
    # Last resort: the usual RPM location, so the error message names a real path.
    return os.path.join("/usr/bin", name)


def _run_text(cmd):
    """Run a condor command and return stdout, raising on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise CondorError(
            "command failed (exit %d): %s\n%s"
            % (proc.returncode, " ".join(cmd), proc.stderr.strip())
        )
    return proc.stdout


def _parse_autoformat(text, fields):
    """Turn tab-separated -af output into a list of lowercase-keyed dicts.

    Attributes that came back "undefined" are omitted entirely, so the _num /
    _gpu_count fallback chains treat them as missing rather than as zero.
    """
    ads = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        ad = {}
        for name, value in zip(fields, parts):
            value = value.strip()
            if value.lower() in UNDEFINED_VALUES:
                continue
            ad[name.lower()] = value
        if ad:
            ads.append(ad)
    return ads


def _run_json(cmd):
    """Run a condor command with -json and return the parsed list of ads."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise CondorError(
            "command failed (exit %d): %s\n%s"
            % (proc.returncode, " ".join(cmd), proc.stderr.strip())
        )
    out = proc.stdout.strip()
    if not out:
        return []
    try:
        ads = json.loads(out)
    except json.JSONDecodeError as exc:
        raise CondorError("could not parse JSON from: %s (%s)" % (" ".join(cmd), exc))
    return ads if isinstance(ads, list) else [ads]


#----------------------------------------
# ClassAd value coercion
#----------------------------------------

def _lower(ad):
    """Lowercase an ad's keys so attribute lookup is case-insensitive."""
    return {str(k).lower(): v for k, v in ad.items()}


def _num(ad, *names, default=0):
    """First numeric value among `names`, coercing strings and skipping expressions.

    Completed jobs usually store literals, but a job ad can still carry an
    unevaluated ClassAd expression (e.g. RequestMemory as an ifThenElse). Those
    come back as strings that don't parse, and fall through to the next name.
    """
    for name in names:
        val = ad.get(name.lower())
        if val is None:
            continue
        if isinstance(val, bool):
            continue
        if isinstance(val, (int, float)):
            return val
        if isinstance(val, str):
            try:
                return float(val)
            except ValueError:
                continue
    return default


def _gpu_count(ad, *names, default=0):
    """GPU count, tolerating the string-list form ("CUDA0,CUDA1")."""
    for name in names:
        val = ad.get(name.lower())
        if val is None:
            continue
        if isinstance(val, bool):
            continue
        if isinstance(val, (int, float)):
            return val
        if isinstance(val, str):
            try:
                return float(val)
            except ValueError:
                parts = [p for p in val.split(",") if p.strip()]
                if parts:
                    return len(parts)
    return default


#----------------------------------------
# Job queries
#----------------------------------------

def _af_args():
    """Autoformat flags: tab-separated, one line per job."""
    return ["-af:t"] + JOB_FIELDS


def fetch_history(from_ts):
    """Completed/removed jobs that overlap the window starting at `from_ts`."""
    cmd_base = [
        _condor_cmd("condor_history"),
        "-limit", str(config.HISTORY_LIMIT),
    ]

    clauses = [
        "(CompletionDate >= %d || JobStartDate >= %d || EnteredCurrentStatus >= %d)"
        % (from_ts, from_ts, from_ts)
    ]
    if config.EXTRA_CONSTRAINT:
        clauses.append("(%s)" % config.EXTRA_CONSTRAINT)
    constraint = " && ".join(clauses)

    # History is scanned newest-first, so `-since` lets condor stop as soon as it
    # walks off the back of our window instead of reading the whole history file.
    attempts = []
    if config.USE_HISTORY_SINCE:
        attempts.append(cmd_base + ["-since", "EnteredCurrentStatus < %d" % from_ts,
                                    "-constraint", constraint] + _af_args())
    attempts.append(cmd_base + ["-constraint", constraint] + _af_args())

    last_error = None
    for cmd in attempts:
        try:
            return _parse_autoformat(_run_text(cmd), JOB_FIELDS)
        except CondorError as exc:
            last_error = exc
            continue
    raise CondorError("condor_history failed: %s" % last_error)


def fetch_queue():
    """Jobs still in the queue: idle, running, held."""
    cmd = [
        _condor_cmd("condor_q"),
        "-allusers",
    ]
    if config.CONDOR_GLOBAL_QUERY:
        cmd.insert(1, "-global")
    if config.EXTRA_CONSTRAINT:
        cmd += ["-constraint", config.EXTRA_CONSTRAINT]
    cmd += _af_args()

    try:
        return _parse_autoformat(_run_text(cmd), JOB_FIELDS)
    except CondorError as exc:
        # An empty pool-wide queue makes `condor_q -global` exit non-zero on some
        # versions. That is not fatal; history alone is still useful.
        print("WARNING: condor_q failed, continuing with history only: %s" % exc)
        return []


#----------------------------------------
# Normalization
#----------------------------------------

def _normalize(ad, now):
    """Turn one raw job ad into a flat record, or None if it never ran."""
    ad = _lower(ad)

    owner = ad.get("owner")
    if not owner or not isinstance(owner, str):
        return None
    if owner in config.EXCLUDE_USERS:
        return None

    status = int(_num(ad, "JobStatus", default=1))

    start = int(_num(ad, "JobCurrentStartDate", "JobStartDate", default=0))
    if start <= 0:
        # Idle job that has never been matched: no resources consumed yet.
        return None

    completion = int(_num(ad, "CompletionDate", default=0))
    wallclock = int(_num(ad, "RemoteWallClockTime", default=0))
    entered = int(_num(ad, "EnteredCurrentStatus", default=0))

    if completion > 0:
        end = completion
    elif status in (2, 6, 7):
        end = now                      # still running: occupy up to the present
    else:
        # The job stopped running but has no CompletionDate (held, removed, or
        # evicted). Two independent upper bounds on when it actually stopped:
        #
        #   EnteredCurrentStatus     - when it entered Held/Removed
        #   Start + RemoteWallClockTime
        #
        # RemoteWallClockTime is CUMULATIVE over every execution, not just the
        # latest one, so on a pool that restarts jobs it overshoots badly (brux
        # restarts constantly - that is why RequestMemory branches on
        # NumJobStarts). EnteredCurrentStatus overshoots too when a job sat Idle
        # after eviction before being held. Neither bound is reliable alone, so
        # take whichever is tighter.
        bounds = []
        if entered > start:
            bounds.append(entered)
        if wallclock > 0:
            bounds.append(start + wallclock)
        if not bounds:
            return None
        end = min(bounds)

    end = min(end, now)
    if end <= start:
        return None

    exit_code = int(_num(ad, "ExitCode", default=0))
    state = CONDOR_STATUS_MAP.get(status, 5)
    if state == 2 and exit_code != 0:
        state = 5                      # completed but non-zero exit == failed

    memory_mb = _num(ad, "RequestMemory", "MemoryProvisioned", "MemoryUsage",
                     default=0)

    return {
        "User": owner,
        "Submit": int(_num(ad, "QDate", default=0)),
        "Start": start,
        "End": end,
        "NCPUS": _num(ad, "RequestCpus", "CpusProvisioned", default=1),
        "NGPUS": _gpu_count(ad, "RequestGpus", "GpusProvisioned", default=0),
        "MEMORY": memory_mb / 1024.0,  # MB -> GB
        "NJOBS": 1,
        "State": state,
        "JobID": "%d.%d" % (int(_num(ad, "ClusterId", default=0)),
                            int(_num(ad, "ProcId", default=0))),
    }


def load_jobs(from_ts, now=None):
    """Merged, normalized job table covering [from_ts, now]."""
    if now is None:
        now = int(time.time())

    history = fetch_history(from_ts)
    queue = fetch_queue()
    print("condor_history: %d ads, condor_q: %d ads" % (len(history), len(queue)))

    records = {}
    # Queue ads are written last so a job present in both wins with its live state.
    for ad in list(history) + list(queue):
        rec = _normalize(ad, now)
        if rec is None:
            continue
        records[rec["JobID"]] = rec

    if not records:
        return pd.DataFrame(
            columns=["User", "Submit", "Start", "End", "NCPUS", "NGPUS",
                     "MEMORY", "NJOBS", "State", "JobID"]
        )

    df = pd.DataFrame(list(records.values()))
    # Keep only jobs that actually overlap the window.
    df = df[df["End"] > from_ts]
    return df.reset_index(drop=True)


#----------------------------------------
# Pool capacity (threshold lines)
#----------------------------------------

def detect_capacity():
    """Total pool capacity as {"NCPUS": n, "NGPUS": n, "MEMORY": gb}.

    Sums per-machine detected resources across the pool. Partitionable slots
    report the same Detected* values on every slot of a machine, so ads are
    grouped by Machine and the max taken rather than summed.
    """
    cmd = [
        _condor_cmd("condor_status"),
        "-json",
        "-attributes", ",".join(MACHINE_ATTRS),
    ]
    try:
        ads = _run_json(cmd)
    except CondorError as exc:
        print("WARNING: could not auto-detect pool capacity: %s" % exc)
        return {"NCPUS": 0, "NGPUS": 0, "MEMORY": 0}

    machines = {}
    for ad in ads:
        ad = _lower(ad)
        machine = ad.get("machine")
        if not machine:
            continue
        cur = machines.setdefault(machine, {"NCPUS": 0, "NGPUS": 0, "MEMORY": 0})
        cur["NCPUS"] = max(cur["NCPUS"], _num(ad, "DetectedCpus", "TotalCpus", default=0))
        cur["NGPUS"] = max(cur["NGPUS"], _gpu_count(ad, "DetectedGpus", "TotalGpus", default=0))
        mem_mb = _num(ad, "DetectedMemory", "TotalMemory", default=0)
        cur["MEMORY"] = max(cur["MEMORY"], mem_mb / 1024.0)

    total = {"NCPUS": 0, "NGPUS": 0, "MEMORY": 0}
    for vals in machines.values():
        for key in total:
            total[key] += vals[key]

    print("detected pool capacity across %d machines: %s" % (len(machines), total))
    return {k: int(round(v)) for k, v in total.items()}


def resolve_thresholds():
    """config.THRESHOLDS with any None entries filled in from condor_status."""
    thresholds = dict(config.THRESHOLDS)
    if any(v is None for v in thresholds.values()):
        detected = detect_capacity()
        for key, val in thresholds.items():
            if val is None:
                thresholds[key] = detected.get(key, 0)
    return thresholds
