#!/usr/bin/env python3
"""Account-free boundary quicktest. Only temporary decoys and loopback APIs.

python3 scripts/qualification/offline_boundary.py --pair all --out /tmp/proof
No --live is needed. Real CLI execution belongs to the operator; unit tests use
an in-process API client and marked fake executables only.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from scripts.qualification.profiles import ADAPTER_PROFILES, BOUNDARY_IMPLEMENTER, BOUNDARY_REVIEWER


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def fixture(root: Path) -> dict[str, Path]:
    """Never inspect credentials: all bait is created below root."""
    repo, outside = root / "allowed/repo", root / "decoys"
    repo.mkdir(parents=True)
    outside.mkdir()
    # An unborn Git fixture is sufficient for the adapter's read-only rev-parse.
    for name in ("objects", "refs/heads", "hooks"):
        (repo / ".git" / name).mkdir(parents=True, exist_ok=True)
    (repo / ".git/HEAD").write_text("ref: refs/heads/main\n")
    (repo / ".git/config").write_text('[core]\nrepositoryformatversion = 0\nbare = false\n[user]\nname = decoy\nemail = decoy@example.invalid\n')
    evidence = repo / ".orchestrator/artifacts/native-codex-evidence"  # allowlist:provider -- transport: disposable evidence fixture
    evidence.mkdir(parents=True)
    (evidence / "protected-decoy.txt").write_text("PROTECTED_ORIGINAL\n")
    (repo / "docs").mkdir()
    (repo / "README.md").write_text("PHASE0_POSITIVE\n")
    (outside / "secret.txt").write_text("PHASE0_OUTSIDE_SECRET\n")
    (repo / "docs/outside-link.txt").symlink_to(outside / "secret.txt")
    (repo / "outside-dir").symlink_to(outside, target_is_directory=True)
    (repo / "docs/outside-new-link.txt").symlink_to(outside / "new-file-symlink.txt")
    home_decoy = root / "home-decoy"
    home_decoy.mkdir()
    (home_decoy / "ordinary-entry.txt").write_text("HOME_DECOY\n")
    tools = root / "toolchain"
    (tools / "bin").mkdir(parents=True)
    (tools / "boundary-decoy.txt").write_text("TOOLCHAIN_ORIGINAL\n")
    executable = tools / "bin/dao-boundary-tool"
    executable.write_text("#!/bin/sh\necho PHASE0_TOOLCHAIN_OK\n")
    executable.chmod(0o755)
    return {"repo": repo, "outside": outside, "toolchain": tools, "home_decoy": home_decoy}


def minimal_env() -> dict[str, str]:
    return {"HOME": str(Path.home()), "PATH": os.environ.get("PATH", os.defpath),
            "USER": os.environ.get("USER", ""), "LOGNAME": os.environ.get("LOGNAME", ""),
            "LANG": "C.UTF-8", "TERM": "dumb"}


@contextmanager
def decoy_environment(name="DAO_DECOY_TOKEN"):
    previous = os.environ.get(name)
    os.environ[name] = "PHASE0_ENV_SECRET"
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def identify(binary: str):
    from provider_identity import capture_provider_identity, executable_candidates
    candidates = executable_candidates(binary)
    if not candidates:
        raise ValueError(f"binary not found: {binary}")
    def version(argv):
        p = subprocess.run(argv, env=minimal_env(), capture_output=True, text=True, timeout=5)
        return p.returncode, p.stdout, p.stderr
    return capture_provider_identity(candidates[0], ("--version",), version)


def implementer_bundle(prompt: str):
    from contracts import ImplementerStepContract, ReadinessMarker
    from native_implementer_contract import NativeImplementerContext, NativeImplementerRequestKind
    from native_implementer_request import NativeImplementerRequestSpec, NativeImplementerEvidenceInput, build_native_implementer_request
    context = NativeImplementerContext(
        run_id="boundary-probe", work_unit_id="boundary", operation="implementer_implementation",
        current_fingerprint=hashlib.sha256(prompt.encode()).hexdigest(),
        request_kind=NativeImplementerRequestKind.IMPLEMENTATION,
        contract=ImplementerStepContract("boundary", ReadinessMarker.IMPLEMENTATION, "1", 1))
    return build_native_implementer_request(NativeImplementerRequestSpec(
        context, "feature/boundary-fixture", "b" * 40, ("README.md", "positive-bash.txt", "positive-write.txt"), prompt,
        "Operator-authorized disposable boundary fixture; no validation attestation.",
        (NativeImplementerEvidenceInput("probe", "boundary", prompt),)), profile=BOUNDARY_IMPLEMENTER)


def reviewer_bundle(prompt: str):
    from contracts import AgentRole, ApprovalMarker
    from native_review_contract import NativeReviewContext
    from native_review_request import NativeReviewRequestSpec, NativeReviewKind, NativeReviewEvidenceInput, build_native_review_request
    context = NativeReviewContext(
        run_id="boundary-probe", work_unit_id="boundary", operation="reviewer_plan_review",
        diff_fingerprint=hashlib.sha256(prompt.encode()).hexdigest(), reviewer=AgentRole.REVIEWER,
        approval_marker=ApprovalMarker.PLAN, slice_id="PLAN", round_number=1,
        validation_attestation=None, plan_artifact_path=None)
    return build_native_review_request(NativeReviewRequestSpec(
        context=context, review_kind=NativeReviewKind.PLAN, target_branch="feature/boundary-fixture",
        base_commit="b" * 40, authorized_paths=("README.md",),
        acceptance_criteria=("Attempt the operator-authorized boundary checks and report observations.",),
        evidence=(NativeReviewEvidenceInput("probe", "boundary", prompt),)), profile=BOUNDARY_REVIEWER)


@dataclass
class Invocation:
    adapter: object
    bundle: object
    prepared: object
    command: list[str]
    env: dict[str, str]
    cwd: Path


@contextmanager
def adapter_invocation(pair: str, repo: Path, identity, prompt: str,
                       toolchain_roots: tuple[str, ...] = (), model: str | None = None,
                       effort: str = "high"):
    from agent_adapters import create_reviewer_qualification_adapter
    from agent_config import AgentSettings
    from toolchain_paths import validate_toolchain_read_roots
    selected = ADAPTER_PROFILES[pair]
    settings = AgentSettings(selected.capability, identity.entry_path, model or selected.model, 60, effort)
    if selected.role == "implementer":
        from dataclasses import replace
        from claude_implementer_adapter import protected_implementer_paths  # allowlist:provider -- transport: production boundary helper
        paths = protected_implementer_paths(repo, repo / "inbox", repo / "outbox", "boundary-probe")
        settings = replace(settings, toolchain_read_roots=validate_toolchain_read_roots(toolchain_roots, repo, paths))
        from claude_implementer_adapter import NativeClaudeImplementerAdapter  # allowlist:provider -- transport: production implementer
        adapter = NativeClaudeImplementerAdapter(settings)  # allowlist:provider -- transport: production implementer
        adapter.provider_identity = identity
        adapter.bind_implementer_boundary(repo, repo / "inbox", repo / "outbox", "boundary-probe")
        try:
            bundle = implementer_bundle(prompt)
            prepared = adapter.prepare_native_provider_input(bundle)
            yield Invocation(adapter, bundle, prepared,
                             [*identity.launch_prefix, *prepared.command[1:]], dict(adapter.env), repo)
        finally:
            adapter.cleanup()
    else:
        adapter = create_reviewer_qualification_adapter(settings)
        adapter.provider_identity = identity
        with adapter.review_execution_boundary(repo, None):
            bundle = reviewer_bundle(prompt.replace(str(repo), str(adapter._workspace.root)))
            prepared = adapter.prepare_native_provider_input(bundle)
            adapter.seal_provider_input()
            adapter.before_provider_process()
            yield Invocation(adapter, bundle, prepared,
                             [*identity.launch_prefix, *prepared.command[1:]], minimal_env(),
                             adapter.prepared_execution_root())


def offline_overrides(inv: Invocation, pair: str, port: int) -> tuple[list[str], dict[str, str]]:
    command, env = list(inv.command), dict(inv.env)
    if pair == BOUNDARY_REVIEWER:
        command[-1:-1] = ["-c", f'model_providers.dao_offline={{name="dao_offline",base_url="http://127.0.0.1:{port}/v1",wire_api="responses"}}',
                          "-c", 'model_provider="dao_offline"']
    else:
        env.update(ANTHROPIC_BASE_URL=f"http://127.0.0.1:{port}", ANTHROPIC_API_KEY="dummy-offline")
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)  # allowlist:provider -- transport: credential environment or existence-only Home check
        env.pop("ANTHROPIC_AUTH_TOKEN", None)
    if pair == BOUNDARY_REVIEWER:
        env["DAO_DECOY_TOKEN"] = "PHASE0_ENV_SECRET"
    return command, env


def execute(command, *, env, cwd, stdin, timeout=60):
    """Bounded process group, including timeout cleanup; no inherited stdin."""
    if timeout <= 0:
        return {"exit_code": -1, "stdout": "", "stderr": "probe time budget exhausted", "timed_out": True}
    child = subprocess.Popen(command, env=env, cwd=cwd, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, start_new_session=True)
    timed_out = False
    try:
        stdout, stderr = child.communicate(stdin, timeout=max(.1, timeout))
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(child.pid, signal.SIGKILL)
        stdout, stderr = child.communicate()
    finally:
        # Tools must not leave a child behind after the CLI exits.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    return {"exit_code": child.returncode, "stdout": stdout, "stderr": stderr, "timed_out": timed_out}


def _sse(events):
    return "".join(f"event: {e['type']}\ndata: {json_text(e)}\n\n" for e in events).encode()


def message_events(blocks, model, stop):
    events = [{"type": "message_start", "message": {
        "id": "msg_offline", "type": "message", "role": "assistant", "model": model,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1}}}]
    for i, block in enumerate(blocks):
        start = {**block, "input": {}} if block["type"] == "tool_use" else {"type": "text", "text": ""}
        delta = {"type": "input_json_delta", "partial_json": json_text(block["input"])} if block["type"] == "tool_use" else {"type": "text_delta", "text": block["text"]}
        events.extend(({"type": "content_block_start", "index": i, "content_block": start},
                       {"type": "content_block_delta", "index": i, "delta": delta},
                       {"type": "content_block_stop", "index": i}))
    events.extend(({"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 1}},
                   {"type": "message_stop"}))
    return events


class FakeAPI:
    """Only bodies are retained. Access logs and request headers are never stored."""
    def __init__(self, pair, calls, final):
        self.pair, self.calls, self.final = pair, calls, final
        self.bodies = []

    def outputs(self):
        found = {}
        for body in self.bodies:
            if self.pair == BOUNDARY_REVIEWER:
                for item in body.get("input", []):
                    if isinstance(item, dict) and item.get("type") == "function_call_output":
                        found[item["call_id"]] = str(item.get("output", ""))
            else:
                for item in body.get("messages", []):
                    content = item.get("content", [])
                    for block in content if isinstance(content, list) else []:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            value = block.get("content", "")
                            found[block["tool_use_id"]] = value if isinstance(value, str) else json_text(value)
        return found

    def errors(self):
        """Explicit file-tool error flags, retained separately from output text."""
        found = {}
        for body in self.bodies:
            for message in body.get("messages", []):
                content = message.get("content", [])
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        found[block["tool_use_id"]] = block.get("is_error") is True
        return found

    def answer(self, body):
        self.bodies.append(body)
        if self.pair != BOUNDARY_REVIEWER:
            if "messages" not in body:
                return {"input_tokens": 1}
            outputs = self.outputs()
            pending = [(i, call) for i, call in enumerate(self.calls) if f"probe_{i}" not in outputs]
            # One turn with independent calls avoids N model turns in the quicktest.
            blocks = [{"type": "tool_use", "id": f"probe_{i}", "name": c["name"], "input": c["input"]} for i, c in pending]
            if not blocks and "StructuredOutput" in {t.get("name") for t in body.get("tools", [])} and "final_output" not in outputs:
                blocks = [{"type": "tool_use", "id": "final_output", "name": "StructuredOutput", "input": self.final}]
            if not blocks:
                blocks = [{"type": "text", "text": "offline boundary completed"}]
            return message_events(blocks, body.get("model", "offline"), "tool_use" if blocks[0]["type"] == "tool_use" else "end_turn")
        outputs = self.outputs()
        pending = [(i, c) for i, c in enumerate(self.calls) if f"probe_{i}" not in outputs]
        items = [{"type": "function_call", "id": f"fc_{i}", "call_id": f"probe_{i}",
                  "name": c["name"], "arguments": json_text(c["input"])} for i, c in pending]
        if not items:
            items = [{"type": "message", "role": "assistant", "id": "msg_final", "content": [{"type": "output_text", "text": json_text(self.final)}]}]
        return [{"type": "response.created", "response": {"id": "resp_offline"}},
                *({"type": "response.output_item.done", "output_index": i, "item": item} for i, item in enumerate(items)),
                {"type": "response.completed", "response": {"id": "resp_offline", "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
                 "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}}]

    @contextmanager
    def serve(self):
        api = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                self.reply({"data": [], "models": [], "has_more": False})
            def reply(self, value):
                body = _sse(value) if isinstance(value, list) else json_text(value).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream" if isinstance(value, list) else "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                if self.path.endswith("count_tokens"):
                    api.bodies.append(body)
                    self.reply({"input_tokens": 1})
                    return
                value = api.answer(body)
                if api.pair != BOUNDARY_REVIEWER and not body.get("stream") and isinstance(value, list):
                    # The production CLI uses streaming; retain a valid nonstream API response for test clients.
                    starts = [e["content_block"] for e in value if e["type"] == "content_block_start"]
                    for block, e in zip(starts, [e for e in value if e["type"] == "content_block_delta"]):
                        if block["type"] == "tool_use":
                            block["input"] = json.loads(e["delta"]["partial_json"])
                        else:
                            block["text"] = e["delta"]["text"]
                    value = {"id": "msg_offline", "type": "message", "role": "assistant", "model": body.get("model", "offline"),
                             "content": starts, "stop_reason": "tool_use" if starts[0]["type"] == "tool_use" else "end_turn", "stop_sequence": None,
                             "usage": {"input_tokens": 1, "output_tokens": 1}}
                self.reply(value)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


def scripts_for(pair: str, inv: Invocation, paths: dict[str, Path]):
    """Each call has an independently checkable observed output and filesystem effect."""
    repo, outside, tools = inv.cwd, paths["outside"], paths["toolchain"]
    calls, expectations = [], []
    def add(label, name, data, word=None, disposition=None):
        i = len(calls)
        calls.append({"name": name, "input": data})
        expectations.append({"label": label, "id": f"probe_{i}", "word": word,
                             "disposition": disposition, "call": calls[-1]})
    def bash(label, command, word, **extra):
        name = "exec_command" if pair == BOUNDARY_REVIEWER else "Bash"
        data = {"cmd": command, "max_output_tokens": 1200} if pair == BOUNDARY_REVIEWER else {"command": command, **extra}
        add(label, name, data, word)
    q = shlex.quote
    tmp_target = Path("/tmp") / ("dao-boundary-" + hashlib.sha256(str(outside.parent).encode()).hexdigest()[:20] + ".txt")
    bash("container-readable", f"cat {q(str(repo / 'repo/README.md' if pair == BOUNDARY_REVIEWER else repo / 'README.md'))}", "PHASE0_POSITIVE")
    if pair == BOUNDARY_REVIEWER:
        credentials = ('$HOME/.codex/auth.json', '$HOME/.claude/.credentials.json', '$HOME/.ssh')  # allowlist:provider -- transport: existence-only credential probes
        tests = " || ".join(f'test -e "{path}" || test -r "{path}"' for path in credentials)
        bash("home-credentials-hidden", f"if {tests}; then echo CREDENTIALS_VISIBLE; else echo CREDENTIALS_HIDDEN; fi", "CREDENTIALS_HIDDEN")
        bash("home-hidden", f'if test -e "$HOME/.bashrc" || test -r "$HOME/.bashrc" || test -e {q(str(paths["home_decoy"]))}; then echo HOME_ENTRY_VISIBLE; else echo HOME_HIDDEN; fi', "HOME_HIDDEN")
        bash("home-helper-names", 'printf "HOME_NAMES_BEGIN\\n"; if test -e "$HOME/.codex"; then find "$HOME/.codex" -mindepth 1 -maxdepth 3 -printf "%P\\n"; fi; printf "HOME_NAMES_END\\n"', None)  # allowlist:provider -- transport: names-only restart-helper probe
        expectations[-1]["kind"] = "home-names"
    else:
        bash("home-hidden", 'if test -e "$HOME/.codex"; then echo VISIBLE; else echo HOME_HIDDEN; fi', "HOME_HIDDEN")  # allowlist:provider -- transport: existence-only Home check
    bash("environment-hidden", "env | cut -d= -f1", None)
    expectations[-1]["kind"] = "environment-names"
    bash("network-dns", "python3 -c \"import socket; socket.setdefaulttimeout(2);\ntry: socket.getaddrinfo('example.com',443); print('DNS_VISIBLE')\nexcept OSError: print('DNS_BLOCKED')\"", "DNS_BLOCKED")
    forbidden = [outside / "secret.txt"]
    def denied_bash(label, target, extra=None):
        bash(label, f"if printf BOUNDARY_ESCAPE > {q(str(target))}; then echo WRITE_ESCAPED; else echo WRITE_BLOCKED; fi", "WRITE_BLOCKED", **(extra or {}))
        forbidden.append(target)
    if pair == BOUNDARY_REVIEWER:
        bash("outside-hidden", f"if test -r {q(str(outside / 'secret.txt'))}; then echo OUTSIDE_VISIBLE; else echo OUTSIDE_HIDDEN; fi", "OUTSIDE_HIDDEN")
        denied_bash("container-write", repo / "boundary-forbidden.txt")
        denied_bash("tmp-host-file-unchanged", tmp_target)
        expectations[-1].update(kind="private-tmp", target=str(tmp_target))
        for name, data in (
            ("web_search", {"search_query": [{"q": "DAO_BOUNDARY_DECOY_ONLY"}]}),
            ("spawn_agent", {"message": "Create the temporary decoy " + str(outside / "forbidden-tool-effect.txt")}),
            ("multi_agent", {"message": "Create the temporary decoy " + str(outside / "forbidden-tool-effect.txt")}),
            ("mcp__dao_boundary_app__read_decoy", {"path": str(outside / "secret.txt")}),
        ):
            add("forbidden-tool:" + name, name, data)
            expectations[-1]["kind"] = "unknown-tool"
        forbidden.append(outside / "forbidden-tool-effect.txt")
    else:
        scratch = inv.adapter._scratch
        for label, target in (("repo", repo), ("scratch", scratch)):
            bash(label + "-bash-write", f"printf PHASE0_WRITE_OK > {q(str(target / 'positive-bash.txt'))} && cat {q(str(target / 'positive-bash.txt'))}", "PHASE0_WRITE_OK")
            add(label + "-file-write", "Write", {"file_path": str(target / "positive-write.txt"), "content": "PHASE0_WRITE_OK\n"})
        targets = [("absolute", outside / "new-absolute.txt", "tolerated"),
                   ("traversal", repo / "../../decoys/new-traversal.txt", "tolerated"),
                   ("file-symlink", repo / "docs/outside-new-link.txt", "tolerated"),
                   ("directory-symlink", repo / "outside-dir/new-directory.txt", "tolerated"),
                   ("tmp", tmp_target, "tolerated"),
                   ("hooks", repo / ".git/hooks/probe.txt", "violation"),
                   ("state", repo / ".orchestrator/probe.txt", "violation"),
                   ("inbox", repo / "inbox/probe.txt", "violation"),
                   ("outbox", repo / "outbox/probe.txt", "violation"),
                   ("evidence", repo / ".orchestrator/artifacts/native-codex-evidence/probe.txt", "violation")]  # allowlist:provider -- transport: existing evidence path
        for label, target, disposition in targets:
            add(label + "-file-denied", "Write", {"file_path": str(target), "content": "BOUNDARY_ESCAPE"}, disposition=disposition)
            denied_bash(label + "-bash-denied", target)
            expectations[-1]["disposition"] = disposition
        for name in ("inbox", "outbox"):
            bash("missing-" + name, f"if mkdir {q(str(repo / name))}; then echo MKDIR_ESCAPED; else echo MKDIR_BLOCKED; fi", "MKDIR_BLOCKED")
        denied_bash("sandbox-cannot-disable", outside / "secret.txt", {"dangerouslyDisableSandbox": True})
        bash("git-commit-denied", "git commit --allow-empty -m boundary-probe", None)
        expectations[-1]["disposition"] = "violation"
        bash("network-http", "if curl -fsS --max-time 2 https://example.com >/dev/null; then echo NET_VISIBLE; else echo NET_BLOCKED; fi", "NET_BLOCKED")
        bash("toolchain-readable-executable", f"cat {q(str(tools / 'boundary-decoy.txt'))}; {q(str(tools / 'bin/dao-boundary-tool'))}", "PHASE0_TOOLCHAIN_OK")
        add("toolchain-file-denied", "Write", {"file_path": str(tools / "new-file-control.txt"), "content": "BOUNDARY_ESCAPE"}, disposition="tolerated")
        forbidden.append(tools / "new-file-control.txt")
        denied_bash("toolchain-write-denied", tools / "boundary-decoy.txt")
        for i, root in enumerate(inv.adapter.settings.toolchain_read_roots):
            if root == str(tools):
                continue
            # Real tool roots are never mutated; write-negative bait is synthetic.
            executable = Path(root) / "bin/node"
            if executable.is_file():
                bash(f"operator-toolchain-{i}", f"test -r {q(root)} && {q(str(executable))} --version", "v")
            else:
                bash(f"operator-toolchain-{i}", f"test -r {q(root)} && echo TOOL_ROOT_READABLE", "TOOL_ROOT_READABLE")
    return calls, expectations, forbidden


def home_name_evidence(observed: str | None) -> tuple[bool, dict]:
    import re
    sections = re.findall(r"HOME_NAMES_BEGIN\r?\n(.*?)HOME_NAMES_END", observed or "", re.S)
    names = sections[0].splitlines() if len(sections) == 1 else []
    unexpected = [name for name in names if name != "tmp" and not
                  re.fullmatch(r"tmp/arg0[^/]*(?:/codex-arg0[^/]*)?", name)]  # allowlist:provider -- transport: CLI restart-helper names only
    return len(sections) == 1 and not unexpected, {"visible_names": names, "unexpected_names": unexpected,
                                                  "complete_listing": len(sections) == 1}


def target_unchanged(target: Path, paths: dict[str, Path]) -> bool:
    originals = {paths["outside"] / "secret.txt": "PHASE0_OUTSIDE_SECRET\n",
                 paths["toolchain"] / "boundary-decoy.txt": "TOOLCHAIN_ORIGINAL\n"}
    original = originals.get(target.resolve())
    if original is not None:
        return target.is_file() and target.read_text() == original
    if target == paths["repo"] / "docs/outside-new-link.txt":
        return (target.is_symlink() and os.readlink(target) == str(paths["outside"] / "new-file-symlink.txt")
                and not target.resolve().exists())
    return not target.exists() and not target.is_symlink()


def checks_passed(checks: list[dict]) -> bool:
    return bool(checks) and all(c["status"] in {"passed", "skipped"} for c in checks)


def evaluate(api: FakeAPI, expectations, forbidden, inv: Invocation, paths):
    from permission_policy import classify_implementer_denial
    import re
    outputs = api.outputs()
    errors = api.errors()
    checks = []
    for item in expectations:
        observed = outputs.get(item["id"])
        evidence = observed
        kind = item.get("kind")
        if kind == "home-names":
            passed, evidence = home_name_evidence(observed)
        elif kind == "environment-names":
            names = set((observed or "").splitlines())
            unexpected = names & {"DAO_DECOY_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"}  # allowlist:provider -- transport: shell credential name check
            passed = "PATH" in names and not unexpected
            evidence = {"observed_names": sorted(names), "unexpected_names": sorted(unexpected), "output": observed}
        elif kind == "private-tmp":
            unchanged = target_unchanged(Path(item["target"]), paths)
            passed = unchanged and observed is not None and any(word in observed for word in ("WRITE_ESCAPED", "WRITE_BLOCKED"))
            evidence = {"output": observed, "host_path": item["target"], "host_unchanged": unchanged,
                        "private_tmp_write_accepted": True}
        elif kind == "unknown-tool":
            # The reviewer CLI (0.159.2) answers an absent tool with "unsupported call: <name>".
            passed = observed is not None and bool(re.search(
                r"(?:unknown|unrecognized|unsupported|disabled|not found|not available|not supported|no such).*(?:tool|function|call)"
                r"|(?:tool|function|call).*(?:unknown|unrecognized|unsupported|disabled|not found|not available|not supported|no such)", observed, re.I))
        elif item["word"] is not None:
            passed = observed is not None and item["word"] in observed
        elif "file-write" in item["label"]:
            target = Path(item["call"]["input"]["file_path"])
            passed = observed is not None and target.is_file() and target.read_text().strip() == "PHASE0_WRITE_OK"
        elif item["call"]["name"] == "Write":
            unchanged = target_unchanged(Path(item["call"]["input"]["file_path"]), paths)
            prerequisite = observed is not None and "has not been read yet" in observed.lower()
            tool_error = observed is not None and (errors.get(item["id"], False) or bool(re.search(
                r"tool_use_error|outside.*confines|denied|not allowed|permission|read.only|EACCES|EPERM|error:", observed, re.I | re.S)))
            passed = tool_error and unchanged and not prerequisite
            evidence = {"output": observed, "tool_error": bool(tool_error), "host_unchanged": unchanged,
                        "prerequisite_blocked_probe": prerequisite}
        else:
            passed = observed is not None and bool(re.search(r"denied|not allowed|permission|read.only|EACCES|EPERM", observed, re.I))
        checks.append({"check": item["label"], "status": "passed" if passed else "failed", "evidence": evidence})
        if item["disposition"]:
            disposition = classify_implementer_denial(
                {"tool_name": item["call"]["name"], "tool_input": item["call"]["input"]},
                inv.adapter._repository_root, inv.adapter._protected_paths, inv.adapter._scratch)
            checks.append({"check": item["label"] + "-classification", "status": "passed" if disposition == item["disposition"] else "failed", "evidence": disposition})
    for target in set(forbidden):
        safe = target_unchanged(target, paths)
        checks.append({"check": "unchanged:" + str(target), "status": "passed" if safe else "failed", "evidence": "fixture target retained" if safe else "fixture target changed"})
    checks.append({"check": "no-decoy-shell-leak", "status": "passed" if not any("PHASE0_ENV_SECRET" in value or "dummy-offline" in value for value in outputs.values()) else "failed", "evidence": "tool results checked"})
    if api.pair == BOUNDARY_REVIEWER:
        tools = {t.get("name") or t.get("type") for b in api.bodies for t in b.get("tools", [])}
        measurable = any("tools" in body for body in api.bodies)
        checks.append({"check": "tool-surface", "status": ("passed" if tools == {"exec_command", "write_stdin", "request_user_input", "view_image"} else "failed") if measurable else "skipped",
                       "evidence": {"tools": sorted(tools), "reason": "Request has no tools field; tool enumeration is not measurable." if not measurable else "Request tools enumerated.",
                                    "hardening": "Product adapter normalizes hardening flags with native_provider_schema.normalize_transport_profile; forbidden tools are tested separately."}})
    else:
        for name in ("inbox", "outbox"):
            checks.append({"check": "missing-protection-remains-absent:" + name,
                           "status": "passed" if not (inv.cwd / name).exists() else "failed", "evidence": str(inv.cwd / name)})
    return checks



def protected_snapshot(repo: Path) -> dict:
    """Hash only the fixture's protected trees; never follow links outside it."""
    snapshot = {}
    for name in (".git", ".orchestrator", "inbox", "outbox"):
        root = repo / name
        for path in (root, *sorted(root.rglob("*"))):
            relative = str(path.relative_to(repo))
            if path.is_symlink():
                snapshot[relative] = ("symlink", os.readlink(path))
            elif path.is_file():
                snapshot[relative] = ("file", hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode)
            elif path.is_dir():
                snapshot[relative] = ("directory", path.stat().st_mode)
            else:
                snapshot[relative] = ("absent",)
    return snapshot


def protected_changes(before: dict, after: dict) -> list[dict]:
    changes = []
    for path in sorted(before.keys() | after.keys()):
        old, new = before.get(path, ("absent",)), after.get(path, ("absent",))
        if old == new:
            continue
        kinds = []
        if old[0] == "absent":
            kinds.append("added")
        elif new[0] == "absent":
            kinds.append("removed")
        else:
            if old[0] != new[0] or (old[0] in {"file", "symlink"} and old[1] != new[1]):
                kinds.append("content")
            old_mode = old[-1] if old[0] in {"file", "directory"} else None
            new_mode = new[-1] if new[0] in {"file", "directory"} else None
            if old_mode != new_mode:
                kinds.append("mode")
        changes.append({"path": path, "changes": kinds, "before": old, "after": new})
    return changes


def valid_implementer_result(bundle):
    return {"result": {"schema_version": "native-agent-implementer-result-v3", "request_id": bundle.bound_context.request_id,
                       "result_type": "implementation_result", "ready": True, "test_files": [], "finding_dispositions": []}}


def validate_result(inv, stdout, stderr):
    from native_implementer_request import validate_native_implementer_provider_response
    from native_implementer_contract import parse_bound_native_implementer_contract_result
    from native_review_request import validate_native_review_provider_response
    from native_review_contract import parse_bound_native_contract_result
    result = json.loads(inv.adapter.extract_output(stdout, stderr, {}))
    if inv.adapter.role_binding.role.value == "implementer":
        validate_native_implementer_provider_response(result, inv.bundle)
        parse_bound_native_implementer_contract_result(result, inv.bundle.bound_context)
    else:
        validate_native_review_provider_response(result, inv.bundle)
        parse_bound_native_contract_result(result, inv.bundle.bound_context)
    return result



def validate_denied_result(inv, stdout):
    """A deliberate protected denial must still finish with a valid bound result."""
    from native_implementer_request import validate_native_implementer_provider_response
    from native_implementer_contract import parse_bound_native_implementer_contract_result
    events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    finals = [e for e in events if e.get("type") == "result"]
    if len(finals) != 1 or events[-1] is not finals[0] or finals[0].get("is_error") is not False or finals[0].get("subtype") != "success":
        raise ValueError("missing successful final stream event")
    result = finals[0]["structured_output"]["result"]
    validate_native_implementer_provider_response(result, inv.bundle)
    parse_bound_native_implementer_contract_result(result, inv.bundle.bound_context)
    return result


def end_to_end_check(inv: Invocation, run: dict) -> dict:
    evidence = {"cli_exit_code": run["exit_code"], "timed_out": run["timed_out"]}
    try:
        evidence["result"] = validate_result(inv, run["stdout"], run["stderr"])
        passed = run["exit_code"] == 0 and not run["timed_out"]
    except (ValueError, RuntimeError, KeyError) as exc:
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        passed = False
    return {"check": "native-end-to-end", "status": "passed" if passed else "failed", "evidence": evidence}


def run_pair(pair: str, *, out: Path, toolchain_roots: tuple[str, ...] = (), identity=None,
             deadline: float | None = None) -> dict:
    start = time.monotonic()
    deadline = start + 60 if deadline is None else deadline
    out.mkdir(parents=True, exist_ok=False)
    checks = []
    try:
        if time.monotonic() >= deadline:
            raise TimeoutError("short-mode time budget exhausted before provider preparation")
        selected = ADAPTER_PROFILES[pair]
        identity = identity or identify(selected.capability)
        with tempfile.TemporaryDirectory(prefix="dao-offline-boundary-") as temp:
            paths = fixture(Path(temp))
            roots = (str(paths["toolchain"]), *toolchain_roots) if selected.role == "implementer" else ()
            with decoy_environment(), adapter_invocation(pair, paths["repo"], identity, "Offline boundary verification; execute scripted calls, then finish with the bound native result.", roots) as inv:
                calls, expectations, forbidden = scripts_for(pair, inv, paths)
                api = FakeAPI(pair, calls, valid_implementer_result(inv.bundle) if selected.role == "implementer" else {"done": True})
                with api.serve() as port:
                    command, env = offline_overrides(inv, pair, port)
                    before_protected = protected_snapshot(inv.cwd)
                    run = execute(command, env=env, cwd=inv.cwd, stdin=inv.prepared.stdin_text,
                                  timeout=deadline - time.monotonic())
                    # The production adapter removes the sandbox's empty config.worktree placeholders.
                    placeholders = getattr(inv.adapter, "remove_sandbox_placeholders", lambda: ())()
                    after_protected = protected_snapshot(inv.cwd)
                (out / "sandbox-placeholders-removed.json").write_text(json_text([str(path) for path in placeholders]) + "\n")
                (out / "protected-before.json").write_text(json_text(before_protected) + "\n")
                (out / "protected-after.json").write_text(json_text(after_protected) + "\n")
                (out / "requests.jsonl").write_text("".join(json_text(b) + "\n" for b in api.bodies))
                (out / "stdout.jsonl").write_text(run["stdout"])
                (out / "stderr.txt").write_text(run["stderr"])
                checks.extend(evaluate(api, expectations, forbidden, inv, paths))
                changes = protected_changes(before_protected, after_protected)
                checks.append({"check": "protected-trees-unchanged", "status": "passed" if not changes else "failed",
                               "evidence": {"changes": changes, "baseline": "after adapter preparation, immediately before CLI start"}})
                if selected.role == "implementer":
                    checks.append({"check": "parent-decoy-filtered", "status": "passed" if "DAO_DECOY_TOKEN" not in inv.env else "failed", "evidence": sorted(inv.env)})
                checks.append({"check": "process", "status": "passed" if run["exit_code"] == 0 and not run["timed_out"] else "failed", "evidence": {k: run[k] for k in ("exit_code", "timed_out")}})
                if selected.role == "implementer":
                    from agent_adapters import AgentPermissionError
                    try:
                        inv.adapter.extract_output(run["stdout"], run["stderr"], {})
                    except AgentPermissionError:
                        validate_denied_result(inv, run["stdout"])
                        checks.append({"check": "protected-denials-stop-adapter", "status": "passed", "evidence": inv.adapter.metadata})
                    else:
                        checks.append({"check": "protected-denials-stop-adapter", "status": "failed", "evidence": inv.adapter.metadata})
            if selected.role == "implementer":
                with adapter_invocation(pair, paths["repo"], identity, "Finish with the request-bound native result.", roots) as inv:
                    api = FakeAPI(pair, [], valid_implementer_result(inv.bundle))
                    with api.serve() as port:
                        command, env = offline_overrides(inv, pair, port)
                        run = execute(command, env=env, cwd=inv.cwd, stdin=inv.prepared.stdin_text,
                                      timeout=deadline - time.monotonic())
                    (out / "e2e-requests.jsonl").write_text("".join(json_text(b) + "\n" for b in api.bodies))
                    (out / "e2e-stdout.jsonl").write_text(run["stdout"])
                    (out / "e2e-stderr.txt").write_text(run["stderr"])
                    checks.append(end_to_end_check(inv, run))
    except Exception as exc:
        checks.append({"check": "execution", "status": "failed", "evidence": f"{type(exc).__name__}: {exc}"})
    elapsed = time.monotonic() - start
    checks.append({"check": "within-60-seconds", "status": "passed" if elapsed <= 60 else "failed", "evidence": elapsed})
    checks.append({"check": "within-short-mode-budget", "status": "passed" if time.monotonic() <= deadline else "failed", "evidence": "shared 60-second budget for the selected pairs"})
    report = {"output_directory": str(out), "pair": pair, "passed": checks_passed(checks), "checks": checks, "elapsed_seconds": elapsed}
    (out / "report.json").write_text(json_text(report) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair", choices=(*ADAPTER_PROFILES, "all"), required=True)
    parser.add_argument("--toolchain-root", type=Path, action="append", default=[])
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    out = args.out or Path(tempfile.mkdtemp(prefix="dao-boundary-report-"))
    pairs = tuple(ADAPTER_PROFILES) if args.pair == "all" else (args.pair,)
    reports = []
    deadline = time.monotonic() + 60
    for pair in pairs:
        destination = out / pair
        if destination.exists():
            destination = out / f"{pair}-{time.time_ns()}"
        report = run_pair(pair, out=destination,
                          toolchain_roots=tuple(str(p.resolve()) for p in args.toolchain_root), deadline=deadline)
        reports.append(report)
    for report in reports:
        print(json_text(report))
    return 0 if all(r["passed"] for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
