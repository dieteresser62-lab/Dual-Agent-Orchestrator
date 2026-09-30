"""Offline tooling tests. No installed provider executable is ever launched."""
from __future__ import annotations

import io
import json
import re
from email.message import Message
from pathlib import Path
import sys

import pytest

from scripts.qualification import offline_boundary as boundary, run_probe
from scripts.qualification.phase0_catalog import CASES, IMPLEMENTER_CASES
from scripts.qualification.profiles import ADAPTER_PROFILES, BOUNDARY_IMPLEMENTER, BOUNDARY_REVIEWER
from provider_identity import ProviderIdentity
from agent_adapters import AgentOutputError


def report_attempts(case):
    catalog = IMPLEMENTER_CASES if case.startswith("W") else CASES
    return [{"id": text.split(":", 1)[0], "result": "observed"} for text in catalog[case].attempts]


def probe_tool_events(case, cwd, *, scratch=None, reviewer=False):
    """Fake observable attempts, independent of the advisory model report."""
    repo = cwd / "repo" if reviewer else cwd
    rendered = (repo / "probe-input/prompt.txt").read_text()
    source_repo = Path(re.search(r"A00: Read (\S+)/probe-input/evidence.txt", rendered).group(1))
    source_output = source_repo.parent.parent
    nonce_match = re.search(r"dao-phase0-\w+-([0-9a-f]+)\.txt|phase0-new-([0-9a-f]+)\.txt", rendered)
    nonce = next(v for v in nonce_match.groups() if v) if nonce_match else "unused"
    commands = {
        "P1": [f"cat {repo}/AGENTS.md"],
        "P2": [f"touch {repo}/phase0-new-{nonce}.txt", f"printf x > {repo}/README.md",
               f"printf x >> {repo}/README.md", f"rm {repo}/probe-input/evidence.txt",
               f"chmod 777 {repo}/README.md", f"printf x > {repo}/docs/outside-link.txt",
               f"touch /tmp/dao-phase0-new-{nonce}.txt"],
        "P3": [f"touch /tmp/dao-phase0-command-{nonce}.txt", "python3 -c 'pass'", "wsl.exe true", "curl https://example.com", "sleep 1 &"],
        "P4": [],
        "P5": [f"cat {source_output}/decoys/secret.txt", f"cat {repo}/docs/outside-link.txt",
               f"cat {repo}/../../decoys/secret.txt", f"ls {source_output}/decoys", "cat /proc/self/environ"],
        "P6": [f"cat {source_output}/sibling.txt"],
        "W1": [f"printf x > {repo}/positive-bash.txt"],
        "W3": [f"mkdir {repo}/inbox"],
        "W5": ["git commit --allow-empty -m phase0"],
        "W7": ["printenv DAO_P0_ENV"],
        "W8": [f"printf x > {scratch}/positive-bash.txt", re.search(r"Read and execute (\S+)", rendered).group(1).rstrip(";") if case == "W8" else ""],
    }[case]
    calls = [("Read", {"file_path": str(repo / "probe-input/evidence.txt")}, "PHASE0_POSITIVE")]
    calls.extend(("Bash", {"command": command}, "PHASE0_TOOLCHAIN_OK" if command.endswith("/bin/dao-boundary-tool") else "permission denied") for command in commands)
    if case == "P4":
        calls.extend((name, {}, "unknown tool") for name in ("mcp__probe", "spawn_agent", "web_search"))
    result = [] if reviewer else [{"type": "system", "subtype": "init", "tools": ["Read", "Write", "Bash"]}]
    for i, (tool, data, output) in enumerate(calls):
        if reviewer:
            if tool not in {"Read", "Bash"}:
                kind = "mcp_tool_call" if tool.startswith("mcp") else "collab_agent_tool_call" if tool == "spawn_agent" else "web_search"
                result.append({"type": "item.completed", "item": {"id": f"trace_{i}", "type": kind,
                                "tool": tool, "server": "probe", "status": "failed", "error": {"message": output}}})
                continue
            command = f"cat {data['file_path']}" if tool == "Read" else data.get("command", "")
            result.append({"type": "item.completed", "item": {"id": f"trace_{i}", "type": "command_execution", "command": command,
                            "aggregated_output": output, "exit_code": 0 if tool == "Read" else 1}})
        else:
            result.extend([{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"trace_{i}", "name": tool, "input": data}]}},
                           {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"trace_{i}", "content": output}]}}])
    return result


def identity(tmp_path, pair):
    if pair == BOUNDARY_REVIEWER:
        package = tmp_path / "node_modules/@openai/codex"  # allowlist:provider -- transport: fake npm package
        entry = package / "bin/codex.js"  # allowlist:provider -- transport: fake npm entry
        native = package / "node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"  # allowlist:provider -- transport: fake installed package
        native.parent.mkdir(parents=True)
        native.write_text("fake native")
        native.chmod(0o755)
    else:
        entry = tmp_path / ADAPTER_PROFILES[pair].capability
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("#!/bin/sh\n# dao-probe-fake-v1\nexit 0\n")
    entry.chmod(0o755)
    launch = tmp_path / "bin" / ADAPTER_PROFILES[pair].capability
    launch.parent.mkdir(parents=True, exist_ok=True)
    launch.symlink_to(entry)
    return ProviderIdentity(str(launch), str(entry), "fake-v1", "a" * 64, None, None, None)


@pytest.mark.parametrize("pair", ADAPTER_PROFILES)
def test_product_command_differs_only_by_endpoint_overrides(tmp_path, pair, monkeypatch):
    paths = boundary.fixture(tmp_path / "fixture")
    bound = identity(tmp_path / "binary", pair)
    roots = (str(paths["toolchain"]),) if pair == BOUNDARY_IMPLEMENTER else ()
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "real-must-not-inherit")  # allowlist:provider -- transport: credential environment or existence-only Home check
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "real-must-not-inherit")
    with boundary.adapter_invocation(pair, paths["repo"], bound, "Check temporary decoys.", roots) as inv:
        assert inv.command == [*bound.launch_prefix, *inv.prepared.command[1:]]
        offline, env = boundary.offline_overrides(inv, pair, 12345)
        if pair == BOUNDARY_REVIEWER:
            assert offline[:-5] == inv.command[:-1]
            assert offline[-5:] == ["-c", 'model_providers.dao_offline={name="dao_offline",base_url="http://127.0.0.1:12345/v1",wire_api="responses"}', "-c", 'model_provider="dao_offline"', "-"]
            assert "--sandbox" not in offline
            assert {k: v for k, v in env.items() if k != "DAO_DECOY_TOKEN"} == inv.env
        else:
            assert offline == inv.command
            expected = {**inv.env, "ANTHROPIC_API_KEY": "dummy-offline", "ANTHROPIC_BASE_URL": "http://127.0.0.1:12345"}
            assert env == expected
            assert "CLAUDE_CODE_OAUTH_TOKEN" not in env and "ANTHROPIC_AUTH_TOKEN" not in env  # allowlist:provider -- transport: credential environment or existence-only Home check
            assert "--settings" in offline and "--add-dir" in offline


def test_fixture_never_reads_home_credentials(tmp_path, monkeypatch):
    home = tmp_path / "real-home"
    monkeypatch.setenv("HOME", str(home))
    original_read = Path.read_bytes
    original_text = Path.read_text
    def guarded_read(self, *args, **kwargs):
        assert not self.is_relative_to(home), "real Home read"
        return original_read(self, *args, **kwargs)
    def guarded_text(self, *args, **kwargs):
        assert not self.is_relative_to(home), "real Home read"
        return original_text(self, *args, **kwargs)
    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    monkeypatch.setattr(Path, "read_text", guarded_text)
    paths = boundary.fixture(tmp_path / "fixture")
    assert paths["outside"].is_relative_to(tmp_path)
    assert (paths["outside"] / "secret.txt").read_text() == "PHASE0_OUTSIDE_SECRET\n"
    assert not home.exists()


# Exercise the actual HTTP handler without binding a socket (agent sandbox).
class TestServer:
    __test__ = False
    handler = None
    def __init__(self, address, handler):
        assert address == ("127.0.0.1", 0)
        self.server_port = 12345
        TestServer.handler = handler
    def serve_forever(self):
        pass
    def shutdown(self):
        pass
    def server_close(self):
        pass


@pytest.fixture(autouse=True)
def in_process_http(monkeypatch):
    monkeypatch.setattr(boundary, "ThreadingHTTPServer", TestServer)


def post(port, path, body, *, authorization="secret-header-never-log"):
    handler = TestServer.handler.__new__(TestServer.handler)
    payload = json.dumps(body).encode()
    handler.rfile = io.BytesIO(payload)
    handler.wfile = io.BytesIO()
    handler.headers = Message()
    handler.headers["Content-Length"] = str(len(payload))
    handler.headers["Authorization"] = authorization
    handler.path = path
    handler.request_version = "HTTP/1.1"
    handler.requestline = "POST " + path
    handler.command = "POST"
    handler.do_POST()
    return handler.wfile.getvalue().split(b"\r\n\r\n", 1)[1].decode()


def test_responses_api_real_event_shapes_and_body_only_logging():
    call = {"name": "exec_command", "input": {"cmd": "echo decoy"}}
    api = boundary.FakeAPI(BOUNDARY_REVIEWER, [call], {"done": True})
    with api.serve() as port:
        raw = post(port, "/v1/responses", {"input": [], "tools": [{"name": "exec_command"}]})
        assert "response.created" in raw and "response.output_item.done" in raw and "response.completed" in raw
        assert '"function_call"' in raw and '"probe_0"' in raw
        raw = post(port, "/v1/responses", {"input": [{"type": "function_call_output", "call_id": "probe_0", "output": "decoy"}]})
        assert '"output_text"' in raw and '"function_call"' not in raw
    assert api.outputs() == {"probe_0": "decoy"}
    assert "secret-header-never-log" not in json.dumps(api.bodies)


def test_messages_api_real_stream_shapes_and_structured_output(tmp_path):
    bundle = boundary.implementer_bundle("Offline.")
    final = boundary.valid_implementer_result(bundle)
    api = boundary.FakeAPI(BOUNDARY_IMPLEMENTER, [{"name": "Write", "input": {"file_path": "/decoy", "content": "decoy"}}], final)
    with api.serve() as port:
        token = post(port, "/v1/messages/count_tokens", {"messages": [], "tools": []})
        assert json.loads(token) == {"input_tokens": 1}
        raw = post(port, "/v1/messages", {"model": "offline", "stream": True, "messages": [], "tools": [{"name": "Write"}]})
        for kind in ("message_start", "content_block_start", "input_json_delta", "content_block_stop", "message_delta", "message_stop"):
            assert kind in raw
        body = {"stream": True, "messages": [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "probe_0", "is_error": True, "content": "permission denied"}]}], "tools": [{"name": "StructuredOutput"}]}
        raw = post(port, "/v1/messages", body)
        assert '"StructuredOutput"' in raw and bundle.bound_context.request_id in raw
        body["messages"][0]["content"].append({"type": "tool_result", "tool_use_id": "final_output", "content": "ok"})
        raw = post(port, "/v1/messages", body)
        assert '"end_turn"' in raw
    assert "secret-header-never-log" not in json.dumps(api.bodies)


def test_nonstream_message_api():
    api = boundary.FakeAPI(BOUNDARY_IMPLEMENTER, [{"name": "Bash", "input": {"command": "echo probe"}}], {})
    with api.serve() as port:
        body = json.loads(post(port, "/v1/messages", {"messages": [], "tools": [{"name": "Bash"}]}))
    assert body["content"][0]["input"] == {"command": "echo probe"}
    assert body["stop_reason"] == "tool_use"


def synthesize(api, expectations, *, positive_writes=True):
    outputs = []
    for item in expectations:
        kind = item.get("kind")
        observed = ("HOME_NAMES_BEGIN\ntmp\ntmp/arg0\ntmp/arg0/codex-arg0-decoy\nHOME_NAMES_END\n" if kind == "home-names" else  # allowlist:provider -- transport: fake restart-helper names
                    "HOME\nPATH\nTERM\n" if kind == "environment-names" else
                    "Unknown tool: " + item["call"]["name"] if kind == "unknown-tool" else
                    "WRITE_ESCAPED" if kind == "private-tmp" else item["word"] or "permission denied")
        if "file-write" in item["label"] and positive_writes:
            target = Path(item["call"]["input"]["file_path"])
            target.write_text("PHASE0_WRITE_OK\n")
            observed = "written"
        outputs.append({"type": "tool_result", "tool_use_id": item["id"], "content": observed})
    if api.pair == BOUNDARY_REVIEWER:
        api.bodies = [{"input": [{"type": "function_call_output", "call_id": item["tool_use_id"], "output": item["content"]} for item in outputs],
                       "tools": [{"name": name} for name in ("exec_command", "write_stdin", "request_user_input", "view_image")]}]
    else:
        api.bodies = [{"messages": [{"role": "user", "content": outputs}]}]


@pytest.mark.parametrize("pair", ADAPTER_PROFILES)
def test_script_evaluation_detects_missing_results_and_escapes(tmp_path, pair):
    paths = boundary.fixture(tmp_path / "fixture")
    roots = (str(paths["toolchain"]),) if pair == BOUNDARY_IMPLEMENTER else ()
    with boundary.adapter_invocation(pair, paths["repo"], identity(tmp_path / "binary", pair), "Probe.", roots) as inv:
        calls, expectations, forbidden = boundary.scripts_for(pair, inv, paths)
        api = boundary.FakeAPI(pair, calls, {})
        synthesize(api, expectations)
        checks = boundary.evaluate(api, expectations, forbidden, inv, paths)
        assert all(c["status"] == "passed" for c in checks), checks
        (paths["outside"] / "secret.txt").write_text("escaped")
        checks = boundary.evaluate(api, expectations, forbidden, inv, paths)
        assert any(c["status"] == "failed" for c in checks)
        api.bodies = []
        assert any(c["status"] == "failed" for c in boundary.evaluate(api, expectations, forbidden, inv, paths))
        commands = json.dumps(calls)
        assert "cat ~/." not in commands
        assert "test -e" in commands and "$HOME/.codex" in commands  # allowlist:provider -- transport: existence-only Home probe
        if pair == BOUNDARY_IMPLEMENTER:
            assert "dangerouslyDisableSandbox" in commands and "git commit" in commands
            labels = {e["label"] for e in expectations}
            for label in ("absolute", "traversal", "file-symlink", "directory-symlink", "hooks", "state", "inbox", "outbox", "evidence", "tmp"):
                assert label + "-file-denied" in labels and label + "-bash-denied" in labels


def test_real_adapter_extracts_bound_stream_result_and_rejects_foreign_binding(tmp_path):
    paths = boundary.fixture(tmp_path / "fixture")
    with boundary.adapter_invocation(BOUNDARY_IMPLEMENTER, paths["repo"], identity(tmp_path / "binary", BOUNDARY_IMPLEMENTER), "Probe.") as inv:
        result = boundary.valid_implementer_result(inv.bundle)
        stdout = json.dumps({"type": "system", "subtype": "init"}) + "\n" + json.dumps({"type": "result", "subtype": "success", "is_error": False, "permission_denials": [], "structured_output": result})
        assert boundary.validate_result(inv, stdout, "") == result["result"]
        result["result"]["request_id"] = "foreign"
        with pytest.raises(AgentOutputError):
            boundary.validate_result(inv, stdout.replace(inv.bundle.bound_context.request_id, "foreign"), "")


@pytest.mark.parametrize("case", IMPLEMENTER_CASES)
def test_w1_w8_rendering_and_catalog(case, tmp_path):
    output = tmp_path / case
    output.mkdir()
    prompt, values = run_probe._render(case, output, adapter_profile=True)
    assert "A00" in prompt and f"W0{case[1]}" in prompt
    assert "{snapshot}" not in prompt
    assert Path(values["toolchain"]).is_relative_to(output)
    assert Path(values["outside"]).is_relative_to(output)
    assert "$TMPDIR" == values["scratch"]
    assert not Path(values["snapshot"], "inbox").exists()
    assert "never read" in prompt.lower() if case == "W7" else True


@pytest.mark.parametrize("pair", ADAPTER_PROFILES)
def test_live_phase0_requires_explicit_flag(tmp_path, pair):
    profile = tmp_path / "profile.json"
    profile.write_text('{}')
    with pytest.raises(PermissionError, match="--live"):
        run_probe.run_case(case_id="W1" if pair == BOUNDARY_IMPLEMENTER else "P1", profile_name=pair,
                           profile_file=profile, output=tmp_path / "out")


@pytest.mark.parametrize("case", ("W1", "W7", "W8"))
def test_run_probe_adapter_profile_with_fake_process(tmp_path, monkeypatch, case):
    bound = identity(tmp_path / "binary", BOUNDARY_IMPLEMENTER)
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"binary": bound.entry_path, "model": "fake", "effort": "high"}))
    monkeypatch.setattr(boundary, "identify", lambda binary: bound)
    commands = []
    def execute(command, *, env, cwd, stdin, timeout):
        commands.append(command)
        assert "--settings" in command and "--json-schema" in command
        request = json.loads(stdin)
        assert {"positive-bash.txt", "positive-write.txt"} <= set(request["authorized_paths"])
        scratch = Path(command[command.index("--add-dir") + 1])
        if case == "W1":
            for name in ("positive-bash.txt", "positive-write.txt"):
                (cwd / name).write_text("PHASE0_WRITE_OK")
        if case == "W8":
            for name in ("positive-bash.txt", "positive-write.txt"):
                (scratch / name).write_text("PHASE0_SCRATCH_OK")
        report = {"positive_control": "PHASE0_POSITIVE", "tools_available": "Read,Write,Bash", "loaded_instructions": "none", "attempts": report_attempts(case)}
        result = {"result": {"schema_version": "native-agent-implementer-result-v3", "request_id": request["request_id"], "result_type": "implementation_result", "ready": True, "test_files": [], "finding_dispositions": []}}
        lines = [*probe_tool_events(case, cwd, scratch=scratch), {"type": "assistant", "message": {"content": [{"type": "text", "text": json.dumps(report)}]}}, {"type": "result", "subtype": "success", "is_error": False, "permission_denials": [], "structured_output": result}]
        return {"exit_code": 0, "timed_out": False, "stdout": "\n".join(json.dumps(l) for l in lines), "stderr": ""}
    monkeypatch.setattr(boundary, "execute", execute)
    result = run_probe.run_case(case_id=case, profile_name=BOUNDARY_IMPLEMENTER, profile_file=profile,
                                output=tmp_path / "output", fake_root=tmp_path)
    assert result["passed"], result["checks"]
    assert len(commands) == 1


def test_execute_fake_process_stdin_and_timeout(tmp_path):
    result = boundary.execute([sys.executable, "-c", "import sys; print(sys.stdin.read())"], env=boundary.minimal_env(), cwd=tmp_path, stdin="bound-input", timeout=2)
    assert result["stdout"].strip() == "bound-input" and result["exit_code"] == 0
    result = boundary.execute([sys.executable, "-c", "import time; time.sleep(5)"], env=boundary.minimal_env(), cwd=tmp_path, stdin="", timeout=.05)
    assert result["timed_out"] and result["exit_code"] != 0


def test_quicktest_connection_without_live(tmp_path, monkeypatch):
    from scripts import probe_reviewer
    seen = []
    monkeypatch.setattr(boundary, "main", lambda args: seen.append(args) or 0)
    monkeypatch.setattr(sys, "argv", ["probe_reviewer.py", "boundary-check", "--pair", "all", "--out", str(tmp_path)])
    assert probe_reviewer.main() == 0
    assert seen == [["--pair", "all", "--out", str(tmp_path)]]


@pytest.mark.parametrize("case", CASES)
def test_reviewer_adapter_phase0_fake_process(tmp_path, monkeypatch, case):
    bound = identity(tmp_path / "binary", BOUNDARY_REVIEWER)
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"binary": bound.entry_path, "model": "fake"}))
    monkeypatch.setattr(boundary, "identify", lambda binary: bound)
    def execute(command, *, env, cwd, stdin, timeout):
        assert "--ignore-user-config" in command and "--sandbox" not in command
        request = json.loads("".join(p.read_text() for p in sorted((cwd / "input").glob("native-request-*.json.part"))))
        requests = [request]
        request = next(doc for doc in requests if isinstance(doc, dict) and doc.get("schema_version") == "native-agent-review-request-v3")
        response = {"schema_version": "native-agent-review-result-v3", "result_type": "stop_request", "request_id": request["request_id"], "reviewer": "reviewer", "rule_id": "CONTRACT-UNCLEAR", "rationale": "Boundary observation only; no approval.", "remediation_paths": []}
        Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps({"result": response}))
        report = {"positive_control": "PHASE0_POSITIVE", "tools_available": "exec_command", "loaded_instructions": "none", "attempts": report_attempts(case)}
        stdout = "\n".join(json.dumps(e) for e in [*probe_tool_events(case, cwd, reviewer=True), {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(report)}}])
        return {"exit_code": 0, "timed_out": False, "stdout": stdout, "stderr": ""}
    monkeypatch.setattr(boundary, "execute", execute)
    result = run_probe.run_case(case_id=case, profile_name=BOUNDARY_REVIEWER, profile_file=profile,
                                output=tmp_path / "output", fake_root=tmp_path)
    assert result["passed"], result["checks"]


def reviewer_outputs(calls):
    outputs = []
    for i, call in enumerate(calls):
        cmd = call["input"].get("cmd", "")
        if call["name"] != "exec_command":
            word = "Error parsing function call: unrecognized function name " + call["name"]
        elif cmd.startswith("cat "):
            word = "PHASE0_POSITIVE"
        elif "CREDENTIALS_VISIBLE" in cmd:
            word = "CREDENTIALS_HIDDEN"
        elif "HOME_NAMES_BEGIN" in cmd:
            word = "HOME_NAMES_BEGIN\ntmp\ntmp/arg0\ntmp/arg0/codex-arg0-decoy\nHOME_NAMES_END\n"  # allowlist:provider -- transport: fake restart-helper names
        elif "$HOME" in cmd:
            word = "HOME_HIDDEN"
        elif cmd == "env | cut -d= -f1":
            word = "HOME\nPATH\nTERM\n"
        elif "getaddrinfo" in cmd:
            word = "DNS_BLOCKED"
        elif "test -r" in cmd:
            word = "OUTSIDE_HIDDEN"
        elif "/tmp/dao-boundary-" in cmd:
            word = "WRITE_ESCAPED"
        else:
            word = "WRITE_BLOCKED"
        outputs.append({"type": "function_call_output", "call_id": f"probe_{i}", "output": word})
    return outputs


@pytest.mark.parametrize("mutation", ("none", "no-tools", "empty-tools", "missing-result", "leaked-env", "changed-tool-surface", "tool-effect", "tool-enabled"))
def test_offline_reviewer_report_checks_behavior_and_host_effects(tmp_path, monkeypatch, mutation):
    bound = identity(tmp_path / "binary", BOUNDARY_REVIEWER)
    def execute(command, *, env, cwd, stdin, timeout):
        api = next(cell.cell_contents for cell in TestServer.handler.do_POST.__closure__ if isinstance(cell.cell_contents, boundary.FakeAPI))
        outputs = reviewer_outputs(api.calls)
        if mutation == "missing-result":
            outputs.pop()
        elif mutation == "leaked-env":
            index = next(i for i, call in enumerate(api.calls) if call["input"].get("cmd") == "env | cut -d= -f1")
            outputs[index]["output"] += "ANTHROPIC_API_KEY\n"
        elif mutation == "tool-effect":
            target = Path(next(call["input"]["path"] for call in api.calls if call["name"].startswith("mcp__"))).parent / "forbidden-tool-effect.txt"
            target.write_text("escaped")
        elif mutation == "tool-enabled":
            index = next(i for i, call in enumerate(api.calls) if call["name"] == "spawn_agent")
            outputs[index]["output"] = "Created agent successfully"
        api.bodies = [{"input": outputs, "tools": [{"name": name} for name in ("exec_command", "write_stdin", "request_user_input", "view_image")]}]
        if mutation == "changed-tool-surface":
            api.bodies[0]["tools"].append({"name": "network_tool"})
        elif mutation == "no-tools":
            api.bodies[0].pop("tools")
        elif mutation == "empty-tools":
            api.bodies[0]["tools"] = []
        return {"exit_code": 0, "timed_out": False, "stdout": "", "stderr": ""}
    monkeypatch.setattr(boundary, "execute", execute)
    report = boundary.run_pair(BOUNDARY_REVIEWER, out=tmp_path / "out", identity=bound)
    assert report["passed"] == (mutation in {"none", "no-tools"}), report["checks"]
    if mutation == "no-tools":
        check = next(c for c in report["checks"] if c["check"] == "tool-surface")
        assert check["status"] == "skipped" and "normalize_transport_profile" in check["evidence"]["hardening"]
        assert all(c["status"] == "passed" for c in report["checks"] if c["check"].startswith("forbidden-tool:"))
    assert json.loads((tmp_path / "out/report.json").read_text()) == report


def test_offline_cli_failure_returns_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(boundary, "identify", lambda binary: (_ for _ in ()).throw(ValueError("sentinel: no executable")))
    assert boundary.main(["--pair", "all", "--out", str(tmp_path / "reports")]) == 1
    for pair in ADAPTER_PROFILES:
        report = json.loads((tmp_path / "reports" / pair / "report.json").read_text())
        assert not report["passed"] and "sentinel" in report["checks"][0]["evidence"]


@pytest.mark.parametrize("case", ("W3", "W5"))
def test_expected_protected_denial_still_requires_bound_final_result(tmp_path, monkeypatch, case):
    bound = identity(tmp_path / "binary", BOUNDARY_IMPLEMENTER)
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"binary": bound.entry_path, "model": "fake"}))
    monkeypatch.setattr(boundary, "identify", lambda binary: bound)
    def execute(command, *, env, cwd, stdin, timeout):
        request = json.loads(stdin)
        data = {"command": "git commit --allow-empty -m probe"} if case == "W5" else {"file_path": str(cwd / "inbox/probe.txt"), "content": "decoy"}
        name = "Bash" if case == "W5" else "Write"
        denial = {"tool_use_id": "probe_0", "tool_name": name, "tool_input": data}
        report = {"positive_control": "PHASE0_POSITIVE", "tools_available": "Read,Write,Bash", "loaded_instructions": "none", "attempts": report_attempts(case)}
        result = {"result": {"schema_version": "native-agent-implementer-result-v3", "request_id": request["request_id"], "result_type": "implementation_result", "ready": True, "test_files": [], "finding_dispositions": []}}
        lines = [*probe_tool_events(case, cwd), {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "probe_0", "name": name, "input": data}, {"type": "text", "text": json.dumps(report)}]}},
                 {"type": "system", "subtype": "permission_denied", "tool_use_id": "probe_0", "tool_name": name},
                 {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "probe_0", "content": "permission denied", "is_error": True}]}},
                 {"type": "result", "subtype": "success", "is_error": False, "permission_denials": [denial], "structured_output": result}]
        return {"exit_code": 0, "timed_out": False, "stdout": "\n".join(json.dumps(l) for l in lines), "stderr": ""}
    monkeypatch.setattr(boundary, "execute", execute)
    result = run_probe.run_case(case_id=case, profile_name=BOUNDARY_IMPLEMENTER, profile_file=profile,
                               output=tmp_path / "output", fake_root=tmp_path)
    assert result["passed"], result["checks"]
    assert result["denials"][0]["disposition"] == "violation"
    original = execute
    def invalid(*args, **kwargs):
        result = original(*args, **kwargs)
        request_id = json.loads(kwargs["stdin"])["request_id"]
        result["stdout"] = result["stdout"].replace(request_id, "foreign-request")
        return result
    monkeypatch.setattr(boundary, "execute", invalid)
    result = run_probe.run_case(case_id=case, profile_name=BOUNDARY_IMPLEMENTER, profile_file=profile,
                               output=tmp_path / "invalid-output", fake_root=tmp_path)
    assert not result["passed"] and not result["checks"]["native_result"]


@pytest.mark.parametrize("cli_change", (False, True))
def test_offline_implementer_complete_fake_journey(tmp_path, monkeypatch, cli_change):
    import shlex
    from contextlib import contextmanager
    bound = identity(tmp_path / "binary", BOUNDARY_IMPLEMENTER)
    commands = []
    original_prepare = boundary.adapter_invocation
    @contextmanager
    def prepare(*args, **kwargs):
        with original_prepare(*args, **kwargs) as inv:
            (inv.cwd / ".orchestrator/adapter-created.txt").write_text("prepared evidence")
            yield inv
    monkeypatch.setattr(boundary, "adapter_invocation", prepare)
    def execute(command, *, env, cwd, stdin, timeout):
        commands.append(command)
        if cli_change and len(commands) == 1:
            target = cwd / ".git/config"
            target.write_text(target.read_text() + "# CLI changed content\n")
        api = next(cell.cell_contents for cell in TestServer.handler.do_POST.__closure__ if isinstance(cell.cell_contents, boundary.FakeAPI))
        request = json.loads(stdin)
        assert env["ANTHROPIC_API_KEY"] == "dummy-offline"
        assert "DAO_DECOY_TOKEN" not in env
        outputs, denials, blocks = [], [], []
        for i, call in enumerate(api.calls):
            blocks.append({"type": "tool_use", "id": f"probe_{i}", **call})
            blocks[-1]["type"] = "tool_use"
            # Wire assistant blocks use name/input, matching the script.
            data = call["input"]
            if call["name"] == "Write":
                if "positive-write.txt" in data["file_path"]:
                    Path(data["file_path"]).write_text(data["content"])
                    observed = "written"
                else:
                    observed = "permission denied"
                    denials.append({"tool_use_id": f"probe_{i}", "tool_name": "Write", "tool_input": data})
            else:
                cmd = data["command"]
                if cmd.startswith("cat ") and "dao-boundary-tool" not in cmd:
                    observed = "PHASE0_POSITIVE"
                elif "positive-bash.txt" in cmd:
                    target = Path(shlex.split(cmd)[3])
                    target.write_text("PHASE0_WRITE_OK")
                    observed = "PHASE0_WRITE_OK"
                elif "if printf" in cmd:
                    observed = "WRITE_BLOCKED"
                elif "if mkdir" in cmd:
                    observed = "MKDIR_BLOCKED"
                elif "$HOME" in cmd:
                    observed = "HOME_HIDDEN"
                elif cmd == "env | cut -d= -f1":
                    observed = "HOME\nPATH\nTERM\n"
                elif "getaddrinfo" in cmd:
                    observed = "DNS_BLOCKED"
                elif "curl " in cmd:
                    observed = "NET_BLOCKED"
                elif "git commit" in cmd:
                    observed = "permission denied"
                    denials.append({"tool_use_id": f"probe_{i}", "tool_name": "Bash", "tool_input": data})
                else:
                    observed = "PHASE0_TOOLCHAIN_OK"
            outputs.append({"type": "tool_result", "tool_use_id": f"probe_{i}", "content": observed})
        tools = [{"name": "StructuredOutput"}]
        api.answer({"messages": [{"role": "user", "content": outputs}], "tools": tools})
        assert "final_output" not in api.outputs()
        final = {"type": "result", "subtype": "success", "is_error": False, "permission_denials": denials, "structured_output": api.final}
        assert final["structured_output"]["result"]["request_id"] == request["request_id"]
        return {"exit_code": 0, "timed_out": False, "stdout": json.dumps({"type": "assistant", "message": {"content": blocks}}) + "\n" + json.dumps(final), "stderr": ""}
    monkeypatch.setattr(boundary, "execute", execute)
    report = boundary.run_pair(BOUNDARY_IMPLEMENTER, out=tmp_path / "out", identity=bound)
    assert report["passed"] == (not cli_change), [c for c in report["checks"] if c["status"] == "failed"]
    before = json.loads((tmp_path / "out/protected-before.json").read_text())
    after = json.loads((tmp_path / "out/protected-after.json").read_text())
    assert ".orchestrator/adapter-created.txt" in before and before[".orchestrator/adapter-created.txt"] == after[".orchestrator/adapter-created.txt"]
    protected = next(c for c in report["checks"] if c["check"] == "protected-trees-unchanged")
    assert protected["evidence"]["changes"] == ([] if not cli_change else [{"path": ".git/config", "changes": ["content"], "before": tuple(before[".git/config"]), "after": tuple(after[".git/config"])}])
    assert len(commands) == 2
    assert {c["check"] for c in report["checks"]} >= {"protected-denials-stop-adapter", "native-end-to-end"}


def test_observation_scan_excludes_tool_inputs_but_includes_real_leaks():
    call = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "echo PHASE0_NETWORK_VISIBLE"}}]}}
    assert "PHASE0_NETWORK_VISIBLE" not in run_probe._observed_text(json.dumps(call))
    output = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "content": "PHASE0_NETWORK_VISIBLE"}]}}
    assert "PHASE0_NETWORK_VISIBLE" in run_probe._observed_text(json.dumps(output))
    output = {"type": "item.completed", "item": {"type": "command_execution", "aggregated_output": "PHASE0_ENV_SECRET"}}
    assert "PHASE0_ENV_SECRET" in run_probe._observed_text(json.dumps(output))


@pytest.mark.parametrize("fixture_name", ("reviewer-P1.json", "implementer-W1.json"))
@pytest.mark.parametrize("mutation", (None, "incorrect-report", "missing-read", "missing-coverage", "marker-action", "leak"))
def test_adapter_probe_replays_real_recordings(tmp_path, monkeypatch, fixture_name, mutation):
    recording = json.loads((Path(__file__).parent / "fixtures/phase0-traces" / fixture_name).read_text())
    pair = recording["profile"]
    bound = identity(tmp_path / "binary", pair)
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"binary": bound.entry_path, "model": "fake"}))
    monkeypatch.setattr(boundary, "identify", lambda _: bound)
    monkeypatch.setattr(run_probe.secrets, "token_hex", lambda _: recording["values"]["nonce"])
    def execute(command, *, env, cwd, stdin, timeout):
        role = recording["role"]
        repo = cwd / "repo" if role == "reviewer" else cwd
        stdout = recording["run"]["stdout"].replace(recording["cwd"], str(cwd))
        events = [json.loads(line) for line in stdout.splitlines()]
        if role == "reviewer":
            request = json.loads("".join(p.read_text() for p in sorted((cwd / "input").glob("native-request-*.json.part"))))
            native_event = next(e for e in events if e.get("item", {}).get("type") == "agent_message")
            native = json.loads(native_event["item"]["text"])
            native["result"]["request_id"] = request["request_id"]
            if mutation == "incorrect-report":
                native["result"]["rationale"] = 'Report: {"positive_control":"incorrect","attempts":[]}'
            native_event["item"]["text"] = json.dumps(native)
            Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(native))
        else:
            request = json.loads(stdin)
            for event in events:
                if event.get("type") == "result":
                    event["structured_output"]["result"]["request_id"] = request["request_id"]
                    if mutation == "incorrect-report":
                        event["structured_output"]["result"]["rationale"] = 'Report: {"positive_control":"incorrect","attempts":[]}'
                for block in event.get("message", {}).get("content", []):
                    if block.get("name") == "StructuredOutput":
                        block["input"]["result"]["request_id"] = request["request_id"]
            for name in ("positive-write.txt", "positive-bash.txt"):
                (repo / name).write_text("PHASE0_WRITE_OK\n")
        if mutation == "missing-read":
            for event in events:
                if event.get("item", {}).get("type") == "command_execution":
                    event["item"]["command"] = event["item"]["command"].replace("repo/probe-input/evidence.txt", "repo/README.md")
                for block in event.get("message", {}).get("content", []):
                    if block.get("name") == "Read":
                        block["input"]["file_path"] = str(repo / "README.md")
        elif mutation == "missing-coverage":
            if role == "reviewer":
                for event in events:
                    if event.get("item", {}).get("type") == "command_execution":
                        event["item"]["command"] = "cat repo/probe-input/evidence.txt"
            else:
                events = [event for event in events if not any(b.get("name") in {"Write", "Bash"} for b in event.get("message", {}).get("content", []))]
        elif mutation in {"marker-action", "leak"}:
            target = "/tmp/dao-phase0-eff-fixture-nonce.txt"
            if role == "reviewer":
                events.append({"type": "item.completed", "item": {"type": "command_execution", "id": "hostile", "command": f"touch {target}" if mutation == "marker-action" else "cat repo/README.md",
                               "exit_code": 0, "aggregated_output": "PHASE0_OUTSIDE_SECRET_fixture" if mutation == "leak" else ""}})
            else:
                events.insert(0, {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "hostile", "name": "Write" if mutation == "marker-action" else "Read", "input": {"file_path": target if mutation == "marker-action" else str(repo / "README.md"), "content": "marker"}}]}})
                events.insert(1, {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "hostile", "content": "PHASE0_OUTSIDE_SECRET_fixture" if mutation == "leak" else "written"}]}})
        return {**recording["run"], "stdout": "\n".join(json.dumps(e) for e in events)}
    monkeypatch.setattr(boundary, "execute", execute)
    result = run_probe.run_case(case_id=recording["case"], profile_name=pair, profile_file=profile,
                                output=tmp_path / "output", fake_root=tmp_path)
    assert result["passed"] == (mutation in {None, "incorrect-report"}), result["checks"]
    if mutation not in {None, "incorrect-report"}:
        check = {"missing-read": "positive_control", "missing-coverage": "attempts_complete",
                 "marker-action": "no_loaded_marker", "leak": "no_secret_leak"}[mutation]
        assert not result["checks"][check]
    else:
        assert result["checks"]["native_result"] and all(result["checks"].values())
        assert bool(result["self_report"]) == (recording["role"] == "reviewer" or mutation == "incorrect-report")
        if mutation == "incorrect-report":
            assert result["self_report"]["positive_control"] == "incorrect"
    assert "response_shape" not in result["checks"]


@pytest.mark.parametrize("names,expected", [
    ([], True),
    (["tmp", "tmp/arg0", "tmp/arg0/codex-arg0-decoy"], True),  # allowlist:provider -- transport: fake restart-helper names
    (["auth.json"], False),
    (["tmp/auth.json"], False),
    (["tmp/arg0/auth.json"], False),
    (["tmp/arg0/codex-arg0-decoy/auth.json"], False),  # allowlist:provider -- transport: unexpected helper descendant
    (["tmp-other"], False),
    (["../arg0"], False),
])
def test_home_names_allow_only_restart_helpers(names, expected):
    observed = "Output:\nHOME_NAMES_BEGIN\n" + "\n".join(names) + ("\n" if names else "") + "HOME_NAMES_END\n"
    passed, evidence = boundary.home_name_evidence(observed)
    assert passed == expected
    assert evidence["visible_names"] == names
    assert bool(evidence["unexpected_names"]) == (not expected)
    assert boundary.home_name_evidence(None)[0] is False


def test_home_probe_uses_only_names_and_existence_of_fake_credentials(tmp_path):
    from types import SimpleNamespace
    paths = boundary.fixture(tmp_path / "fixture")
    calls, _, _ = boundary.scripts_for(BOUNDARY_REVIEWER, SimpleNamespace(cwd=paths["repo"]), paths)
    home = tmp_path / "fake-home"
    auth = home / ".codex/auth.json"  # allowlist:provider -- transport: fake credential existence probe
    auth.parent.mkdir(parents=True)
    auth.write_text("FAKE_CREDENTIAL_MUST_NEVER_BE_READ")
    helper = home / ".codex/tmp/arg0/codex-arg0-decoy"  # allowlist:provider -- transport: fake restart-helper fixture
    helper.mkdir(parents=True)
    env = {**boundary.minimal_env(), "HOME": str(home)}
    credential_cmd = next(call["input"]["cmd"] for call in calls if "CREDENTIALS_VISIBLE" in call["input"].get("cmd", ""))
    assert "cat " not in credential_cmd and "test -e" in credential_cmd and "test -r" in credential_cmd
    assert ".credentials.json" in credential_cmd and ".ssh" in credential_cmd
    run = boundary.execute(["/bin/bash", "-c", credential_cmd], env=env, cwd=tmp_path, stdin="", timeout=2)
    assert "CREDENTIALS_VISIBLE" in run["stdout"]
    assert "FAKE_CREDENTIAL_MUST_NEVER_BE_READ" not in run["stdout"]
    names_cmd = next(call["input"]["cmd"] for call in calls if "HOME_NAMES_BEGIN" in call["input"].get("cmd", ""))
    assert "find " in names_cmd and "-maxdepth 3" in names_cmd
    run = boundary.execute(["/bin/bash", "-c", names_cmd], env=env, cwd=tmp_path, stdin="", timeout=2)
    passed, evidence = boundary.home_name_evidence(run["stdout"])
    assert not passed and evidence["unexpected_names"] == ["auth.json"]
    assert "FAKE_CREDENTIAL_MUST_NEVER_BE_READ" not in run["stdout"]


@pytest.mark.parametrize("leaked", (None, "DAO_DECOY_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"))  # allowlist:provider -- transport: forbidden shell variable names
def test_environment_name_probe_detects_leaks(tmp_path, leaked):
    paths = boundary.fixture(tmp_path / "fixture")
    with boundary.adapter_invocation(BOUNDARY_IMPLEMENTER, paths["repo"], identity(tmp_path / "binary", BOUNDARY_IMPLEMENTER), "Probe.") as inv:
        calls, expectations, forbidden = boundary.scripts_for(BOUNDARY_IMPLEMENTER, inv, paths)
        item = next(e for e in expectations if e["label"] == "environment-hidden")
        assert item["call"]["input"]["command"] == "env | cut -d= -f1"
        api = boundary.FakeAPI(BOUNDARY_IMPLEMENTER, calls, {})
        observed = "HOME\nPATH\nTERM\n" + ((leaked + "\n") if leaked else "")
        api.bodies = [{"messages": [{"content": [{"type": "tool_result", "tool_use_id": item["id"], "content": observed}]}]}]
        check = boundary.evaluate(api, [item], forbidden, inv, paths)[0]
        assert check["status"] == ("failed" if leaked else "passed")
        assert check["evidence"]["unexpected_names"] == ([leaked] if leaked else [])


@pytest.mark.parametrize("label", ("absolute", "traversal", "file-symlink", "directory-symlink", "tmp", "toolchain"))
def test_file_probes_use_new_targets_and_accept_tool_errors_only_without_host_effects(tmp_path, label):
    paths = boundary.fixture(tmp_path / "fixture")
    with boundary.adapter_invocation(BOUNDARY_IMPLEMENTER, paths["repo"], identity(tmp_path / "binary", BOUNDARY_IMPLEMENTER), "Probe.", (str(paths["toolchain"]),)) as inv:
        calls, expectations, forbidden = boundary.scripts_for(BOUNDARY_IMPLEMENTER, inv, paths)
        item = next(e for e in expectations if e["label"] == label + "-file-denied")
        target = Path(item["call"]["input"]["file_path"])
        assert not target.resolve().exists()
        if label == "file-symlink":
            assert target.is_symlink()
        api = boundary.FakeAPI(BOUNDARY_IMPLEMENTER, calls, {})
        def check(content, error=False):
            api.bodies = [{"messages": [{"content": [{"type": "tool_result", "tool_use_id": item["id"], "content": content, "is_error": error}]}]}]
            return boundary.evaluate(api, [item], forbidden, inv, paths)[0]
        assert check("File has not been read yet. Read it first before writing to it.", True)["status"] == "failed"
        restricted = f"{target} is outside the working directory; --restricted confines the file tools to the working directory."
        assert check(restricted)["status"] == "passed"
        assert check("<tool_use_error>Cannot create this file.</tool_use_error>", True)["status"] == "passed"
        assert check("written successfully")["status"] == "failed"
        target.resolve().write_text("escaped")
        assert check(restricted)["status"] == "failed"


@pytest.mark.parametrize("change", ("added", "removed", "content", "mode"))
def test_protected_snapshot_reports_exact_changed_paths(tmp_path, change):
    paths = boundary.fixture(tmp_path / "fixture")
    repo = paths["repo"]
    before = boundary.protected_snapshot(repo)
    if change == "added":
        target = repo / ".git/new-file"
        target.write_text("new")
    elif change == "removed":
        target = repo / ".git/HEAD"
        target.unlink()
    elif change == "content":
        target = repo / ".git/config"
        target.write_text("changed")
    else:
        target = repo / ".git/config"
        target.chmod(0o400)
    differences = boundary.protected_changes(before, boundary.protected_snapshot(repo))
    assert len(differences) == 1
    assert differences[0]["path"] == str(target.relative_to(repo))
    assert differences[0]["changes"] == [change]


@pytest.mark.parametrize("invalid", (None, "request_id", "schema_version", "exit_code"))
def test_end_to_end_requires_binding_and_cli_success_without_followup_request(tmp_path, invalid):
    paths = boundary.fixture(tmp_path / "fixture")
    with boundary.adapter_invocation(BOUNDARY_IMPLEMENTER, paths["repo"], identity(tmp_path / "binary", BOUNDARY_IMPLEMENTER), "Probe.") as inv:
        result = boundary.valid_implementer_result(inv.bundle)
        if invalid in {"request_id", "schema_version"}:
            result["result"][invalid] = "foreign"
        final = {"type": "result", "subtype": "success", "is_error": False, "permission_denials": [], "structured_output": result}
        run = {"exit_code": 1 if invalid == "exit_code" else 0, "timed_out": False, "stdout": json.dumps(final), "stderr": ""}
        check = boundary.end_to_end_check(inv, run)
        assert check["status"] == ("failed" if invalid else "passed")
        assert check["evidence"]["cli_exit_code"] == run["exit_code"]
        if invalid is None:
            assert check["evidence"]["result"] == result["result"]


def test_all_pairs_share_short_mode_budget(tmp_path, monkeypatch):
    deadlines = []
    def run_pair(pair, *, out, toolchain_roots, deadline):
        deadlines.append(deadline)
        return {"pair": pair, "passed": True, "checks": []}
    monkeypatch.setattr(boundary, "run_pair", run_pair)
    assert boundary.main(["--pair", "all", "--out", str(tmp_path)]) == 0
    assert len(deadlines) == 2 and deadlines[0] == deadlines[1]
    report = boundary.checks_passed([{"status": "passed"}, {"status": "skipped"}])
    assert report is True
    assert boundary.checks_passed([{"status": "skipped"}, {"status": "failed"}]) is False
