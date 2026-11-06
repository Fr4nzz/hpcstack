"""Tests for skills/right-size-jobs/scripts/pendlog and pend-risk with fake LSF commands on PATH.

  uv run --with pytest python -m pytest tests/
"""
import csv
import io
import json
import os
import subprocess
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(REPO, "skills", "right-size-jobs", "scripts")
PENDLOG = os.path.join(SCRIPTS, "pendlog")
PENDRISK = os.path.join(SCRIPTS, "pend-risk")

# Fake LSF commands. They read $FAKE_STATE (a JSON file) so a test can change what they report.
FAKES = {
    "bqueues": r'''
import json, os, sys, time
st = json.load(open(os.environ["FAKE_STATE"]))
time.sleep(st.get("bqueues_sleep", 0))
if "-r" in sys.argv:
    print("QUEUE: gpu")
    print("SHARE_INFO_FOR: gpu/")
    print(" USER/GROUP   SHARES  PRIORITY  STARTED  RESERVED  CPU_TIME  RUN_TIME   ADJUST")
    print(f" {os.environ['USER']}         10       0.010      2        0         100.0     50         0.000")
    print(" other/         10       0.333      0        0         0.0       0          0.000")
else:
    print("QUEUE_NAME      PRIO STATUS          MAX JL/U JL/P JL/H NJOBS  PEND   RUN  SUSP")
    print(f"gpu              40  Open:Active       -    -    -    -  {st['pend']+st['run']}  {st['pend']}  {st['run']}     0")
''',
    "bhosts": r'''
import sys
if "-gpu" in sys.argv:
    print("HOST_NAME   ID  MODEL                 MUSED   MRSV  NJOBS  RUN  SUSP  RSV")
    print("big-01       0  NVIDIAA100_SXM4_80GB     0M     0M      0    0     0    0")
    print("             1  NVIDIAA100_SXM4_80GB    10G    50G      1    1     0    0")
    print("small-01     0  TeslaV100_SXM2_32GB      0M     0M      0    0     0    0")
    print("full-01      0  NVIDIAH10080GBHBM3       0M     0M      0    0     0    0")
else:
    print("HOST_NAME   STATUS     JL/U  MAX  NJOBS  RUN  SSUSP  USUSP  RSV")
    print("big-01      ok          -     64   10     10   0      0      0")
    print("small-01    ok          -     32   2      2    0      0      0")
    print("full-01     closed_Full -     64   64     64   0      0      0")
''',
    "bjobs": r'''
import json, os, sys
st = json.load(open(os.environ["FAKE_STATE"]))
if "-u" in sys.argv:
    print("RUN gpu"); print("PEND gpu"); print("PEND normal")
    sys.exit(0)
ids = [a for a in sys.argv[1:] if a.isdigit()]
recs = []
for j in ids:
    if j in st.get("jobs", {}):
        recs.extend(st["jobs"][j])
    else:
        recs.append({"JOBID": j, "ERROR": f"Job <{j}> is not found"})
print(json.dumps({"COMMAND": "bjobs", "JOBS": len(recs), "RECORDS": recs}))
''',
    "bhist": r'''
import json, os, sys
open(os.environ["FAKE_STATE"] + ".bhist_args", "a").write(" ".join(sys.argv[1:]) + "\n")
if json.load(open(os.environ["FAKE_STATE"])).get("bhist_none"):
    sys.exit(0)
print("Job <%s>, User <u>, Project <default>, Command <x>" % sys.argv[-1])
print("Summary of time in seconds spent in various states:")
print("  PEND     PSUSP    RUN      USUSP    SSUSP    UNKWN    TOTAL")
print("  5000     0        300      0        0        0        5300")
''',
    "sbatch": r'''
print("777;cluster1")
''',
    "bsub": r'''
import json, os, sys
st = json.load(open(os.environ["FAKE_STATE"]))
open(os.environ["FAKE_STATE"] + ".bsub_stdin", "w").write(sys.stdin.read() if not sys.stdin.isatty() else "")
mode = st.get("bsub_mode")
if "-Is" in sys.argv:
    print(f"interactive pid={os.getpid()}")
    sys.exit(0)
if mode == "bytes":
    sys.stdout.buffer.write(b"Job <4300> is submitted to queue <gpu>.\r\nno newline at end")
    sys.stderr.buffer.write(b"warn\r\n\xff")
    sys.exit(0)
if mode == "slow":
    print("Job <4301> is submitted to queue <gpu>.", flush=True)
    import time; time.sleep(4)
    sys.exit(0)
if mode == "sigterm":
    import signal; os.kill(os.getpid(), signal.SIGTERM)
if st.get("bsub_rc", 0):
    sys.stderr.write("Bad resource requirement syntax. Job not submitted.\n")
    sys.exit(st["bsub_rc"])
print(f"Job <{st['next_id']}> is submitted to queue <gpu>.")
''',
}


@pytest.fixture
def lsf(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in FAKES.items():
        p = bindir / name
        p.write_text(f"#!{sys.executable}\n{body}")
        p.chmod(0o755)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"pend": 4000, "run": 120, "next_id": 4242, "jobs": {}}))
    env = dict(os.environ, PATH=f"{bindir}:/usr/bin:/bin", FAKE_STATE=str(state), USER="tester")
    return {"env": env, "state": state, "out": tmp_path / "log", "tmp": tmp_path}


def run(lsf, script, *args, stdin=None, check=True):
    r = subprocess.run([sys.executable, script, *args], env=lsf["env"], capture_output=True, text=True,
                       timeout=60, input=stdin if stdin is not None else "")
    if check:
        assert r.returncode == 0, r.stdout + r.stderr
    return r


def set_state(lsf, **kw):
    st = json.loads(lsf["state"].read_text())
    st.update(kw)
    lsf["state"].write_text(json.dumps(st))


def jsonl(path):
    return [json.loads(x) for x in path.read_text().splitlines()]


def test_snapshot_line(lsf):
    run(lsf, PENDLOG, "snapshot", "--queue", "gpu", "--hosts-from-request", "-n 4 -gpu 'num=1:gmem=40G'",
        "--out", str(lsf["out"]))
    (s,) = jsonl(lsf["out"] / "snapshots.jsonl")
    assert s["kind"] == "snapshot" and s["scheduler"] == "lsf" and s["errors"] == []
    assert (s["queue_pend"], s["queue_run"]) == (4000, 120)
    assert (s["own_run_q"], s["own_pend_q"], s["own_pend_all"]) == (1, 1, 2)
    assert s["fairshare_priority"] == "0.010"
    assert s["hosts_total"] == 3
    fits = {h["host"]: h["fits"] for h in s["gpu_hosts"]}
    # big-01: GPU 0 is free with 80 GB; small-01 has 32 GB GPUs; full-01 has no free slots
    assert fits == {"big-01": True, "small-01": False, "full-01": False}
    assert s["hosts_fit"] == 1


def test_snapshot_never_fails(lsf):
    env = dict(lsf["env"], PATH="/nonexistent")
    r = subprocess.run([sys.executable, PENDLOG, "snapshot", "--queue", "gpu", "--out", str(lsf["out"])],
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    (s,) = jsonl(lsf["out"] / "snapshots.jsonl")
    assert s["errors"]


def test_submit_passes_output_and_code(lsf):
    r = run(lsf, PENDLOG, "submit", "--out", str(lsf["out"]), "--", "bsub", "-q", "gpu", "-n", "4", "-M", "16000",
            "-gpu", "num=1:gmem=40G", "python", "train.py")
    assert r.stdout == "Job <4242> is submitted to queue <gpu>.\n"
    (sub,) = jsonl(lsf["out"] / "jobs.jsonl")
    assert sub["job_id"] == "4242" and sub["rc"] == 0
    assert sub["request"]["gmem_gb"] == 40 and sub["request"]["cores"] == 4 and sub["request"]["queue"] == "gpu"
    assert sub["request_args"][-1] == "train.py"
    assert len(jsonl(lsf["out"] / "snapshots.jsonl")) == 1

    set_state(lsf, bsub_rc=255)
    r = run(lsf, PENDLOG, "submit", "--out", str(lsf["out"]), "--", "bsub", "-q", "gpu", "x", check=False)
    assert r.returncode == 255
    assert r.stdout == "" and r.stderr == "Bad resource requirement syntax. Job not submitted.\n"
    assert jsonl(lsf["out"] / "jobs.jsonl")[-1]["job_id"] is None


def test_submit_stdin_script(lsf):
    script = "#!/bin/bash\n#BSUB -q gpu\n#BSUB -gpu \"num=1:gmem=24G\"\n#BSUB -n 2\npython run.py\n"
    run(lsf, PENDLOG, "submit", "--stdin-script", "--out", str(lsf["out"]), "--", "bsub", "-n", "3", stdin=script)
    (sub,) = jsonl(lsf["out"] / "jobs.jsonl")
    assert sub["request"]["gmem_gb"] == 24 and sub["request"]["cores"] == 3  # command line wins
    assert (lsf["state"].parent / "state.json.bsub_stdin").read_text() == script


def started_setup(lsf):
    for jid in (4242, 4243, 4244):
        set_state(lsf, next_id=jid)
        run(lsf, PENDLOG, "submit", "--out", str(lsf["out"]), "--", "bsub", "-q", "gpu", "-gpu", "num=1:gmem=40G", "x")
    set_state(lsf, jobs={
        "4242": [{"JOBID": "4242", "STAT": "PEND", "START_TIME": "", "PEND_TIME": "700",
                  "PEND_REASON": "Job's requirements for resource reservation not satisfied: 4 hosts;"}],
        "4243": [{"JOBID": "4243", "STAT": "PEND", "START_TIME": "", "PEND_TIME": "800", "PEND_REASON": ""}],
        "4244": [{"JOBID": "4244", "STAT": "PEND", "START_TIME": "", "PEND_TIME": "800", "PEND_REASON": ""}]})
    run(lsf, PENDLOG, "started", "--out", str(lsf["out"]))
    set_state(lsf, jobs={
        "4242": [{"JOBID": "4242", "STAT": "RUN", "START_TIME": "Nov  6 10:42", "PEND_TIME": "1500",
                  "PEND_REASON": ""}],
        "4243": [{"JOBID": "4243", "STAT": "PEND", "START_TIME": "", "PEND_TIME": "1600",
                  "PEND_REASON": "Not enough GPUs: 3 hosts;"}]})
    run(lsf, PENDLOG, "started", "--out", str(lsf["out"]))  # 4244 is purged: bhist fallback


def test_started_fills_start_and_pend(lsf):
    started_setup(lsf)
    ev = [e for e in jsonl(lsf["out"] / "jobs.jsonl") if e["kind"] == "started"]
    last = {e["job_id"]: e for e in ev}
    assert last["4242"]["started"] and last["4242"]["pend_s"] == 1500 and last["4242"]["final"]
    assert not last["4243"]["started"] and last["4243"]["pend_s"] == 1600
    assert "3 hosts" in last["4243"]["pend_reason"]
    assert last["4244"]["source"] == "bhist" and last["4244"]["pend_s"] == 5000 and last["4244"]["started"]
    # a third run only looks at the job still pending
    n = len(ev)
    run(lsf, PENDLOG, "started", "--out", str(lsf["out"]))
    ev2 = [e for e in jsonl(lsf["out"] / "jobs.jsonl") if e["kind"] == "started"]
    assert [e["job_id"] for e in ev2[n:]] == ["4243"]


def test_export_join(lsf):
    started_setup(lsf)
    r = run(lsf, PENDLOG, "export", "--out", str(lsf["out"]))
    rows = {x["job_id"]: x for x in csv.DictReader(io.StringIO(r.stdout))}
    assert set(rows) == {"4242", "4243", "4244"}
    a = rows["4242"]
    assert a["started"] == "1" and a["pend_s"] == "1500" and a["pend_censored"] == "0"
    assert "4 hosts" in a["pend_reason"]  # the reason seen while it pended is kept
    assert a["snap_queue_pend"] == "4000" and a["snap_hosts_fit"] == "1" and a["gmem_gb"] == "40.0"
    assert "gmem=40G" in a["request"]
    b = rows["4243"]
    assert b["started"] == "0" and b["pend_censored"] == "1" and b["pend_s"] == "1600"


# ---------------------------------------------------------------- pend-risk
NOW = 1_800_000_000
EXPORT_COLS = ["job_id", "submit_ts", "queue", "gmem_gb", "pin", "started", "pend_s", "pend_censored"]


def write_export(path, rows):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=EXPORT_COLS)
        w.writeheader()
        for i, (gmem, pin, pend, days_ago) in enumerate(rows):
            w.writerow({"job_id": 1000 + i, "submit_ts": NOW - days_ago * 86400, "queue": "gpu",
                        "gmem_gb": gmem if gmem is not None else "", "pin": int(pin), "started": 1,
                        "pend_s": pend, "pend_censored": 0})


def synthetic(n_big, n_small, big_long, small_long):
    rows = []
    for i in range(n_big):
        rows.append((40, False, 7200 if i < big_long else 30, 1 + i % 20))
    for i in range(n_small):
        rows.append((16 if i % 2 else None, False, 7200 if i < small_long else 5, 1 + i % 20))
    rows.append((40, False, 9000, 60))  # outside the 4-week window
    return rows


def pend_risk(lsf, hist, request, *extra):
    return run(lsf, PENDRISK, "--history", str(hist), "--request", request, "--now", str(NOW), "--no-live", *extra)


def test_risk_threshold_split_and_hint(lsf):
    hist = lsf["tmp"] / "h.csv"
    write_export(hist, synthetic(25, 40, 10, 4))
    out = pend_risk(lsf, hist, "-q gpu -n 4 -gpu 'num=1:gmem=40G'").stdout
    assert "Of 25 similar jobs in the last 4 weeks, 40 % of the 25 with a known outcome waited over 1 h" in out
    assert "Hint: jobs with GPU memory at or below 32G waited over 1 h in 10 % of cases" in out
    out = pend_risk(lsf, hist, "-q gpu -gpu 'num=1:gmem=32G'").stdout
    assert "Of 40 similar jobs in the last 4 weeks, 10 % of the 40" in out
    assert "Hint" not in out
    # threshold is configurable: at 48G the 40G request joins the small class
    out = pend_risk(lsf, hist, "-q gpu -gpu 'num=1:gmem=40G'", "--gmem-threshold", "48").stdout
    assert "Of 65 similar jobs" in out


def test_risk_small_n(lsf):
    hist = lsf["tmp"] / "h.csv"
    write_export(hist, synthetic(5, 40, 3, 4))
    out = pend_risk(lsf, hist, "-q gpu -gpu 'num=1:gmem=40G'").stdout
    assert "Of 5 similar jobs in the last 4 weeks, 3 of the 5 with a known outcome waited over 1 h" in out
    assert "too few similar jobs to say" in out.lower()
    assert "Hint" not in out
    out = pend_risk(lsf, hist, "-q gpu -gpu 'num=1:gmodel=NVIDIAH10080GBHBM3:gmem=40G'").stdout
    assert "No similar jobs in the last 4 weeks: too few similar jobs to say." in out


def test_risk_never_prints_eta(lsf):
    hist = lsf["tmp"] / "h.csv"
    write_export(hist, synthetic(30, 40, 20, 2))
    snap = lsf["out"]
    run(lsf, PENDLOG, "snapshot", "--queue", "gpu", "--hosts-from-request", "-gpu num=1:gmem=40G", "--out", str(snap))
    for req in ("-q gpu -gpu 'num=1:gmem=40G'", "-q gpu -gpu 'num=1:gmem=8G'", "-q gpu -m big-01 -gpu num=1"):
        r = run(lsf, PENDRISK, "--history", str(hist), "--request", req, "--now", str(NOW),
                "--hosts-snapshot", str(snap / "snapshots.jsonl"))
        low = r.stdout.lower()
        for word in ("eta", "expected start", "will start", "estimated", "start in"):
            assert word not in low.replace("not a start-time forecast", ""), (word, r.stdout)
    r = run(lsf, PENDRISK, "--history", str(hist), "--request", "-q gpu -gpu num=1:gmem=40G", "--now", str(NOW),
            "--hosts-snapshot", str(snap / "snapshots.jsonl"))
    assert "Hosts that can take this shape right now: 1 of 3 GPU hosts" in r.stdout
    r = run(lsf, PENDRISK, "--history", str(hist), "--request", "-q gpu -gpu num=1:gmem=40G", "--now", str(NOW))
    assert "1 of 3 GPU hosts (live bhosts" in r.stdout


# The study's accounting format: same columns as its lsf_jobs.csv, 20 synthetic rows.
ACCT_COLS = ("acct_file,event_time_utc,job_id,array_idx,job_name,queue,status,exit_status,submit_utc,begin_utc,"
             "start_utc,end_utc,submit_ts,start_ts,end_ts,pend_s,run_s,n_slots,max_num_proc,mem_req_mb,rusage,"
             "select,span,hname_rule,gpu_req,gpu_num,gpu_gmem_mb,gpu_gmodel,gpu_mode,gpu_j_exclusive,"
             "runlimit_W_s,runlimit_norm_s,host_factor,exec_host,n_exec_hosts,max_rmem_mb,avg_mem_mb,cpu_s,"
             "effective_resreq,requeue_flag,source,command_head,parse_warn").split(",")


def write_acct(path):
    rows = []

    def add(job, idx, gmem_mb, pend, start=True, status="DONE", model="", queue="gpu", cmd="python t.py", day=2):
        sub = NOW - day * 86400
        rows.append({"acct_file": "lsb.acct.1", "job_id": job, "array_idx": idx, "job_name": "t", "queue": queue,
                     "status": status, "submit_ts": sub, "start_ts": sub + pend if start else "",
                     "end_ts": sub + pend + 100, "pend_s": pend if start else "", "n_slots": 4,
                     "mem_req_mb": 16000, "gpu_req": f"num=1:gmem={gmem_mb}" if gmem_mb else "",
                     "gpu_num": 1 if gmem_mb else "", "gpu_gmem_mb": f"{gmem_mb:.2f}" if gmem_mb else "",
                     "gpu_gmodel": model, "source": "bacct_raw", "command_head": cmd})
    for i in range(8):                       # 8 large-gmem jobs, 4 waited over 1 h
        add(100 + i, 0, 40960, 7200 if i < 4 else 10)
    for i in range(6):                       # 6 small-gmem jobs, none long
        add(200 + i, 0, 16384, 5)
    for k in (1, 2, 3):                      # one array of 3 elements: counts once, at its first start
        add(300, k, 40960, 100 + k * 5000)
    add(301, 0, 40960, 0, start=False, status="EXIT")   # killed while pending after 100 s: unknown
    add(302, 0, None, 1, queue="cpu-interactive")       # interactive: excluded
    add(303, 0, 40960, 9000, model="NVIDIAH10080GBHBM3")  # pinned: other group
    assert len(rows) == 20
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=ACCT_COLS)
        w.writeheader()
        w.writerows(rows)


def test_risk_reads_study_csv(lsf):
    hist = lsf["tmp"] / "lsf_jobs_sample.csv"
    write_acct(hist)
    out = pend_risk(lsf, hist, "-q gpu -gpu 'num=1:gmem=40G'").stdout
    # 8 plain + 1 array start (5100 s) + 1 killed while pending = 10 jobs, 9 known outcomes, 5 long
    assert "Of 10 similar jobs in the last 4 weeks, 5 of the 9 with a known outcome waited over 1 h" in out
    assert "too few similar jobs to say" in out.lower()
    out = pend_risk(lsf, hist, "-q gpu -gpu 'num=1:gmem=16G'").stdout
    assert "Of 6 similar jobs" in out and "0 of the 6 with a known outcome" in out
    out = pend_risk(lsf, hist, "-q gpu -gpu 'num=1:gmodel=NVIDIAH10080GBHBM3:gmem=40G'").stdout
    assert "Of 1 similar jobs" in out


# ---------------------------------------------------------------- review round 1 fixes
def submit(lsf, *args, **kw):
    return run(lsf, PENDLOG, "submit", "--out", str(lsf["out"]), "--", *args, **kw)


def test_parser_value_options_before_queue(lsf):  # O1
    submit(lsf, "bsub", "-oo", "log.txt", "-env", "all", "-q", "gpu", "-gpu", "num=1:gmem=40G", "-K", "-n", "4", "x")
    req = jsonl(lsf["out"] / "jobs.jsonl")[-1]["request"]
    assert (req["queue"], req["gmem_gb"], req["cores"]) == ("gpu", 40, 4)


def test_bsub_c_is_not_cores(lsf):  # O2
    run(lsf, PENDLOG, "submit", "--out", str(lsf["out"]), "--", "bsub", "-c", "30", "-q", "gpu", "x")
    assert jsonl(lsf["out"] / "jobs.jsonl")[-1]["request"]["cores"] is None
    submit(lsf, "sbatch", "-c", "6", "-p", "gpu", "--parsable", "job.sh")
    rec = jsonl(lsf["out"] / "jobs.jsonl")[-1]
    assert (rec["request"]["cores"], rec["request"]["queue"]) == (6, "gpu")
    assert rec["job_id"] == "777"  # O9: sbatch --parsable


def test_submit_output_bytes_unchanged(lsf):  # S3
    set_state(lsf, bsub_mode="bytes")
    r = subprocess.run([sys.executable, PENDLOG, "submit", "--out", str(lsf["out"]), "--", "bsub", "-q", "gpu", "x"],
                       env=lsf["env"], capture_output=True, timeout=60, input=b"")
    assert r.stdout == b"Job <4300> is submitted to queue <gpu>.\r\nno newline at end"
    assert r.stderr == b"warn\r\n\xff"
    assert jsonl(lsf["out"] / "jobs.jsonl")[-1]["job_id"] == "4300"


def test_submit_streams_output(lsf):  # S3
    set_state(lsf, bsub_mode="slow")
    p = subprocess.Popen([sys.executable, PENDLOG, "submit", "--out", str(lsf["out"]), "--", "bsub", "-q", "gpu", "x"],
                         env=lsf["env"], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL)
    t0 = time.time()
    line = p.stdout.readline()
    assert line == b"Job <4301> is submitted to queue <gpu>.\n" and p.poll() is None  # before bsub exits
    assert p.wait(timeout=30) == 0 and time.time() - t0 >= 3


def test_interactive_submit_execs(lsf):  # O3
    p = subprocess.Popen([sys.executable, PENDLOG, "submit", "--out", str(lsf["out"]), "--", "bsub", "-Is", "-q", "gpu",
                          "bash"], env=lsf["env"], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True)
    out, _ = p.communicate(timeout=60)
    assert out == f"interactive pid={p.pid}\n"  # same process: pendlog exec'd bsub
    rec = jsonl(lsf["out"] / "jobs.jsonl")[-1]
    assert rec["interactive"] and rec["job_id"] is None


def test_signal_exit_code(lsf):  # O11
    set_state(lsf, bsub_mode="sigterm")
    assert submit(lsf, "bsub", "-q", "gpu", "x", check=False).returncode == 143


def test_stdin_script_on_terminal(lsf):  # O10
    import pty
    master, slave = pty.openpty()
    try:
        r = subprocess.run([sys.executable, PENDLOG, "submit", "--stdin-script", "--out", str(lsf["out"]), "--",
                            "bsub"], env=lsf["env"], stdin=slave, capture_output=True, text=True, timeout=60)
    finally:
        os.close(master)
        os.close(slave)
    assert r.returncode == 2 and "bsub < job.sh" in r.stderr


def test_lost_job_stops_after_two_tries(lsf):  # O4
    submit(lsf, "bsub", "-q", "gpu", "x")
    set_state(lsf, bhist_none=True)
    for _ in range(3):
        run(lsf, PENDLOG, "started", "--out", str(lsf["out"]))
    ev = [e for e in jsonl(lsf["out"] / "jobs.jsonl") if e["kind"] == "started"]
    assert [e["final"] for e in ev] == [False, True] and ev[-1]["unknown"]
    args = (lsf["state"].parent / "state.json.bhist_args").read_text().splitlines()
    assert args == ["-n 3 -l 4242"] * 2


def test_submit_reuses_recent_snapshot(lsf):  # O5
    submit(lsf, "bsub", "-q", "gpu", "-gpu", "num=1:gmem=40G", "x")
    set_state(lsf, next_id=4243)
    submit(lsf, "bsub", "-q", "gpu", "-gpu", "num=1:gmem=40G", "y")
    set_state(lsf, next_id=4244)
    submit(lsf, "bsub", "-q", "gpu", "-gpu", "num=1:gmem=8G", "z")
    snaps = jsonl(lsf["out"] / "snapshots.jsonl")
    subs = jsonl(lsf["out"] / "jobs.jsonl")
    assert len(snaps) == 2
    assert subs[0]["snapshot_id"] == subs[1]["snapshot_id"] == snaps[0]["snap_id"] != subs[2]["snapshot_id"]


def test_submit_snapshot_time_capped(lsf):  # O5
    set_state(lsf, bqueues_sleep=30)
    t0 = time.time()
    r = submit(lsf, "bsub", "-q", "gpu", "x")
    assert time.time() - t0 < 20 and "Job <4242>" in r.stdout
    assert any("budget" in e or "timeout" in e for e in jsonl(lsf["out"] / "snapshots.jsonl")[-1]["errors"])


def test_export_joins_on_snapshot_id(lsf):  # S2/O7
    submit(lsf, "bsub", "-q", "gpu", "-gpu", "num=1:gmem=40G", "x")
    set_state(lsf, next_id=4243)
    submit(lsf, "bsub", "-q", "gpu", "-gpu", "num=1:gmem=8G", "y")
    # force both submits and snapshots onto the same second, as fast submits are
    for name in ("jobs.jsonl", "snapshots.jsonl"):
        f = lsf["out"] / name
        f.write_text("".join(json.dumps(dict(r, ts=1)) + "\n" for r in jsonl(f)))
    rows = {x["job_id"]: x for x in csv.DictReader(io.StringIO(run(lsf, PENDLOG, "export", "--out",
                                                                   str(lsf["out"])).stdout))}
    assert (rows["4242"]["snap_hosts_fit"], rows["4243"]["snap_hosts_fit"]) == ("1", "2")


def test_risk_gate_counts_known_outcomes(lsf):  # S1
    hist = lsf["tmp"] / "h.csv"
    with open(hist, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=EXPORT_COLS)
        w.writeheader()
        for i in range(25):  # pending under 1 h so far: outcome unknown
            w.writerow({"job_id": i, "submit_ts": NOW - 3600, "queue": "gpu", "gmem_gb": 40, "pin": 0,
                        "started": 0, "pend_s": 600, "pend_censored": 1})
        w.writerow({"job_id": 99, "submit_ts": NOW - 7200, "queue": "gpu", "gmem_gb": 40, "pin": 0,
                    "started": 1, "pend_s": 120, "pend_censored": 0})
    out = pend_risk(lsf, hist, "-q gpu -gpu num=1:gmem=40G").stdout
    assert "Of 26 similar jobs in the last 4 weeks, 0 of the 1 with a known outcome waited over 1 h" in out
    assert "median wait of those that started 2 min" in out and "too few similar jobs to say" in out.lower()
    assert "%" not in out
