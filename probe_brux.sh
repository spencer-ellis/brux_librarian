#!/bin/bash
# probe_brux.sh - read-only reconnaissance of the brux HTCondor pool.
#
# Run this ON brux before the first real run:
#     ./probe_brux.sh 2>&1 | tee probe_output.txt
#
# It changes nothing. Its output tells you what to put in config.py.

echo "=================== HOST ==================="
hostname
cat /etc/redhat-release 2>/dev/null || uname -a

echo
echo "=================== CONDOR ==================="
condor_version 2>&1 | head -2
echo "-- binaries --"
for c in condor_q condor_history condor_status condor_submit; do
    printf '%-16s %s\n' "$c" "$(command -v $c || echo 'NOT FOUND')"
done

echo
echo "=================== PYTHON ==================="
python3 --version 2>&1
python3 -c "import pandas, matplotlib, pytz; print('pandas', pandas.__version__, '| matplotlib', matplotlib.__version__, '| pytz OK')" 2>&1

echo
echo "=================== POOL CAPACITY ==================="
condor_status -total 2>&1 | head -20
echo "-- per-machine detected resources --"
condor_status -json -attributes Machine,DetectedCpus,DetectedMemory,DetectedGpus 2>/dev/null \
  | python3 -c "
import json,sys
try: ads = json.load(sys.stdin)
except Exception as e: print('could not parse:', e); sys.exit()
m = {}
for a in ads:
    a = {k.lower(): v for k, v in a.items()}
    name = a.get('machine')
    if not name: continue
    cur = m.setdefault(name, [0,0,0])
    cur[0] = max(cur[0], a.get('detectedcpus') or 0)
    cur[1] = max(cur[1], a.get('detectedmemory') or 0)
    g = a.get('detectedgpus') or 0
    if isinstance(g, str): g = len([x for x in g.split(',') if x.strip()])
    cur[2] = max(cur[2], g)
print('%d machines' % len(m))
print('TOTAL CPUs   :', sum(v[0] for v in m.values()))
print('TOTAL MEM GB :', round(sum(v[1] for v in m.values())/1024, 1))
print('TOTAL GPUs   :', sum(v[2] for v in m.values()))
"

echo
echo "=================== QUEUE ==================="
condor_q -allusers -totals 2>&1 | tail -5
echo "-- does -global work? --"
condor_q -global -allusers -totals 2>&1 | tail -3

echo
echo "=================== ACCOUNTING GROUPS ==================="
condor_q -allusers -af AccountingGroup 2>/dev/null | sort -u | head -20

echo
echo "=================== HISTORY ==================="
SINCE=$(python3 -c "import time; print(int(time.time()) - 3600*24*30)")
echo "-- does 'condor_history -since <expr>' work on this version? --"
condor_history -limit 1 -since "EnteredCurrentStatus < $SINCE" -af ClusterId 2>&1 | head -3
echo "-- sample of attributes on a recent job --"
condor_history -limit 1 -json 2>/dev/null | python3 -c "
import json,sys
try: ads=json.load(sys.stdin)
except Exception as e: print('could not parse:', e); sys.exit()
if not ads: print('no history records'); sys.exit()
a = ads[0]
want = ['Owner','JobStatus','QDate','JobStartDate','JobCurrentStartDate','CompletionDate',
        'EnteredCurrentStatus','RemoteWallClockTime','RequestCpus','RequestMemory',
        'RequestGpus','GpusProvisioned','ExitCode','AccountingGroup']
low = {k.lower(): (k, v) for k, v in a.items()}
for w in want:
    k, v = low.get(w.lower(), (None, None))
    print('%-22s %s' % (w, ('MISSING' if k is None else repr(v))))
"
echo "-- jobs in the last 30 days --"
condor_history -limit 20000 -constraint "EnteredCurrentStatus >= $SINCE" -af Owner 2>/dev/null | sort | uniq -c | sort -rn | head -20

echo
echo "=================== NETWORK (for gh-pages deploy) ==================="
git --version 2>&1
ssh -o BatchMode=yes -o ConnectTimeout=8 -T git@github.com 2>&1 | head -2

echo
echo "=================== DONE ==================="
