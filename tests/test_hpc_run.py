"""Tests for hpc-run using a fake ssh stub (no cluster needed)."""

from pathlib import Path
import os
import stat
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/cluster-ops/scripts/hpc-run"


def make_fake_ssh(directory: Path, body: str) -> Path:
    stub = directory / "fake-ssh"
    stub.write_text("#!/usr/bin/env bash\n" + body)
    stub.chmod(0o755)
    return stub


class HpcRunTests(unittest.TestCase):
    def run_cmd(self, args, *, env=None, stdin_text=None):
        run_env = os.environ.copy()
        for k in ("HPC_SSH", "HPC_HOST", "HPC_RUN_TIMEOUT", "HPC_RUN_CONF"):
            run_env.pop(k, None)
        if env:
            run_env.update(env)
        return subprocess.run(
            [str(SCRIPT), *args],
            input=stdin_text,
            text=True,
            capture_output=True,
            env=run_env,
            check=False,
        )

    def test_help_and_executable(self):
        self.assertTrue(SCRIPT.stat().st_mode & stat.S_IXUSR)
        res = self.run_cmd(["--help"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("hpc-run", res.stdout)
        self.assertIn("bash -lc", res.stdout)

    def test_wraps_command_in_bash_lc_preserving_quotes(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = make_fake_ssh(
                Path(tmp),
                'host=$1; shift; printf "HOST=%s\\n" "$host"; eval "$*"\n',
            )
            res = self.run_cmd(
                ["--host", "user@head1", "--ssh", str(stub), "printf '%s\\n' 'a b' \"c d\""],
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stdout.splitlines(), ["HOST=user@head1", "a b", "c d"])

    def test_reads_conf_file_and_env_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            stub = make_fake_ssh(p, 'printf "HOST=%s CMD=%s\\n" "$1" "$2"\n')
            conf = p / "hpc-run.conf"
            conf.write_text(f'# comment\nHPC_SSH="{stub}"\nHPC_HOST="u@from-conf"\n')
            res = self.run_cmd(["echo hi"], env={"HPC_RUN_CONF": str(conf)})
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("HOST=u@from-conf", res.stdout)
            self.assertIn("bash -lc", res.stdout)

            res2 = self.run_cmd(["echo hi"], env={"HPC_RUN_CONF": str(conf), "HPC_HOST": "u@from-env"})
            self.assertEqual(res2.returncode, 0, res2.stderr)
            self.assertIn("HOST=u@from-env", res2.stdout)

    def test_stdin_mode_uses_bash_ls(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = make_fake_ssh(Path(tmp), 'host=$1; shift; printf "ARG=%s\\n" "$*"; cat\n')
            res = self.run_cmd(
                ["--host", "u@h", "--ssh", str(stub)],
                stdin_text="echo from-stdin\n",
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stdout, "ARG=bash -ls\necho from-stdin\n")

    def test_lsf_exit_127_prints_path_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = make_fake_ssh(Path(tmp), 'echo "bash: line 1: bjobs: command not found" >&2; exit 127\n')
            res = self.run_cmd(["--host", "u@h", "--ssh", str(stub), "bjobs"])
            self.assertEqual(res.returncode, 127)
            self.assertIn("LSF not on PATH", res.stderr)

    def test_non_lsf_exit_127_does_not_print_lsf_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = make_fake_ssh(Path(tmp), 'echo "bash: line 1: nonexistent_tool: command not found" >&2; exit 127\n')
            res = self.run_cmd(["--host", "u@h", "--ssh", str(stub), "nonexistent_tool"])
            self.assertEqual(res.returncode, 127)
            self.assertNotIn("LSF not on PATH", res.stderr)

    def test_timeout_returns_124_with_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = make_fake_ssh(Path(tmp), "sleep 5\n")
            res = self.run_cmd(["-t", "1", "--host", "u@h", "--ssh", str(stub), "sleep 5"])
            self.assertEqual(res.returncode, 124)
            self.assertIn("timed out after 1s", res.stderr)

    def test_command_mode_disconnects_stdin_to_dev_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = make_fake_ssh(Path(tmp), 'printf "IN=%s\\n" "$(cat)"\n')
            res = self.run_cmd(
                ["--host", "u@h", "--ssh", str(stub), "echo ignored"],
                stdin_text="should-not-be-read\n",
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stdout.strip(), "IN=")

    def test_missing_host_exits_2(self):
        res = self.run_cmd(["echo hi"], env={"HPC_RUN_CONF": "/nonexistent/hpc-run.conf"})
        self.assertEqual(res.returncode, 2)
        self.assertIn("no host set", res.stderr)


if __name__ == "__main__":
    unittest.main()
