#!/bin/bash
# run.sh - one collection cycle: query condor, render, deploy.
# Invoked by librarian.sub every 10 minutes (HTCondor local-universe cron job).

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "${DIR}" || exit 1

LOGDIR="${DIR}/logs"
mkdir -p "${LOGDIR}"

# HTCondor hands the job a minimal environment. Make sure the condor tools,
# python and git are findable even when PATH is bare.
export PATH="/usr/bin:/usr/local/bin:/usr/sbin:${PATH}"

# brux's system python3 (3.10.12) has no pandas/matplotlib, and the schedd runs
# this job with a bare environment that won't have conda activated. So prefer a
# self-contained venv living next to this script; see README step 2.
if [ -z "${PYTHON}" ] && [ -x "${DIR}/venv/bin/python" ]; then
    PYTHON="${DIR}/venv/bin/python"
fi
PYTHON="${PYTHON:-python3}"

if ! "${PYTHON}" -c "import pandas, matplotlib, pytz" 2>/dev/null; then
    echo "ERROR: ${PYTHON} is missing pandas/matplotlib/pytz."
    echo "       Create the venv:  python3 -m venv ${DIR}/venv && ${DIR}/venv/bin/pip install pandas matplotlib pytz"
    exit 1
fi

# Single source of truth for the pool name: config.py.
POOL="$("${PYTHON}" -c 'import config; print(config.POOL_NAME)')" || {
    echo "could not read POOL_NAME from config.py"; exit 1; }
export POOL

echo "=== librarian run at $(date) (pool: ${POOL}) ==="

"${PYTHON}" parse.py || { echo "parse.py FAILED"; exit 1; }

# Sanity-check the JSON before publishing it, so a bad run can't blank the site.
"${PYTHON}" -c "import json; json.load(open('data_${POOL}.json'))" \
    || { echo "generated JSON is invalid, not deploying"; exit 1; }
echo "JSON OK"

# Publish to GitHub Pages.
if [ "${SKIP_DEPLOY:-0}" = "1" ]; then
    echo "SKIP_DEPLOY set, not deploying"
else
    ./deploy_pages.sh || echo "deploy_pages.sh failed (data still generated locally)"
fi

echo "=== librarian run finished at $(date) ==="
