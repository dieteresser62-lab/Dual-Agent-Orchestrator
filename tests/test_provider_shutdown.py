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


def test_nohup_preserves_inherited_ignored_hangup():
    from cli import shutdown_signals
    original = signal.getsignal(signal.SIGHUP)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    try:
        with shutdown_signals():
            assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
            os.kill(os.getpid(), signal.SIGHUP)
        assert signal.getsignal(signal.SIGHUP) == signal.SIG_IGN
    finally:
        signal.signal(signal.SIGHUP, original)


def test_sigterm_in_popen_ownership_window_reaps_provider(tmp_path, monkeypatch):
    import agent_runtime
    from cli import shutdown_signals, ShutdownRequested
    real_popen = subprocess.Popen
    processes, started = [], []
    def interrupted_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        os.kill(os.getpid(), signal.SIGTERM)
        return process
    monkeypatch.setattr(agent_runtime.subprocess,'Popen',interrupted_popen)
    with shutdown_signals(), pytest.raises(ShutdownRequested):
        agent_runtime._run_agent_process(object(),[sys.executable,'-c','import time; time.sleep(60)'],None,
            config=agent_runtime.OrchestratorConfig(repo_root=tmp_path), env=os.environ.copy(),
            execution_root=tmp_path, timeout_seconds=None, agent_key='fake', process_started=started.append)
    assert started == [processes[0].pid]
    assert len(processes) == 1 and processes[0].poll() is not None
    assert not alive(processes[0].pid)


def test_git_transaction_finishes_before_shutdown_without_index_lock(tmp_path, monkeypatch):
    from cli import shutdown_signals, ShutdownRequested
    import git_service
    seen = []
    def local_git(*args, **kwargs):
        lock = tmp_path / 'index.lock'
        lock.write_text('transaction active')
        os.kill(os.getpid(),signal.SIGTERM)
        # The handler must be deferred until this transaction has settled.
        assert lock.exists()
        lock.unlink()
        seen.append(True)
        return subprocess.CompletedProcess(args[0],0,b'',b'')
    monkeypatch.setattr(git_service.subprocess,'run',local_git)
    with shutdown_signals(), pytest.raises(ShutdownRequested):
        git_service._git(tmp_path, 'add','safe')
    assert seen == [True] and not (tmp_path / 'index.lock').exists()


def test_runner_cleans_descendants_when_leader_exits_first(tmp_path):
    pidfile = tmp_path / 'orphan.json'
    orphan = ('import os,signal,time,json,pathlib; '
              'signal.signal(signal.SIGTERM,signal.SIG_IGN); '
              f'pathlib.Path({str(pidfile)!r}).write_text(json.dumps(os.getpid())); time.sleep(60)')
    root = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{orphan!r}],start_new_session=True); time.sleep(0.5)"
    try:
        result = runner.run_process([sys.executable,'-c',root],tmp_path,tmp_path/'orphan.log',timeout=10)
        assert result['returncode'] == 0 and not result['timed_out']
        assert pidfile.exists() and not alive(json.loads(pidfile.read_text()))
    finally:
        if pidfile.exists() and alive(json.loads(pidfile.read_text())):
            os.kill(json.loads(pidfile.read_text()),signal.SIGKILL)


@pytest.mark.parametrize('number', [signal.SIGINT, signal.SIGTERM])
def test_signal_during_waiting_git_child_leaves_no_index_lock(tmp_path, monkeypatch, number):
    from cli import shutdown_signals, ShutdownRequested
    import git_service
    real_run = subprocess.run
    gitdir = tmp_path / '.git'
    gitdir.mkdir()
    code = ('import os,signal,time,pathlib; '
            f'lock=pathlib.Path({str(gitdir / "index.lock")!r}); lock.write_text("locked"); '
            f'os.kill(os.getppid(),{int(number)}); time.sleep(0.1); lock.unlink()')
    def fake_git(*args, **kwargs):
        assert args[0] == ['git','add','safe']
        return real_run([sys.executable,'-c',code], **kwargs)
    monkeypatch.setattr(git_service.subprocess,'run',fake_git)
    with shutdown_signals(), pytest.raises(ShutdownRequested) as caught:
        git_service._git(tmp_path,'add','safe')
    assert caught.value.signal_number == number
    assert not (gitdir / 'index.lock').exists()
