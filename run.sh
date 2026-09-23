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

# Nothing here runs on a terminal, so anything that tries to *ask* a question
# would block forever and wedge the schedule. Make git and ssh fail fast
# instead: no credential prompt, no passphrase prompt, no host-key prompt.
export GIT_TERMINAL_PROMPT=0
export GIT_ASKPASS=/bin/true
export SSH_ASKPASS=/bin/true
export GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"

# Belt and braces: cap each stage so a hang can never outlive one tick.
if command -v timeout >/dev/null 2>&1; then
    RUN_PARSE="timeout 900"
    RUN_DEPLOY="timeout 180"
else
    RUN_PARSE=""
    RUN_DEPLOY=""
fi

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

${RUN_PARSE} "${PYTHON}" parse.py || {
    rc=$?
    [ $rc -eq 124 ] && echo "parse.py TIMED OUT after 900s" || echo "parse.py FAILED (exit $rc)"
    exit 1
}

# Sanity-check the JSON before publishing it, so a bad run can't blank the site.
"${PYTHON}" -c "import json; json.load(open('data_${POOL}.json'))" \
    || { echo "generated JSON is invalid, not deploying"; exit 1; }
echo "JSON OK"

# Publish to GitHub Pages.
if [ "${SKIP_DEPLOY:-0}" = "1" ]; then
    echo "SKIP_DEPLOY set, not deploying"
else
    ${RUN_DEPLOY} ./deploy_pages.sh || {
        rc=$?
        [ $rc -eq 124 ] && echo "deploy_pages.sh TIMED OUT after 180s (hang averted)" \
                        || echo "deploy_pages.sh failed with exit $rc (data still generated locally)"
    }
fi

echo "=== librarian run finished at $(date) ==="
