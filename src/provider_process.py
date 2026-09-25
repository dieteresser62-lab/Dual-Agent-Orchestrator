"""Process identity evidence for reconciling an open provider start."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
import stat
import tempfile


logger = logging.getLogger(__name__)


class ProcessStatus(StrEnum):
    RUNNING = "running"
    ENDED = "ended"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProcessObservation:
    status: ProcessStatus
    pid: int | None = None


class ProviderOutcomeUnknown(RuntimeError):
    def __init__(self, effect_key: str, response_path: Path) -> None:
        self.effect_key = effect_key
        self.response_path = response_path
        super().__init__(f"provider outcome is unknown for {effect_key}")


def process_evidence_path(response_path: Path) -> Path:
    return response_path.with_suffix(response_path.suffix + ".process.json")


def _boot_id() -> str | None:
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return None
    return value or None


def _proc_stat(pid: int) -> tuple[str, int] | None:
    try:
        data = Path(f"/proc/{pid}/stat").read_text()
        fields = data[data.rfind(")") + 2 :].split()
        return fields[0], int(fields[19])
    except FileNotFoundError:
        return None


def _write_evidence(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, sort_keys=True) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def record_attempt_baseline(
    response_path: Path, effect_key: str, before: dict[str, object],
) -> None:
    path = process_evidence_path(response_path)
    if path.exists() or path.is_symlink():
        raise RuntimeError("provider attempt baseline already exists")
    _write_evidence(path, {"effect_key": effect_key, "before": before})


def changed_paths_since_start(
    response_path: Path, effect_key: str,
    current: dict[str, object], fallback: tuple[str, ...],
) -> tuple[str, ...]:
    path = process_evidence_path(response_path)
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return fallback
        raw = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(raw, dict) or raw.get("effect_key") != effect_key
                or not isinstance(raw.get("before"), dict)):
            return fallback
        before = raw["before"]
        return tuple(sorted(
            key for key in before.keys() | current.keys()
            if before.get(key) != current.get(key)
        ))
    except (OSError, ValueError, TypeError, UnicodeError):
        return fallback


def record_process_start(response_path: Path, effect_key: str, pid: int) -> None:
    """Publish identity atomically; missing evidence always remains uncertain."""
    path = process_evidence_path(response_path)
    payload: dict[str, object] = {"effect_key": effect_key}
    if path.exists() or path.is_symlink():
        if not stat.S_ISREG(path.lstat().st_mode):
            raise RuntimeError("provider process identity target is not a regular file")
        prior = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(prior, dict) or prior.get("effect_key") != effect_key
                or set(prior) != {"effect_key", "before"}
                or not isinstance(prior.get("before"), dict)):
            raise RuntimeError("provider attempt baseline is invalid")
        payload = prior
    if pid <= 0:
        raise RuntimeError("provider process identity has an invalid pid")
    boot_id = _boot_id()
    if boot_id is None:
        logger.warning("process identity unavailable: /proc boot ID could not be read")
        return
    try:
        observed = _proc_stat(pid)
    except OSError as exc:
        logger.warning("process identity unavailable: /proc/%s/stat could not be read: %s", pid, exc)
        return
    if observed is None:
        logger.warning("process identity unavailable: /proc/%s/stat is absent", pid)
        return
    payload.update({"boot_id": boot_id, "pid": pid, "start_ticks": observed[1]})
    _write_evidence(path, payload)


def observe_process(response_path: Path, effect_key: str) -> ProcessObservation:
    path = process_evidence_path(response_path)
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return ProcessObservation(ProcessStatus.UNKNOWN)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) not in (
            {"effect_key", "boot_id", "pid", "start_ticks"},
            {"effect_key", "before", "boot_id", "pid", "start_ticks"},
        ) or raw["effect_key"] != effect_key:
            return ProcessObservation(ProcessStatus.UNKNOWN)
        pid, start_ticks, boot_id = raw["pid"], raw["start_ticks"], raw["boot_id"]
        if (type(pid) is not int or pid <= 0 or type(start_ticks) is not int
                or start_ticks < 0 or not isinstance(boot_id, str) or not boot_id):
            return ProcessObservation(ProcessStatus.UNKNOWN)
        current_boot = _boot_id()
        if current_boot is None:
            return ProcessObservation(ProcessStatus.UNKNOWN, pid)
        if current_boot != boot_id:
            return ProcessObservation(ProcessStatus.ENDED, pid)
        current = _proc_stat(pid)
        if current is None or current[1] != start_ticks or current[0] in {"Z", "X", "x"}:
            return ProcessObservation(ProcessStatus.ENDED, pid)
        return ProcessObservation(ProcessStatus.RUNNING, pid)
    except (OSError, ValueError, IndexError, UnicodeError, TypeError):
        return ProcessObservation(ProcessStatus.UNKNOWN)
