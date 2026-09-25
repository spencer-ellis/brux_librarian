# BRUX Librarian

Automated HTCondor resource monitoring for the **brux** cluster (`brux.hep.brown.edu`).
Generates an interactive dashboard showing CPU, GPU, memory, and job usage per user over time.

A port of [hpg_librarian](../hpg_librarian) from Slurm/HiPerGator to HTCondor/brux.

## What It Does

- Queries `condor_history` + `condor_q` every 10 minutes for usage across all users in the pool
- Bins usage into 1-hour intervals over a 30-day rolling window
- Generates static PNG/PDF plots and a JSON data file
- Deploys an interactive Plotly.js dashboard to GitHub Pages
- Runs itself on a schedule as a self-rescheduling HTCondor job

## How this differs from hpg_librarian

| | hpg_librarian (Slurm) | brux_librarian (HTCondor) |
|---|---|---|
| Job accounting | `sacct` (one command, all jobs) | `condor_history` **+** `condor_q`, merged |
| Grouping | tabs per QoS (`avery`, `avery-b`) | single pool, no tabs |
| Scheduling | `scrontab` | self-rescheduling `local` universe job (`librarian.sub`) |
| Stuck-job recovery | `watchdog.sh` | not needed — the schedd re-runs the job itself |
| Capacity thresholds | hardcoded per QoS | auto-detected from `condor_status` |
| Config | edited inline in `parse.py` | all in `config.py` |

The reason for the two-command query: HTCondor moves a job into `condor_history`
only *after* it leaves the queue, so a job running right now appears **only** in
`condor_q`. Slurm's `sacct` reports both in one shot. `condor_source.py` merges
them and de-duplicates on `ClusterId.ProcId`, with the live queue record winning.

### Why `-af` and not `-json`

Job attributes are read with `condor_history -af` (autoformat), not `-json`.
`-json` dumps the raw ClassAd, so an attribute stored as an *expression* arrives
unevaluated. On brux `RequestMemory` is exactly that:

```
ifThenElse(NumJobStarts == 0, 4096, ifThenElse(NumJobStarts == 1, 8192, 16384))
```

the standard "retry the job with more memory" idiom. Under `-json` that string
is unparseable as a number and every memory reading would silently be **zero**;
`MemoryProvisioned` is not populated on this pool either. `-af` evaluates each
attribute against its own job ad, yielding the 4096/8192/16384 the job actually
requested. Attributes the pool does not populate (`RequestGpus`,
`AccountingGroup`) come back as `undefined` and are treated as missing rather
than as zero, so the fallback chains still work.

## Prerequisites

- A brux account that can run `condor_q`, `condor_history`, `condor_status`, `condor_submit`
- Python 3 (a venv is created in step 2 -- the system python has no pandas)
- `git` on the submit node (2.52.0 present) plus a passphrase-less deploy key

## Setup

Cluster facts below were confirmed by `probe_brux.sh` on 2026-09-23:
HTCondor **23.0.28** on RHEL 9.8, submit node `pbrux40cit.hep.brown.edu`,
**8 machines / 17 slots / 384 CPUs / ~3012 GiB / 0 GPUs**, a single schedd, and
`AccountingGroup` undefined pool-wide.

### 1. Copy it over

```bash
scp -r brux_librarian brux:~/
ssh brux && cd ~/brux_librarian
```

### 2. Create the Python environment

brux's `python3` is 3.10.12 with **no pandas/matplotlib**, and the schedd runs
the scheduled job with a bare environment where conda is not activated. A
self-contained venv sidesteps both problems:

```bash
python3 -m venv ~/brux_librarian/venv
~/brux_librarian/venv/bin/pip install pandas matplotlib pytz
```

`run.sh` picks up `./venv/bin/python` automatically and refuses to run with a
clear message if the modules are missing, so a broken environment can never
quietly publish an empty dashboard.

### 3. Test manually

```bash
cd ~/brux_librarian
./run.sh                 # SKIP_DEPLOY=1 ./run.sh to stop before publishing
```

You should get `data_brux.json` plus `NCPUS_brux.png`, `MEMORY_brux.png`,
`NJOBS_brux.png` (and PDFs). The GPU chart is hidden automatically because brux
reports no GPUs.

### 4. Set up the GitHub deploy key

The probe found no usable key on brux (`git@github.com: Permission denied
(publickey)`). The scheduled job runs unattended and cannot type a passphrase,
so generate a **passphrase-less** deploy key:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_brux_librarian -N "" \
    -C "brux_librarian deploy"
cat ~/.ssh/id_ed25519_brux_librarian.pub
```

Add that public key to the repo under **Settings > Deploy keys**, with
**Allow write access** checked. Then tell SSH to use it for GitHub:

```bash
cat >> ~/.ssh/config <<'CFG'
Host github.com
    HostName github.com
    User git
    IdentityFile ~/.ssh/id_ed25519_brux_librarian
    IdentitiesOnly yes
CFG
chmod 600 ~/.ssh/config
ssh -T git@github.com     # expect: "Hi <user>/brux_librarian! You've successfully authenticated"
```

A deploy key is scoped to this one repo, which is what you want for an
unattended job — unlike an account-wide key.

### 5. Set up the repo and GitHub Pages

```bash
cd ~/brux_librarian
git init && git add . && git commit -m "Initial commit"
git remote add origin git@github.com:YOUR_USERNAME/brux_librarian.git
git push -u origin main

# create the empty gh-pages branch the deploy script pushes into
git checkout --orphan gh-pages && git rm -rf . >/dev/null 2>&1
echo init > .gitkeep && git add .gitkeep && git commit -m "Initialize gh-pages"
git push origin gh-pages && git checkout main
```

Then **Settings > Pages**: source *Deploy from a branch*, branch **gh-pages** /
**(root)**. Confirm publishing works:

```bash
./deploy_pages.sh
```

The dashboard lands at `https://YOUR_USERNAME.github.io/brux_librarian/`.

### 6. Schedule it

HTCondor has no `scrontab`. `librarian.sub` submits a job that stays in the queue
and re-runs every 10 minutes:

```bash
cd ~/brux_librarian && mkdir -p logs
condor_submit librarian.sub
```

Verify and manage it:

```bash
condor_q                  # the job sits Idle between ticks - that is correct
tail -f logs/librarian.out
condor_rm <clusterid>     # stop the schedule
```

The mechanism is `cron_minute`/`cron_hour`/... plus `on_exit_remove = false`,
which keeps the job in the queue instead of leaving after one run.
`universe = local` runs it on the submit node, where the condor tools, your
filesystem and your SSH keys all live.

## Files

| File | Purpose |
|------|---------|
| `config.py` | All cluster-specific settings. Edit this, not the others. |
| `condor_source.py` | Queries `condor_history` + `condor_q`, normalizes job ads |
| `parse.py` | Bins usage, writes plots + `data_brux.json` |
| `run.sh` | One cycle: parse, validate JSON, deploy |
| `deploy_pages.sh` | Pushes dashboard + JSON to the `gh-pages` branch |
| `librarian.sub` | Self-rescheduling HTCondor job (the `scrontab` replacement) |
| `probe_brux.sh` | Read-only cluster diagnostics; safe to re-run anytime |
| `dashboard/index.html` | Self-contained interactive Plotly.js dashboard |

## Dashboard Features

- Stacked area charts: NCPUS, NGPUS, Memory, Jobs in a 2x2 grid
- **Averaged** mode (1-hour bins, full 30 days) and **Snapshot** mode (exact usage
  sampled at every job start/end, last 3 days)
- Hover for per-user values; click legend entries to toggle users
- Time range presets (6h–30d) and a custom date picker
- Current-usage and total-usage (CPU-hours) tables
- Dark mode, persisted in the browser
- Auto-refreshes every 5 minutes

## Tuning

| Parameter | Location | Default | Description |
|-----------|----------|---------|-------------|
| `TIME_RES` | `config.py` | 3600 | Bin size in seconds |
| `TIME_WINDOW` | `config.py` | 30 days | Rolling window |
| `SNAPSHOT_WINDOW` | `config.py` | 3 days | Event-sampled window |
| `HISTORY_LIMIT` | `config.py` | 200000 | Max history records per run |
| `EXTRA_CONSTRAINT` | `config.py` | `""` | ClassAd expression to restrict the pool |
| `REFRESH_MS` | `dashboard/index.html` | 5 min | Dashboard auto-refresh |

Smaller `TIME_RES` = more detail, spikier charts, larger JSON.
Larger `TIME_WINDOW` = more history, slower `condor_history` scans.

## Troubleshooting

**`parse.py` reports 0 jobs.**
Check `condor_history -limit 5 -af Owner JobStartDate CompletionDate` returns rows.
If `EXTRA_CONSTRAINT` is set, try clearing it. Also confirm `EXCLUDE_USERS` isn't
filtering everyone.

**History looks truncated (usage drops to zero further back).**
Your condor may not support `condor_history -since`. Set `USE_HISTORY_SINCE = False`
in `config.py`. Also raise `HISTORY_LIMIT` if the pool is busy.

**`condor_history` takes minutes to run.**
It's scanning the whole history file. Keep `USE_HISTORY_SINCE = True` if supported,
and consider shortening `TIME_WINDOW`.

**The scheduled job runs but nothing deploys.**
`getenv = true` in `librarian.sub` inherits the environment from whoever ran
`condor_submit`. If your SSH key needs an agent, the job can't reach it — use a
passphrase-less deploy key. Check `logs/librarian.err`.

**The job goes to Held instead of re-running.**
Look at `condor_q -held -af HoldReason`. A non-zero exit from `run.sh` shouldn't
hold it (`on_exit_hold = false`), so this usually means the executable isn't
readable or `LIBRARIAN_DIR` in `librarian.sub` is wrong.

**Memory shows as zero for every user.**
This is the `-json` vs `-af` trap described above. Confirm evaluation works:

```bash
condor_history -limit 5 -af:t Owner RequestMemory
```

If that prints `ifThenElse(...)` rather than a number, your condor is not
evaluating the expression; fall back by adding `MemoryUsage` earlier in the
`_num(...)` chain in `condor_source.py`.

**The GPU chart is missing.**
Expected on brux: the pool reports 0 GPUs and no job requests any, so the panel
hides itself. It reappears automatically if GPU nodes are added. If brux does
get GPUs but counts stay zero, check what `RequestGpus`/`GpusProvisioned` print
and add the pool's attribute name to the `_gpu_count(...)` call.

**The queue shows thousands of held jobs.**
Normal on brux (3443 of 3452 at probe time). Held jobs consume nothing and are
excluded from usage; only jobs that actually ran contribute, bounded by their
`RemoteWallClockTime` rather than extended to the present.

**Re-running diagnostics.**
`./probe_brux.sh` is read-only and safe to re-run at any time; it reports condor
version, attribute availability, capacity and GitHub reachability.
