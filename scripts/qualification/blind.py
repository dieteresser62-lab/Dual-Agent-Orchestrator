"""Blind rating packet export and guarded independent rater command."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts import probe_reviewer as probe
from scripts.qualification.profiles import LEGACY_BLIND_WORDS


def prepare(*, protocol_path: Path, series_path: Path, envelopes_path: Path,
            output_dir: Path) -> dict:
    protocol = probe.strict_json(protocol_path.read_bytes())
    pair = probe.qualification_pair(protocol)
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
        provider_words=(*pair.providers, *pair.capabilities.values()))
    packet = probe.export_rater_packet(assessment, mapping, corpus, protocol, rubric)
    probe._write_evidence_file(private / "sources.json", sources)
    probe._write_evidence_file(private / "mapping.json", mapping)
    (private / "sources.json").chmod(0o600)
    (private / "mapping.json").chmod(0o600)
    probe._write_evidence_file(packets / "assessment.json", assessment)
    for rater in ("codex", "steering"):  # allowlist:provider -- certification data: independent raters
        probe._write_evidence_file(packets / f"{rater}-packet.json", packet)
    (packets / "codex-prompt.txt").write_text(probe.render_rater_prompt(packet, protocol), encoding="utf-8")  # allowlist:provider -- certification data: independent rater prompt
    words = {*LEGACY_BLIND_WORDS, *pair.providers, *pair.capabilities.values()}
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
    options = {name: getattr(arguments, name) for name in
               ("binary", "prompt", "output", "events", "stderr", "private")}
    if arguments.command == "codex-command":  # allowlist:provider -- certification data: print-only rater command
        print(codex_command(**options))  # allowlist:provider -- certification data: independent rater command
        return 0
    return run_codex(**options, live=arguments.live)  # allowlist:provider -- certification data: live rater gate


if __name__ == "__main__":
    raise SystemExit(main())
