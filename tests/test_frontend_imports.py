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
import pytest
from pathlib import Path

FRONTEND_SRC = Path(__file__).parent.parent / "frontend" / "src"
COMPONENTS_DIR = FRONTEND_SRC / "components"
UTILS_FILE = FRONTEND_SRC / "utils.js"

# React hooks that components commonly use
REACT_HOOKS = [
    "useState", "useEffect", "useLayoutEffect", "useCallback",
    "useRef", "useMemo", "useContext", "useReducer",
    "useImperativeHandle",
]

# React non-hook symbols that also require explicit import from 'react'
REACT_NAMED_EXPORTS = ["forwardRef", "createContext"]
# Symbols from 'react-dom' that require explicit import
REACT_DOM_NAMED_EXPORTS = ["createPortal"]

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

    def _react_imported_symbols(self, text: str) -> set[str]:
        """Symbols declared in any React or react-dom import line."""
        symbols: set[str] = set()
        for pattern in [
            r"import React,?\s*\{([^}]+)\}\s+from\s+'react'",
            r"import\s*\{([^}]+)\}\s+from\s+'react'",
            r"import\s*\{([^}]+)\}\s+from\s+'react-dom'",
        ]:
            for m in re.finditer(pattern, text):
                symbols.update(s.strip() for s in m.group(1).split(","))
        return symbols

    def _hooks_used(self, text: str) -> set[str]:
        """Hooks that appear as function calls in the source."""
        used = set()
        for hook in REACT_HOOKS:
            if re.search(rf"\b{hook}\s*\(", text):
                used.add(hook)
        return used

    def test_no_missing_hook_imports(self):
        missing = {}
        for path in component_files():
            text = path.read_text()
            imported = self._react_imported_symbols(text)
            used = self._hooks_used(text)
            gap = used - imported
            if gap:
                missing[path.name] = sorted(gap)

        assert not missing, (
            "React hooks used but not imported:\n" +
            "\n".join(f"  {f}: {', '.join(hooks)}" for f, hooks in missing.items())
        )

    def test_no_missing_react_named_exports(self):
        """forwardRef, createPortal, createContext must be imported when used."""
        missing = {}
        all_react_symbols = REACT_NAMED_EXPORTS + REACT_DOM_NAMED_EXPORTS
        for path in component_files():
            text = path.read_text()
            imported = self._react_imported_symbols(text)
            gap = set()
            for sym in all_react_symbols:
                if re.search(rf"\b{sym}\s*[\(\<]", text) and sym not in imported:
                    gap.add(sym)
            if gap:
                missing[path.name] = sorted(gap)

        assert not missing, (
            "React named exports used but not imported:\n" +
            "\n".join(f"  {f}: {', '.join(syms)}" for f, syms in missing.items())
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


class TestExtractionIntegrity:
    """Verify extracted components are correctly wired after refactors.

    These tests are the safety net for the SideChat / CLI / future extractions.
    Each check answers: "did the extraction leave the parent consistent?"
    """

    DETAIL = COMPONENTS_DIR / "DetailPanel.jsx"
    SIDE_CHAT = COMPONENTS_DIR / "SideChat.jsx"

    def test_sidechat_file_exists(self):
        assert self.SIDE_CHAT.exists(), "SideChat.jsx is missing"

    def test_detail_imports_sidechat(self):
        text = self.DETAIL.read_text()
        assert "import SideChat" in text, \
            "DetailPanel.jsx does not import SideChat"

    def test_detail_renders_sidechat(self):
        text = self.DETAIL.read_text()
        assert "<SideChat" in text, \
            "DetailPanel.jsx does not render <SideChat>"

    def test_detail_has_no_orphan_sidechat_state(self):
        """State vars that lived only in the old inline side chat must be gone."""
        text = self.DETAIL.read_text()
        orphans = [
            "sideChatOpen", "sideChatLines", "sideChatThinking",
            "sideChatDraft", "sideChatHistory", "sideChatHistoryIdx",
            "sideChatOpening", "sideChatChipsOpen",
            "sideChatPollRef", "sideChatBottomRef",
        ]
        found = [v for v in orphans if v in text]
        assert not found, (
            f"DetailPanel.jsx still references extracted state vars: {found}"
        )

    def test_sidechat_uses_forwardref(self):
        text = self.SIDE_CHAT.read_text()
        assert "forwardRef" in text, \
            "SideChat.jsx does not use forwardRef"

    def test_sidechat_exports_default(self):
        text = self.SIDE_CHAT.read_text()
        assert "export default SideChat" in text, \
            "SideChat.jsx has no default export"

    def test_sidechat_imports_paste_attachments(self):
        """SideChat has its own paste attachment handling — must import the hook."""
        text = self.SIDE_CHAT.read_text()
        assert "usePasteAttachments" in text, \
            "SideChat.jsx does not import usePasteAttachments"

    def test_detail_no_sidechat_jsxblock(self):
        """The old inline side-chat-panel JSX must not remain in DetailPanel."""
        text = self.DETAIL.read_text()
        # The panel div only appears in SideChat now
        assert text.count('className="side-chat-panel"') == 0, \
            'DetailPanel.jsx still contains inline side-chat-panel div'

    def test_sidechat_contains_panel(self):
        text = self.SIDE_CHAT.read_text()
        assert 'side-chat-panel' in text, \
            "SideChat.jsx is missing the side-chat-panel div"


class TestComponentPropsConsistency:
    """Cross-file prop contract checks — if a component's interface changes,
    the caller in DetailPanel must change with it."""

    DETAIL = COMPONENTS_DIR / "DetailPanel.jsx"
    SIDE_CHAT = COMPONENTS_DIR / "SideChat.jsx"

    def _props_passed_to(self, component_tag: str, caller_text: str) -> set[str]:
        """Extract prop names passed to a JSX component tag in caller_text."""
        # Find the component tag and grab all prop=... attributes until />
        m = re.search(
            rf"<{component_tag}\s+((?:[^>]|\n)*?)(?:/>|>)",
            caller_text,
        )
        if not m:
            return set()
        attr_block = m.group(1)
        return set(re.findall(r"(\w+)=", attr_block))

    def test_sidechat_receives_required_props(self):
        """DetailPanel must pass the four props SideChat's signature requires."""
        detail_text = (COMPONENTS_DIR / "DetailPanel.jsx").read_text()
        passed = self._props_passed_to("SideChat", detail_text)
        required = {"sessionId", "notify", "respond", "runCommand", "options"}
        missing = required - passed
        assert not missing, (
            f"DetailPanel does not pass required props to <SideChat>: {missing}"
        )


class TestCLIHookExtraction:
    """Verify useCLI hook is correctly extracted and wired."""

    DETAIL = COMPONENTS_DIR / "DetailPanel.jsx"
    HOOKS_DIR = COMPONENTS_DIR.parent / "hooks"
    CLI_HOOK = HOOKS_DIR / "useCLI.js"

    def test_usecli_hook_exists(self):
        assert self.CLI_HOOK.exists(), "hooks/useCLI.js is missing"

    def test_detail_imports_usecli(self):
        text = self.DETAIL.read_text()
        assert "useCLI" in text, "DetailPanel.jsx does not import useCLI"

    def test_detail_has_no_orphan_cli_state(self):
        """CLI state vars that moved to the hook must not remain in DetailPanel."""
        text = self.DETAIL.read_text()
        orphans = [
            "setCliStatus", "setCliInstances",
            "setCliSendMode", "setCliBindOpen",
        ]
        # Allow the destructured names (cliStatus, cliSendMode etc.) — only the
        # setter declarations are gone. Check for useState declarations only.
        found = []
        for v in orphans:
            import re as _re
            if _re.search(rf"useState.*{v}|{v}.*useState", text):
                found.append(v)
        assert not found, (
            f"DetailPanel.jsx still declares CLI state via useState: {found}"
        )

    def test_usecli_exports_hook(self):
        text = self.CLI_HOOK.read_text()
        assert "export function useCLI" in text, \
            "useCLI.js does not export useCLI"

    def test_usecli_returns_expected_keys(self):
        """Hook must return the keys DetailPanel destructures."""
        text = self.CLI_HOOK.read_text()
        required = [
            "cliStatus", "cliSendMode", "setCLISendMode",
            "openCliBinder", "cliBindOpen", "setCliBindOpen",
            "cliInstances", "bindCli", "unbindCli",
        ]
        missing = [k for k in required if k not in text]
        assert not missing, (
            f"useCLI.js is missing expected return keys: {missing}"
        )

    def test_detail_uses_hook_destructure(self):
        """DetailPanel must destructure from useCLI, not declare its own state."""
        text = self.DETAIL.read_text()
        assert "useCLI(" in text, \
            "DetailPanel.jsx does not call useCLI()"


class TestMarkdownPathDetection:
    """Verify isFilesystemPath logic in Markdown.jsx correctly classifies paths.

    We extract the logic into Python to unit-test it without a browser.
    The regex patterns must match what's in Markdown.jsx exactly.
    """

    # Mirror the JS logic in Python for testing
    FILE_EXTS = re.compile(
        r'\.(md|txt|json|yaml|yml|py|js|jsx|ts|tsx|sh|toml|csv|html|xml|log|pdf|docx|xlsx)$',
        re.IGNORECASE
    )
    API_PATH_RE = re.compile(
        r'^/(api|app|auth|static|assets|public|login|logout|health)/',
        re.IGNORECASE
    )
    FS_SEGMENT_RE = re.compile(
        r'^(Users|home|var|etc|tmp|usr|opt|Applications|Library|Documents|Desktop|Downloads|Projects|src|backend|frontend|build|dist)\b',
        re.IGNORECASE
    )

    def _is_filesystem_path(self, path: str) -> bool:
        if not path:
            return False
        if self.API_PATH_RE.match(path):
            return False
        if re.match(r'^/\w+:', path):
            return False
        depth = path.count('/')
        if self.FILE_EXTS.search(path):
            return True
        if depth < 2:
            return False
        segments = [s for s in path.split('/') if s]
        return any(self.FS_SEGMENT_RE.match(s) for s in segments) or path.startswith('~/')

    @pytest.mark.parametrize("path", [
        '/api/deny-patterns',
        '/api/sessions',
        '/api/sessions/abc/restart-here',
        '/api/deny-patterns/delete/123',
        '/app/index.html',
        '/auth/login',
        '/static/js/bundle.js',
        '/health/check',
        '/login',
    ])
    def test_api_routes_not_detected_as_files(self, path):
        assert not self._is_filesystem_path(path), \
            f"{path!r} should NOT be detected as a filesystem path"

    @pytest.mark.parametrize("path", [
        '/Users/a.vidanov/Documents/PROJECTS/test.py',
        '~/Documents/myfile.md',
        '/home/user/projects/backend/api.py',
        '/var/log/system.log',
        '/Users/a.vidanov/Documents/Obsidian Vault/Notes/note.md',
        '/tmp/kiro-charts/output.png',
        '/usr/local/bin/python3',
        '/Applications/Quarterdeck.app',
        '/etc/hosts',
        '/opt/homebrew/bin/python',
    ])
    def test_real_paths_detected_as_files(self, path):
        assert self._is_filesystem_path(path), \
            f"{path!r} SHOULD be detected as a filesystem path"

    def test_markdown_jsx_contains_api_exclusion(self):
        """The API exclusion pattern must be present in Markdown.jsx."""
        markdown = (COMPONENTS_DIR / "Markdown.jsx").read_text()
        assert "API_PATH_RE" in markdown, \
            "Markdown.jsx is missing the API_PATH_RE exclusion pattern"

    def test_markdown_jsx_filechip_accepts_label_prop(self):
        """FileChip must accept a label prop so [label](path) keeps its label."""
        markdown = (COMPONENTS_DIR / "Markdown.jsx").read_text()
        assert "label" in markdown, \
            "FileChip does not accept a label prop — [label](path) will lose its label"

    def test_markdown_jsx_non_file_link_renders_label(self):
        """[label](/api/path) must fall through to render the label, not discard it."""
        markdown = (COMPONENTS_DIR / "Markdown.jsx").read_text()
        # Should have a span/text fallback for non-file, non-http links
        assert "<span key={key}>{label}</span>" in markdown or \
               "out.push(<span" in markdown, \
            "Non-file markdown links do not render their label text"
