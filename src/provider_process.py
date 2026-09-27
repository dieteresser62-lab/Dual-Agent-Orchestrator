"""Process identity evidence for reconciling an open provider start."""

from __future__ import annotations

import json
import logging
import os
import signal
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


@dataclass(frozen=True)
class ProcessIdentity:
    boot_id: str
    pid: int
    start_ticks: int
    pgid: int
    sid: int


@dataclass(frozen=True)
class ProcStat:
    state: str
    start_ticks: int
    pgid: int
    sid: int


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


def _proc_stat(pid: int) -> ProcStat | None:
    try:
        data = Path(f"/proc/{pid}/stat").read_text()
        if ")" not in data:
            raise ValueError("malformed proc stat")
        fields = data[data.rfind(")") + 2 :].split()
        return ProcStat(fields[0], int(fields[19]), int(fields[2]), int(fields[3]))
    except FileNotFoundError:
        return None


def capture_process_identity(pid: int) -> ProcessIdentity | None:
    boot_id = _boot_id()
    if boot_id is None:
        return None
    observed = _proc_stat(pid)
    if observed is None or observed.pgid != pid or observed.sid != pid:
        return None
    return ProcessIdentity(boot_id, pid, observed.start_ticks, pid, pid)


def _group_state(identity: ProcessIdentity) -> ProcessStatus:
    """Track the bound session without mistaking a foreign process for ours."""
    boot = _boot_id()
    if boot is None:
        return ProcessStatus.UNKNOWN
    if boot != identity.boot_id:
        return ProcessStatus.ENDED
    leader = _proc_stat(identity.pid)
    if leader is not None:
        if leader.start_ticks != identity.start_ticks:
            # Linux cannot reuse this PID while it still names our PGID or SID.
            return ProcessStatus.ENDED
        if leader.state not in {"Z", "X", "x"}:
            if leader.pgid != identity.pgid or leader.sid != identity.sid:
                return ProcessStatus.UNKNOWN
            return ProcessStatus.RUNNING
    # A leader may have exited while descendants retain its pipes and group.
    # Scan the whole session too: a member can move to another process group.
    try:
        entries = os.scandir("/proc")
        with entries as directory:
            for entry in directory:
                if not entry.name.isdecimal():
                    continue
                pid = int(entry.name)
                if pid == identity.pid:
                    continue
                member = _proc_stat(pid)
                if member is None or member.state in {"Z", "X", "x"}:
                    continue
                if member.pgid == identity.pgid and member.sid != identity.sid:
                    return ProcessStatus.UNKNOWN
                if member.sid == identity.sid:
                    if member.start_ticks < identity.start_ticks:
                        return ProcessStatus.UNKNOWN
                    return ProcessStatus.RUNNING
    except (OSError, ValueError, IndexError):
        return ProcessStatus.UNKNOWN
    return ProcessStatus.ENDED


def observe_identity(identity: ProcessIdentity) -> ProcessObservation:
    try:
        return ProcessObservation(_group_state(identity), identity.pid)
    except (OSError, ValueError, IndexError):
        return ProcessObservation(ProcessStatus.UNKNOWN, identity.pid)


def count_process_group_members(identity: ProcessIdentity) -> int | None:
    """Count live members only when the bound group's ownership is provable."""
    if _boot_id() != identity.boot_id:
        return None
    try:
        leader = _proc_stat(identity.pid)
        if leader is not None and leader.start_ticks != identity.start_ticks:
            return 0
        count = 0
        if leader is not None and leader.state not in {"Z", "X", "x"}:
            if leader.pgid != identity.pgid or leader.sid != identity.sid:
                return None
            count = 1
        with os.scandir("/proc") as directory:
            for entry in directory:
                if not entry.name.isdecimal() or int(entry.name) == identity.pid:
                    continue
                member = _proc_stat(int(entry.name))
                if member is None or member.state in {"Z", "X", "x"}:
                    continue
                if member.pgid == identity.pgid:
                    if member.sid != identity.sid or member.start_ticks < identity.start_ticks:
                        return None
                    count += 1
        return count
    except (OSError, ValueError, IndexError):
        return None


def signal_process_group(identity: ProcessIdentity, sig: signal.Signals) -> bool:
    """Signal only while a member of the bound group is still observable."""
    if _boot_id() != identity.boot_id:
        return False
    try:
        leader = _proc_stat(identity.pid)
        if leader is not None and leader.start_ticks != identity.start_ticks:
            return False
        if leader is not None and leader.state not in {"Z", "X", "x"}:
            if leader.pgid != identity.pgid or leader.sid != identity.sid:
                return False
            own_member = True
        else:
            own_member = False
        with os.scandir("/proc") as directory:
            for entry in directory:
                if not entry.name.isdecimal() or int(entry.name) == identity.pid:
                    continue
                member = _proc_stat(int(entry.name))
                if member is None or member.state in {"Z", "X", "x"}:
                    continue
                if member.pgid == identity.pgid:
                    if member.sid != identity.sid or member.start_ticks < identity.start_ticks:
                        return False
                    own_member = True
        if not own_member:
            return False
        os.killpg(identity.pgid, sig)
        return True
    except (OSError, ValueError, IndexError):
        return False


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
    try:
        identity = capture_process_identity(pid)
    except (OSError, ValueError, IndexError) as exc:
        logger.warning("process identity unavailable: /proc/%s/stat could not be read: %s", pid, exc)
        return
    if identity is None:
        logger.warning("process identity unavailable: boot ID, session or /proc/%s/stat", pid)
        return
    payload.update({"boot_id": identity.boot_id, "pid": pid,
                    "start_ticks": identity.start_ticks, "pgid": identity.pgid,
                    "sid": identity.sid})
    _write_evidence(path, payload)


def observe_process(response_path: Path, effect_key: str) -> ProcessObservation:
    path = process_evidence_path(response_path)
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return ProcessObservation(ProcessStatus.UNKNOWN)
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or set(raw) not in (
            {"effect_key", "boot_id", "pid", "start_ticks", "pgid", "sid"},
            {"effect_key", "before", "boot_id", "pid", "start_ticks", "pgid", "sid"},
        ) or raw["effect_key"] != effect_key:
            return ProcessObservation(ProcessStatus.UNKNOWN)
        pid, start_ticks, boot_id = raw["pid"], raw["start_ticks"], raw["boot_id"]
        pgid, sid = raw["pgid"], raw["sid"]
        if (type(pid) is not int or pid <= 0 or type(start_ticks) is not int
                or start_ticks < 0 or not isinstance(boot_id, str) or not boot_id
                or type(pgid) is not int or pgid != pid
                or type(sid) is not int or sid != pid):
            return ProcessObservation(ProcessStatus.UNKNOWN)
        return observe_identity(ProcessIdentity(boot_id, pid, start_ticks, pgid, sid))
    except (OSError, ValueError, IndexError, UnicodeError, TypeError):
        return ProcessObservation(ProcessStatus.UNKNOWN)
