"""Tests for skills/cluster-ops/scripts/xfer with a fake ssh that runs the remote command locally under bash.

  uv run --with pytest python -m pytest tests/test_xfer.py
"""
import hashlib
import os
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XFER = os.path.join(REPO, "skills", "cluster-ops", "scripts", "xfer")

# Fake ssh: "fakessh HOST COMMAND". With FAKE_CUT set, a dd range is cut short once per part, so the first
# attempt fails and the retry succeeds. With FAKE_BREAK set, every dd range is cut short.
FAKE_SSH = r"""#!/usr/bin/env bash
host=$1; shift
echo "$host" >> "$FAKE_DIR/hosts"
cmd=$*
if [[ $cmd == dd* && ( -n ${FAKE_BREAK:-} || -n ${FAKE_CUT:-} ) ]]; then
  mark="$FAKE_DIR/cut.$(printf '%s' "$cmd" | sha256sum | cut -c1-16)"
  if [[ -n ${FAKE_BREAK:-} || ! -e $mark ]]; then
    touch "$mark"; bash -c "$cmd" | head -c 1000; exit 0
  fi
fi
exec bash -c "$cmd"
"""


@pytest.fixture
def env(tmp_path):
    fake = tmp_path / "fakessh"
    fake.write_text(FAKE_SSH)
    fake.chmod(0o755)
    e = dict(os.environ, XFER_SSH=str(fake), FAKE_DIR=str(tmp_path), XFER_RETRIES="2")
    for k in ("FAKE_CUT", "FAKE_BREAK", "XFER_NOFULLHASH"):
        e.pop(k, None)
    return e


def run(env, *args, **kw):
    return subprocess.run([XFER, *args], env=env, capture_output=True, text=True, timeout=120, **kw)


def make(path, size):
    data = os.urandom(size)
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def test_help():
    r = subprocess.run([XFER, "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "xfer get" in r.stdout


def test_get_parallel_matches(env, tmp_path):
    src = tmp_path / "remote dir" / "big file.bin"
    src.parent.mkdir()
    h = make(src, 5 * 1048576 + 123)
    out = tmp_path / "local.bin"
    r = run(env, "get", "-n", "4", f"host1:{src}", str(out))
    assert r.returncode == 0, r.stderr
    assert hashlib.sha256(out.read_bytes()).hexdigest() == h
    assert f"OK {out} sha256 {h}" in r.stdout
    assert (tmp_path / "local.bin.sha256").read_text().split()[0] == h
    assert not (tmp_path / "local.bin.parts").exists()
    assert set((tmp_path / "hosts").read_text().split()) == {"host1"}


def test_get_empty_file(env, tmp_path):
    src = tmp_path / "empty"
    src.write_bytes(b"")
    out = tmp_path / "out"
    r = run(env, "get", f"h:{src}", str(out))
    assert r.returncode == 0, r.stderr
    assert out.read_bytes() == b""


def test_get_retries_a_cut_part(env, tmp_path):
    src = tmp_path / "src.bin"
    h = make(src, 3 * 1048576)
    out = tmp_path / "out.bin"
    r = run(dict(env, FAKE_CUT="1"), "get", "-n", "3", f"h:{src}", str(out))
    assert r.returncode == 0, r.stderr
    assert "retry 1" in r.stderr
    assert hashlib.sha256(out.read_bytes()).hexdigest() == h


def test_get_fails_and_keeps_nothing_wrong(env, tmp_path):
    src = tmp_path / "src.bin"
    make(src, 2 * 1048576)
    out = tmp_path / "out.bin"
    r = run(dict(env, FAKE_BREAK="1"), "get", "-n", "2", f"h:{src}", str(out))
    assert r.returncode == 4
    assert not out.exists()


def test_get_refuses_overwrite_and_bad_spec(env, tmp_path):
    out = tmp_path / "exists"
    out.write_text("x")
    assert run(env, "get", "h:/x", str(out)).returncode == 2
    assert run(env, "get", "h:relative", str(tmp_path / "n")).returncode == 2
    assert run(env, "get", f"h:{tmp_path}/missing", str(tmp_path / "n")).returncode == 3


def test_many(env, tmp_path):
    rdir = tmp_path / "r"
    rdir.mkdir()
    hashes = {f"f{i}.bin": make(rdir / f"f{i}.bin", 200000 + i) for i in range(5)}
    lst = tmp_path / "list.txt"
    lst.write_text("\n".join(str(rdir / n) for n in hashes) + "\n\n")
    out = tmp_path / "out"
    r = run(env, "many", "-P", "3", "h", str(lst), str(out))
    assert r.returncode == 0, r.stderr
    for n, h in hashes.items():
        assert hashlib.sha256((out / n).read_bytes()).hexdigest() == h
    r2 = run(env, "many", "h", str(lst), str(out))
    assert r2.returncode == 0 and r2.stdout.count("skip") == 5


def test_many_rejects_duplicates(env, tmp_path):
    lst = tmp_path / "list.txt"
    lst.write_text("/a/x.bin\n/b/x.bin\n")
    r = run(env, "many", "h", str(lst), str(tmp_path / "o"))
    assert r.returncode == 2 and "duplicate" in r.stderr


def test_put(env, tmp_path):
    src = tmp_path / "up.bin"
    h = make(src, 1048576 + 7)
    dest = tmp_path / "remote" / "up copy.bin"
    dest.parent.mkdir()
    r = run(env, "put", str(src), f"h:{dest}")
    assert r.returncode == 0, r.stderr
    assert hashlib.sha256(dest.read_bytes()).hexdigest() == h
    assert not list(dest.parent.glob("*.xfer-partial"))
    r2 = run(env, "put", str(src), f"h:{dest}")
    assert r2.returncode == 7
