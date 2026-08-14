"""Frontend import completeness tests.

These tests catch the class of bug that caused two white-screen crashes:
- A React hook (useState, useEffect, useMemo, etc.) used in a component
  file but not imported from 'react'.
- A symbol from utils.js (STATUS_CONFIG, timeAgo, etc.) used in a
  component file but not imported from '../utils'.

The checks are intentionally simple: grep the source for usage, grep the
imports for the declaration. False positives are possible (e.g. a symbol
used in a comment) but false negatives are the dangerous case and this
catches them reliably.

Run with: python -m pytest tests/test_frontend_imports.py
"""
import re
from pathlib import Path

FRONTEND_SRC = Path(__file__).parent.parent / "frontend" / "src"
COMPONENTS_DIR = FRONTEND_SRC / "components"
UTILS_FILE = FRONTEND_SRC / "utils.js"

# React hooks that components commonly use
REACT_HOOKS = [
    "useState", "useEffect", "useLayoutEffect", "useCallback",
    "useRef", "useMemo", "useContext", "useReducer",
]

# Symbols exported from utils.js (detected dynamically)
def utils_exports() -> set[str]:
    text = UTILS_FILE.read_text()
    return set(re.findall(r"^export (?:const|function|class) (\w+)", text, re.MULTILINE))


def component_files() -> list[Path]:
    return [f for f in COMPONENTS_DIR.glob("*.jsx") if f.name != "__tests__"]


def imports_in_file(text: str) -> set[str]:
    """Extract every imported symbol name from the file's import statements."""
    symbols: set[str] = set()
    for m in re.finditer(r"^import\s+(?:\*\s+as\s+(\w+)|(\w+)|{([^}]+)})", text, re.MULTILINE):
        if m.group(1):
            symbols.add(m.group(1))
        if m.group(2):
            symbols.add(m.group(2))
        if m.group(3):
            symbols.update(s.strip().split(" as ")[-1] for s in m.group(3).split(","))
    return symbols


def locally_defined(text: str) -> set[str]:
    """Symbols defined locally in the file (const/function/class at top level)."""
    symbols: set[str] = set()
    for m in re.finditer(r"^(?:const|function|class|let|var)\s+(\w+)", text, re.MULTILINE):
        symbols.add(m.group(1))
    return symbols


class TestReactHookImports:
    """Every React hook used in a component must be imported from 'react'."""

    def _react_imported_hooks(self, text: str) -> set[str]:
        """Hooks declared in the React import line."""
        m = re.search(r"import React,?\s*\{([^}]+)\}\s+from\s+'react'", text)
        if not m:
            return set()
        return {s.strip() for s in m.group(1).split(",")}

    def _hooks_used(self, text: str) -> set[str]:
        """Hooks that appear as function calls in the source."""
        used = set()
        for hook in REACT_HOOKS:
            # Match hook( but not inside a string or comment — good enough
            if re.search(rf"\b{hook}\s*\(", text):
                used.add(hook)
        return used

    def test_no_missing_hook_imports(self):
        missing = {}
        for path in component_files():
            text = path.read_text()
            imported = self._react_imported_hooks(text)
            used = self._hooks_used(text)
            gap = used - imported
            if gap:
                missing[path.name] = sorted(gap)

        assert not missing, (
            "React hooks used but not imported:\n" +
            "\n".join(f"  {f}: {', '.join(hooks)}" for f, hooks in missing.items())
        )


class TestUtilsImports:
    """Every symbol from utils.js used in a component must be imported."""

    def test_no_missing_utils_imports(self):
        exports = utils_exports()
        missing = {}

        for path in component_files():
            text = path.read_text()
            imported = imports_in_file(text)
            defined = locally_defined(text)
            available = imported | defined

            # Which exported utils symbols are used but not available?
            gap = set()
            for sym in exports:
                if re.search(rf"\b{sym}\b", text) and sym not in available:
                    gap.add(sym)

            if gap:
                missing[path.name] = sorted(gap)

        assert not missing, (
            "Symbols from utils.js used but not imported:\n" +
            "\n".join(f"  {f}: {', '.join(syms)}" for f, syms in missing.items())
        )


class TestNoDuplicateImports:
    """No symbol should be imported twice in the same file (causes build errors)."""

    def test_no_duplicate_import_symbols(self):
        duplicates = {}
        for path in component_files():
            text = path.read_text()
            # Find all import {...} blocks and check for repeated names
            all_symbols = []
            for m in re.finditer(r"^import\s+\{([^}]+)\}", text, re.MULTILINE):
                for s in m.group(1).split(","):
                    name = s.strip().split(" as ")[-1].strip()
                    if name:
                        all_symbols.append(name)
            seen = set()
            dupes = set()
            for sym in all_symbols:
                if sym in seen:
                    dupes.add(sym)
                seen.add(sym)
            if dupes:
                duplicates[path.name] = sorted(dupes)

        assert not duplicates, (
            "Symbols imported more than once:\n" +
            "\n".join(f"  {f}: {', '.join(dupes)}" for f, dupes in duplicates.items())
        )


class TestBuildPasses:
    """The Vite build must succeed with zero errors."""

    def test_vite_build_succeeds(self):
        import subprocess
        result = subprocess.run(
            ["npm", "run", "build"],
            cwd=str(FRONTEND_SRC.parent),
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, (
            "Vite build failed:\n" + result.stdout[-2000:] + result.stderr[-1000:]
        )
