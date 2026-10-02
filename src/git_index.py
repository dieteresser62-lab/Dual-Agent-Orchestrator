"""Read an index snapshot without Git, locks, configuration, or worktree access."""

import hashlib
import struct


def index_content(data: bytes) -> tuple:
    """Keep everything except entry stat caches and the derived checksum.

    https://git-scm.com/docs/index-format: the first 40 entry bytes are
    ctime, mtime, dev, ino, mode, uid, gid and size. Mode is content-bearing.
    Preserve the object ID, all flags (including stage, assume-valid,
    skip-worktree and intent-to-add), decoded path, and every extension byte.
    Even optional caches are protected conservatively. Split indexes are
    rejected: their entry interpretation requires the separate shared index.
    """
    algorithms = [algorithm for algorithm in (hashlib.sha1, hashlib.sha256)
                  if len(data) >= 12 + algorithm().digest_size
                  and algorithm(data[:-algorithm().digest_size]).digest()
                  == data[-algorithm().digest_size:]]
    if len(algorithms) != 1:
        raise ValueError("invalid index checksum")
    width = algorithms[0]().digest_size
    body = data[:-width]
    signature, version, count = struct.unpack_from("!4sII", body)
    if signature != b"DIRC" or version not in (2, 3, 4):
        raise ValueError("unsupported index header")
    offset, previous = 12, b""
    entries = []

    def take(size):
        nonlocal offset
        if offset + size > len(body):
            raise ValueError("truncated index")
        value = body[offset:offset + size]
        offset += size
        return value

    for _ in range(count):
        start = offset
        fields = take(40)
        mode = struct.unpack_from("!I", fields, 24)[0]
        if mode not in (0o100644, 0o100755, 0o120000, 0o160000, 0o040000):
            raise ValueError("invalid index mode")
        oid = take(width)
        flags = int.from_bytes(take(2), "big")
        extended = 0
        if flags & 0x4000:
            if version == 2:
                raise ValueError("extended flags in index v2")
            extended = int.from_bytes(take(2), "big")
            if extended & ~0x6000:
                raise ValueError("unknown extended index flags")
        remove = 0
        if version == 4:
            byte = take(1)[0]
            remove = byte & 0x7f
            for _ in range(9):
                if not byte & 0x80:
                    break
                byte = take(1)[0]
                remove = ((remove + 1) << 7) | (byte & 0x7f)
            else:
                raise ValueError("invalid index path prefix")
            if remove > len(previous):
                raise ValueError("invalid index path prefix")
        end = body.find(b"\0", offset)
        if end < 0:
            raise ValueError("unterminated index path")
        suffix = take(end - offset + 1)[:-1]
        path = (previous[:len(previous) - remove] if version == 4 else b"") + suffix
        if (not path or path.startswith(b"/")
                or any(part in (b"", b".", b"..", b".git") for part in path.rstrip(b"/").split(b"/"))
                or (path.endswith(b"/") and mode != 0o040000)
                or flags & 0xfff != min(len(path), 0xfff)):
            raise ValueError("invalid index path")
        if version != 4:
            padding = take((-(offset - start)) % 8)
            if any(padding):
                raise ValueError("invalid index padding")
        stage = (flags >> 12) & 3
        if entries and (path, stage) <= (entries[-1][0], (entries[-1][3] >> 12) & 3):
            raise ValueError("unordered index entries")
        entries.append((path, mode, oid, flags, extended))
        previous = path
    extensions = []
    while offset < len(body):
        name, size = struct.unpack("!4sI", take(8))
        if not 65 <= name[0] <= 90 and name != b"sdir":
            raise ValueError("unsupported mandatory index extension")
        extensions.append((name, take(size)))
    return version, width, tuple(entries), tuple(extensions)
