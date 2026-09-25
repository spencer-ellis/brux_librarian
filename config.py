"""Configuration for the BRUX librarian.

Everything cluster-specific lives here. Edit this file, not the others.
"""

#----------------------------------------
# Identity
#----------------------------------------
# NOTE: if you change POOL_NAME, also update DATA_FILE and POOL_LABEL near the
# top of the <script> block in dashboard/index.html - the dashboard is static
# HTML and can't read this file.
POOL_NAME = "brux"          # used for output filenames: data_brux.json, NCPUS_brux.png
DASHBOARD_TITLE = "BRUX Usage Dashboard"
TIMEZONE = "US/Eastern"

#----------------------------------------
# Binning
#----------------------------------------
TIME_RES = 3600                    # averaged-mode bin size, seconds (1 hour)
TIME_WINDOW = 3600 * 24 * 30       # rolling window, seconds (30 days)
SNAPSHOT_WINDOW = 3600 * 24 * 3    # event-driven snapshot depth, seconds (3 days)

#----------------------------------------
# HTCondor access
#
# Confirmed against brux on 2026-09-23 (probe_brux.sh):
#   HTCondor 23.0.28, RHEL 9.8, submit node pbrux40cit.hep.brown.edu
#   condor_* all in /usr/bin, single schedd, `-since` supported
#   8 machines / 17 slots / 384 CPUs / ~3012 GiB / 0 GPUs
#   AccountingGroup is undefined pool-wide (hence: one pool, no tabs)
#----------------------------------------
# Leave empty to find condor_* on PATH. Set to e.g. "/usr/bin" if cron has a bare PATH.
CONDOR_BIN_DIR = ""

# Query every schedd in the pool (condor_q -global) rather than just the local one.
# brux has a single schedd, where -global returns exactly the same 3452 jobs, so
# this stays off. Set True only if a second submit node is added.
CONDOR_GLOBAL_QUERY = False

# Use `condor_history -since` to stop the backwards scan once it leaves the window.
# Verified working on brux's 23.0.28. Disable if history counts look truncated.
USE_HISTORY_SINCE = True

# Hard cap on history records pulled per run. Raise if the pool is busier than this.
HISTORY_LIMIT = 200000

# Restrict to a subset of the pool, as a ClassAd expression. Empty = whole pool.
# e.g. 'AccountingGroup =?= "group_hep"'  or  'Owner != "condor"'
EXTRA_CONSTRAINT = ""

#----------------------------------------
# Capacity thresholds (the red dashed line on each chart)
#----------------------------------------
# None  -> auto-detect from `condor_status` (total pool capacity)
# 0     -> no threshold line
# a number -> use it verbatim
# brux auto-detects as 384 CPUs / 3012 GiB / 0 GPUs. Because it reports no GPUs
# and no job requests them, the dashboard hides the GPU chart automatically --
# leave NGPUS here so it reappears by itself if GPU nodes are ever added.
THRESHOLDS = {
    "NCPUS":  None,
    "NGPUS":  None,
    "MEMORY": None,   # GiB
    "NJOBS":  0,
}

#----------------------------------------
# Observables
#----------------------------------------
OBSERVABLES = ["NCPUS", "NGPUS", "MEMORY", "NJOBS"]

NICE_NAMES = {
    "NCPUS":  "# of CPUs",
    "NGPUS":  "# of GPUs",
    "MEMORY": "Memory (GiB)",
    "NJOBS":  "# of Jobs",
}

#----------------------------------------
# Users to hide from the dashboard (system/service accounts)
#
# Real brux users seen in the last 30 days: gbarone1, kho29, smondal5, jofferma.
# Note that brux's queue is dominated by held jobs (3443 of 3452 at probe time);
# those consume nothing and are excluded from usage automatically.
#----------------------------------------
EXCLUDE_USERS = ["condor", "root", "nobody"]
