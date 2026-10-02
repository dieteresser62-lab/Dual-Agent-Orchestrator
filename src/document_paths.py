"""Display local home paths in repository documents without changing records."""

from pathlib import Path
import re


def home_relative_document(text: str) -> str:
    """Replace the local HOME at path boundaries, including embedded output.

    Do not resolve paths or access targets: rendering is a pure projection.
    Preserve sibling prefixes such as /home/operator-other and other roots.
    """
    home = str(Path.home()).rstrip("/")
    if not home:
        return text
    # Node stack traces use local file URLs; display those as paths as well.
    pattern = re.compile(r"(?<![\w/])(?:file://(?:localhost)?)?" + re.escape(home)
                         + r"(?=/|[\s`'\"<>)\],:;]|[.!?](?=\s|$)|$)")
    return pattern.sub("~", text)
