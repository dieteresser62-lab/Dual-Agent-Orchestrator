from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from scripts.qualification import run_implementer_package as runner


def alive(pid):
    stat = runner._process_stat(pid)
    return stat is not None and stat[0] not in {"Z", "X", "x"}


def wait_file(path, process):
    deadline = time.monotonic() + 10
    while not path.exists() and time.monotonic() < deadline:
        assert process.poll() is None
        time.sleep(0.02)
    assert path.exists()
    return json.loads(path.read_text())


@pytest.mark.parametrize("number", (signal.SIGINT, signal.SIGTERM, signal.SIGHUP))
@pytest.mark.parametrize("stream", (False, True))
def test_cli_signal_unwinds_real_provider_session(tmp_path, number, stream):
    # Python-only doubles: one provider, plus a stubborn child in another PGID.
    child_file, provider_file = tmp_path / "child.json", tmp_path / "provider.json"
    child = ("import os,signal,time,json,pathlib; os.setpgid(0,0); "
             "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
             f"pathlib.Path({str(child_file)!r}).write_text(json.dumps(os.getpid())); time.sleep(60)")
    provider = ("import os,signal,time,json,pathlib,subprocess,sys; "
                "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                f"subprocess.Popen([sys.executable,'-c',{child!r}]); "
                f"pathlib.Path({str(provider_file)!r}).write_text(json.dumps(os.getpid())); time.sleep(60)")
    code = f"""
import os,sys,pathlib
import cli,agent_runtime
root=pathlib.Path({str(tmp_path)!r})
def invoke(*args,**kwargs):
    agent_runtime._run_agent_process(object(), [sys.executable,'-c',{provider!r}], None,
        config=agent_runtime.OrchestratorConfig(repo_root=root, agent_live_stream={stream!r}),
        env=os.environ.copy(), execution_root=root, timeout_seconds=None, agent_key='fake')
sys.exit(cli.main([],run_pipeline_fn=invoke,watch_inbox_fn=invoke,
                  find_task_file_fn=lambda path:root/'task.md'))
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    process = subprocess.Popen([sys.executable, "-c", code], cwd=tmp_path,
                               env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    pids = []
    try:
        pids = [wait_file(provider_file, process), wait_file(child_file, process)]
        assert os.getsid(pids[0]) == os.getsid(pids[1]) != os.getsid(process.pid)
        assert os.getpgid(pids[0]) != os.getpgid(pids[1])
        process.send_signal(number)
        _, stderr = process.communicate(timeout=10)
        assert process.returncode == 128 + number, stderr
        assert "interrupted; resume with --resume" in stderr
        assert not any(alive(pid) for pid in pids)
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        for pid in pids:
            if alive(pid):
                os.kill(pid, signal.SIGKILL)


def test_runner_timeout_stops_a_grandchild_in_its_own_session(tmp_path):
    pidfile = tmp_path / "grandchild.json"
    grandchild = ("import os,signal,time,json,pathlib; "
                  "signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                  f"pathlib.Path({str(pidfile)!r}).write_text(json.dumps(os.getpid())); time.sleep(60)")
    child = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{grandchild!r}],start_new_session=True); time.sleep(60)"
    root = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}],start_new_session=True); time.sleep(60)"
    try:
        result = runner.run_process([sys.executable, "-c", root], tmp_path,
                                    tmp_path / "runner.log", timeout=1)
        assert result["timed_out"]
        assert result["returncode"] == -signal.SIGTERM
        assert pidfile.exists()
        assert not alive(json.loads(pidfile.read_text()))
    finally:
        if pidfile.exists() and alive(json.loads(pidfile.read_text())):
            os.kill(json.loads(pidfile.read_text()), signal.SIGKILL)
