"""Blind rating packet export and guarded independent rater command."""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import probe_reviewer as probe
from scripts.qualification.profiles import LEGACY_BLIND_WORDS, BLIND_WORDS


def prepare(*, protocol_path: Path, series_path: Path, envelopes_path: Path,
            output_dir: Path) -> dict:
    protocol = probe.strict_json(protocol_path.read_bytes())
    pair = probe.qualification_pair(protocol)
    raters = probe.qualification_raters(protocol)
    private = output_dir / "private"
    packets = output_dir / "packets"
    if private.exists() or packets.exists():
        raise FileExistsError("blind export destination already exists")
    private.mkdir(parents=True, mode=0o700)
    packets.mkdir(mode=0o700)
    os.chmod(private, 0o700)
    corpus = probe.strict_json((probe.ROOT / "tests/fixtures/reviewer-quality-corpus-v1.json").read_bytes())
    rubric = probe.strict_json((pair.evidence_directory / "quality-rubric-v1.json").read_bytes())
    sources = probe.quality_blind_sources(probe.read_evidence(series_path),
                                          probe.read_evidence(envelopes_path), protocol)
    assessment, mapping = probe.blind_package(sources, seed=protocol["quality"]["blind_seed"],
        provider_words=(*pair.providers, *pair.capabilities.values(),
                        *(BLIND_WORDS if protocol["schema_version"] == "qualification-protocol-v6" else ())))
    packet = probe.export_rater_packet(assessment, mapping, corpus, protocol, rubric)
    probe._write_evidence_file(private / "sources.json", sources)
    probe._write_evidence_file(private / "mapping.json", mapping)
    (private / "sources.json").chmod(0o600)
    (private / "mapping.json").chmod(0o600)
    probe._write_evidence_file(packets / "assessment.json", assessment)
    for rater in raters:
        probe._write_evidence_file(packets / f"{rater}-packet.json", packet)
    if protocol["schema_version"] == "qualification-protocol-v5":
        (packets / "codex-prompt.txt").write_text(probe.render_rater_prompt(packet, protocol), encoding="utf-8")  # allowlist:provider -- certification data: unchanged v5 prompt
    else:
        for rater in raters:
            prompt = probe.render_rater_prompt(packet, protocol) + f"\nDeine Bewerterkennung: {rater}.\n"
            (packets / f"{rater}-prompt.txt").write_text(prompt, encoding="utf-8")
            schema = dict(packet["rubric"]["rating_json_schema"])
            schema = probe.strict_json(probe.canonical(schema))
            schema["properties"]["rater"] = {"type": "string", "const": rater}
            schema["properties"]["packet_sha256"] = {"type": "string", "const": packet["packet_sha256"]}
            probe._write_evidence_file(packets / f"{rater}-rating-schema.json", schema)
    words = {*(LEGACY_BLIND_WORDS if protocol["schema_version"] == "qualification-protocol-v5" else BLIND_WORDS),
             *pair.providers, *pair.capabilities.values()}
    hits = sorted({row["id"] for row in packet["responses"]
                   if any(re.search(r"\b" + re.escape(word) + r"\b",
                                    json.dumps(row["response"], ensure_ascii=False), re.I)
                          for word in words)})
    flags = sorted(row["id"] for row in assessment["responses"] if row["possible_self_identification"])
    return {"packet_sha256": packet["packet_sha256"], "responses": len(packet["responses"]),
            "self_identification_flags": flags, "provider_word_hits": hits}


def codex_command(*, binary: Path, prompt: Path, output: Path, events: Path,  # allowlist:provider -- certification data: independent rater command
                  stderr: Path, private: Path) -> str:
    """Print a complete shell command; this function never starts a provider."""
    if (not binary.is_absolute() or not prompt.is_absolute() or not output.is_absolute() or
            not events.is_absolute() or not stderr.is_absolute() or
            not prompt.is_file() or prompt.is_symlink() or not private.is_dir() or private.is_symlink()):
        raise ValueError("absolute binary, existing prompt and private directory required")
    quoted = lambda value: shlex.quote(str(value))
    restore = shlex.quote("chmod 700 -- " + quoted(private))
    return (f"chmod 000 -- {quoted(private)}; "
            f"trap {restore} EXIT; cd {quoted(prompt.parent)}; "
            f"{quoted(binary)} exec --json --skip-git-repo-check -s read-only "
            f"-m gpt-6-sol -c 'model_reasoning_effort=\"medium\"' "
            f"--output-last-message {quoted(output)} - < {quoted(prompt)} "
            f"> {quoted(events)} 2> {quoted(stderr)}")


def run_codex(*, binary: Path, prompt: Path, output: Path, events: Path,  # allowlist:provider -- certification data: independent rater command
              stderr: Path, private: Path, live: bool = False) -> int:
    if not live:
        raise PermissionError("independent rater provider call requires --live")
    if (not binary.is_absolute() or not binary.is_file() or not prompt.is_absolute() or
            not prompt.is_file() or prompt.is_symlink() or not private.is_dir() or private.is_symlink()):
        raise ValueError("absolute binary, existing prompt and private directory required")
    if any(not path.is_absolute() or path.exists() or path.is_symlink()
           for path in (output, events, stderr)):
        raise FileExistsError("rating output already exists")
    os.chmod(private, 0o000)
    try:
        with prompt.open("rb") as source, events.open("wb") as event_stream, stderr.open("wb") as error_stream:
            completed = subprocess.run(
                [str(binary), "exec", "--json", "--skip-git-repo-check", "-s", "read-only",
                 "-m", "gpt-6-sol", "-c", 'model_reasoning_effort="medium"',
                 "--output-last-message", str(output), "-"], cwd=prompt.parent,
                stdin=source, stdout=event_stream, stderr=error_stream, check=False)
        return completed.returncode
    finally:
        os.chmod(private, 0o700)


def _agy_options(*, binary: Path, prompt: Path, schema: Path, output: Path,
                 events: Path, stderr: Path, private: Path, home: Path,
                 run_root: Path) -> dict:
    paths = locals().copy()
    if any(not path.is_absolute() or path.is_symlink() for path in paths.values()):
        raise ValueError("absolute regular rater paths required")
    if (not prompt.is_file() or not schema.is_file() or not private.is_dir()
            or prompt.parent != schema.parent or prompt == schema):
        raise ValueError("prompt, schema and separate private directory required")
    source = prompt.parent.resolve()
    if any(source.is_relative_to(path.resolve()) or path.resolve().is_relative_to(source)
           for path in (private, home, run_root)) or home.resolve() == Path.home().resolve():
        raise ValueError("rater HOME, run root and private mapping must be isolated")
    if not run_root.is_relative_to(Path("/var/tmp")) or run_root == Path("/var/tmp"):
        raise ValueError("rater run root must be below /var/tmp")
    if home.is_relative_to(run_root) or run_root.is_relative_to(home):
        raise ValueError("rater HOME and run root overlap")
    if len({output, events, stderr}) != 3 or any(
            path.resolve().is_relative_to(source) or path.resolve().is_relative_to(private.resolve())
            for path in (output, events, stderr)):
        raise ValueError("rater output paths must be distinct and outside blind inputs")
    return paths


def agy_command(**options) -> str:
    """Print the executable wrapper that prepares the production read-only boundary.

    Temporary container names are allocated at execution time, so the wrapper
    owns HOME isolation, dao-reviewer configuration, schema flags and sealing.
    Printing does not write configuration or start any process.
    """
    paths = _agy_options(**options)
    command = [sys.executable, str(Path(__file__).resolve()), "run-agy"]
    for name, path in paths.items():
        command.extend(["--" + name.replace("_", "-"), str(path)])
    return shlex.join([*command, "--live"])


def run_agy(*, live: bool = False, **options) -> int:
    if not live:
        raise PermissionError("independent rater provider call requires --live")
    paths = _agy_options(**options)
    binary, prompt, schema, output, events, stderr, private, home, run_root = (
        paths[name] for name in ("binary", "prompt", "schema", "output", "events", "stderr", "private", "home", "run_root"))
    process_dir = events.parent / (events.name + ".process")
    if not binary.is_file() or any(path.exists() for path in (output, events, stderr, process_dir)):
        raise FileExistsError("rater binary missing or rating output already exists")
    from agent_adapters import create_reviewer_qualification_adapter
    from agent_config import AgentSettings
    from agent_roles import AgentRoleName
    from antigravity_adapter import AntigravityTransport  # allowlist:provider -- transport: measured read-only rater boundary
    from role_binding import binding_for
    from schema_validation import validate_schema_document

    writer = probe.strict_json(schema.read_bytes())
    if writer.get("properties", {}).get("rater") != {"type": "string", "const": "agy"}:  # allowlist:provider -- certification data: pinned independent rater
        raise ValueError("rating schema must bind the AGY rater")
    policy = ("You are an independent blind rater. Read only the supplied blind prompt, "
              "do not research providers, follow its rubric and return only the rating JSON.")
    binding = replace(binding_for("antigravity", AgentRoleName.REVIEWER), policy=policy)  # allowlist:provider -- profile configuration: read-only rater boundary
    settings = AgentSettings("antigravity", str(binary), "gemini-3.1-pro-high", 600, "high",  # allowlist:provider -- profile configuration: independent rater
                             antigravity_home=str(home), antigravity_run_root=str(run_root))
    adapter = create_reviewer_qualification_adapter(settings, role_binding=binding)
    private_mode = private.stat().st_mode & 0o777
    os.chmod(private, 0o000)
    try:
        with adapter.review_execution_boundary(prompt.parent, tuple(sorted((prompt.name, schema.name)))):
            workspace = adapter.prepared_workspace
            directive = f"Read {workspace.repo / prompt.name} and return the schema-bound blind rating."
            command = AntigravityTransport.command(settings, workspace, directive, workspace.repo / schema.name)
            adapter.seal_provider_input()
            adapter.before_provider_process()
            completed = probe.run(command, env=adapter._isolated_environment(), cwd=workspace.repo,
                                  out=process_dir, limit=600, cleanup_limit=15)
            stdout = (process_dir / "stdout.txt").read_text()
            error = (process_dir / "stderr.txt").read_text()
            events.write_text(stdout, encoding="utf-8")
            stderr.write_text(error, encoding="utf-8")
            if completed["timed_out"] or completed["remaining_identities"] or completed["survivor_identities"]:
                raise RuntimeError("blind rater timed out or left processes")
            envelope, _ = AntigravityTransport.envelope(stdout, error, completed["exit_code"], probe.canonical(writer))
            rating = envelope["structured_output"]
            validate_schema_document(rating, writer)
        # Post-run checks complete before the rating is accepted.
        probe._write_evidence_file(output, rating)
        return completed["exit_code"]
    finally:
        os.chmod(private, private_mode)


def steering_prompt(packet: Path, output: Path) -> str:
    template = (Path(__file__).parent / "steering-rater-prompt-v1.md").read_text(encoding="utf-8")
    if not packet.is_absolute() or not output.is_absolute():
        raise ValueError("steering packet and rating output paths must be absolute")
    return template.replace("{packet}", str(packet)).replace("{output}", str(output))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare_cmd = commands.add_parser("prepare")
    prepare_cmd.add_argument("--protocol", type=Path, required=True)
    prepare_cmd.add_argument("--series", type=Path, required=True)
    prepare_cmd.add_argument("--envelopes", type=Path, required=True)
    prepare_cmd.add_argument("--output-dir", type=Path, required=True)
    for name in ("codex-command", "run-codex"):  # allowlist:provider -- certification data: independent rater operation
        command = commands.add_parser(name)
        for option in ("binary", "prompt", "output", "events", "stderr", "private"):
            command.add_argument("--" + option, type=Path, required=True)
        if name == "run-codex":  # allowlist:provider -- certification data: guarded rater start
            command.add_argument("--live", action="store_true")
    for name in ("agy-command", "run-agy"):
        command = commands.add_parser(name)
        for option in ("binary", "prompt", "schema", "output", "events", "stderr", "private", "home", "run-root"):
            command.add_argument("--" + option, type=Path, required=True)
        if name == "run-agy":
            command.add_argument("--live", action="store_true")
    steering = commands.add_parser("steering-prompt")
    steering.add_argument("--packet", type=Path, required=True)
    steering.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if arguments.command == "prepare":
        print(json.dumps(prepare(protocol_path=arguments.protocol, series_path=arguments.series,
            envelopes_path=arguments.envelopes, output_dir=arguments.output_dir), ensure_ascii=False))
        return 0
    if arguments.command == "steering-prompt":
        print(steering_prompt(arguments.packet, arguments.output))
        return 0
    if arguments.command in ("agy-command", "run-agy"):
        options = {name: getattr(arguments, name) for name in
                   ("binary", "prompt", "schema", "output", "events", "stderr", "private", "home", "run_root")}
        if arguments.command == "agy-command":
            print(agy_command(**options))
            return 0
        return run_agy(**options, live=arguments.live)
    options = {name: getattr(arguments, name) for name in
               ("binary", "prompt", "output", "events", "stderr", "private")}
    if arguments.command == "codex-command":  # allowlist:provider -- certification data: print-only rater command
        print(codex_command(**options))  # allowlist:provider -- certification data: independent rater command
        return 0
    return run_codex(**options, live=arguments.live)  # allowlist:provider -- certification data: live rater gate


if __name__ == "__main__":
    raise SystemExit(main())
