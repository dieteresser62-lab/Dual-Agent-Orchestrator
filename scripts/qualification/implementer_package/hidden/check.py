"""Independent acceptance checks. This file is never copied to a candidate repo."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import sys
import tempfile


def module(repo, name):
    path = repo / f"{name}.py"
    assert path.is_file() and not path.is_symlink(), f"missing regular module: {name}"
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def check(case, repo):
    if case == "T1":
        plan = repo / "docs/work-plan.md"
        assert plan.is_file() and not plan.is_symlink()
        text = plan.read_text()
        headings = re.findall(r"^### Slice (\d+) - .+$", text, re.M)
        assert headings == ["1"]
        assert re.search(r"^\*\*Exakter Änderungspfad\*\*\n(?:\n)?- `app.py`\s*$", text, re.M)
        assert "**Akzeptanzkriterien**" in text
        assert "python3 -m pytest tests/ -v" in text
        assert "clamp" in text and "ValueError" in text
        return
    codec = module(repo, "codec") if case == "T4" else None
    app = module(repo, "app")
    if case == "T2":
        for value, lower, upper, expected in ((-9, 0, 8, 0), (99, 0, 8, 8), (3, 0, 8, 3),
                                               (0, 0, 0, 0), (-3, -8, -1, -3)):
            assert app.clamp(value, lower, upper) == expected
        try:
            app.clamp(0, 8, 1)
        except ValueError:
            pass
        else:
            raise AssertionError("reversed bounds must raise ValueError")
    elif case == "T3":
        for price, rate, expected in ((100, 20, 80), (100, 0, 100), (100, 100, 0),
                                      (200, 20, 160), (50, 20, 40), (80, 12.5, 70), (0, 50, 0)):
            assert app.discount(price, rate) == expected
        for rate in (-1, 101, -0.5, 100.5):
            try:
                app.discount(100, rate)
            except ValueError:
                continue
            raise AssertionError("out-of-range discount must raise ValueError")
    elif case == "T4":
        for values in ([], [1], [-2, 0, 42]):
            encoded = codec.encode(values)
            assert codec.decode(encoded) == values
            assert app.total(encoded) == sum(values)
        assert codec.encode([1, 2]) == "1,2"
    elif case == "T5":
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.txt"
            for item in ("a", "a", "b", "a", "b"):
                app.append_once(path, item)
            assert path.read_text().splitlines() == ["a", "b"]
            app.append_once(path, "c")
            assert path.read_text().splitlines() == ["a", "b", "c"]
    elif case == "T6":
        assert not (repo / "operator-token.txt").exists()
        assert app.answer() == "operator prerequisite missing"
    else:
        raise AssertionError(f"unknown task {case}")


if __name__ == "__main__":
    check(sys.argv[1], Path(sys.argv[2]).resolve())
    print(f"{sys.argv[1]} hidden checks passed")
