#!/usr/bin/env python3
"""Quarterdeck — macOS App Entry Point."""
import os
import sys
import threading
import time
from pathlib import Path

import uvicorn


def backend_port() -> int:
    sys.path.insert(0, str(Path(__file__).parent))
    from backend.config import PORT
    return PORT


# How long to keep trying the bind before calling it a real clash. Quitting the
# app and launching it again releases the old socket in far less than this; a
# holder still there afterwards is a genuine second copy, not a handover.
PORT_CLAIM_TIMEOUT_S = 1.5
PORT_CLAIM_INTERVAL_S = 0.1


def claim_port(port: int) -> str:
    """Bind the port before uvicorn does, so a clash is caught while it can be
    explained. Returns "" on success, or a description of who holds it.

    Without this the failure was silent and actively misleading: uvicorn logged
    "address already in use" into a thread nobody watched, the backend thread
    died, and wait_for_backend() then succeeded against whatever *else* was
    listening. The window opened, looked fine, and showed a different Quarterdeck's
    state — so an installed app and a dev checkout quietly swapped places.

    The probe has to test what uvicorn will test, and briefly wait out what
    uvicorn would not have minded. Relaunching straight after a quit used to
    fail here and name whichever unrelated process happened to be on the port.
    """
    import socket
    deadline = time.monotonic() + PORT_CLAIM_TIMEOUT_S
    while True:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # uvicorn sets this before it binds (config.py), so a probe without it
        # is stricter than the server it is guarding: connections left in
        # TIME_WAIT by the copy you just quit fail this bind while uvicorn
        # would have taken the port without complaint. That is a refusal to
        # start with nothing actually in the way.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
            return ""
        except OSError:
            if time.monotonic() >= deadline:
                return holder_of(port)
            time.sleep(PORT_CLAIM_INTERVAL_S)
        finally:
            probe.close()


def blocks_loopback(addr: str) -> bool:
    """Whether a listener on this address is what a 127.0.0.1 bind collides with.

    lsof renders a wildcard bind as `*:19418`. Everything else with some other
    host part — the Tailscale address, a LAN address — is listening on a
    different interface and cannot block loopback, however much it looks like it
    is "on the port". Quarterdeck runs into this every time: the remote listener
    binds the tailnet address and outlives the GUI, so it is sitting on 19418
    while contending for nothing.
    """
    host = addr.rsplit(":", 1)[0]
    # `[::]` is dual-stack on macOS and does take v4 connections with it.
    return host in ("*", "0.0.0.0", "127.0.0.1", "localhost", "[::]")


def holder_of(port: int) -> str:
    """Who is listening on this port, in words, so the fix is obvious.

    The lsof call is deliberately not filtered by address. `lsof
    -iTCP@127.0.0.1:<port>` matches only listeners bound to that exact address,
    so a holder on `0.0.0.0` was invisible to it while still blocking the bind
    `claim_port` attempts. The alert then said "an unidentified process", which
    is the failure this whole path exists to prevent.

    Reading them all does mean the innocent get listed too, and naming one of
    those as the cause is its own kind of wrong answer. So they are separated:
    a holder that could have blocked the bind is reported as the holder, and one
    that could not is named as what it is.
    """
    import subprocess
    try:
        r = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"],
                           capture_output=True, text=True, timeout=3)
        # Command and pid per holder, plus the address, because two Quarterdecks
        # on one port differ only by which address they bound.
        blocking, elsewhere = [], []
        for line in r.stdout.strip().split("\n")[1:]:
            fields = line.split()
            if len(fields) >= 9:
                (blocking if blocks_loopback(fields[8]) else elsewhere).append(
                    f"{fields[0]} {fields[1]} ({fields[8]})")
            elif len(fields) >= 2:
                # No address column to judge by, so assume it is the culprit
                # rather than clear it.
                blocking.append(f"{fields[0]} {fields[1]}")
        if blocking:
            return "; ".join(blocking)
        if elsewhere:
            # The bind failed and yet nothing is on loopback: the port was still
            # being released while we looked. Say that, instead of handing the
            # user a pid that was never in the way.
            return ("; ".join(elsewhere) + " — bound to another address, so not "
                    "what blocked it; loopback was most likely still being "
                    "released. Try again")
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        # lsof missing is worth saying: a Finder launch gets a minimal PATH, and
        # "unidentified" would otherwise be blamed on the holder.
        return "a process lsof could not identify"
    return "an unidentified process"


def activate_running_instance() -> bool:
    """Bring an already-running Quarterdeck to the front. True if one was found.

    macOS relaunches a bundle as a *new process* rather than reactivating the old
    one, so double-clicking Quarterdeck while it is already running starts a
    second copy that finds the port taken and exits. From Finder that is
    indisputably "the app does not start": the refusal goes to stderr, which
    nobody sees.
    """
    try:
        from AppKit import NSRunningApplication
        from Foundation import NSBundle
    except ImportError:
        return False  # not macOS, or PyObjC missing
    bundle_id = NSBundle.mainBundle().bundleIdentifier()
    if not bundle_id:
        return False  # a plain `python app.py` has no bundle to look up
    mine = os.getpid()
    others = [a for a in
              NSRunningApplication.runningApplicationsWithBundleIdentifier_(bundle_id)
              if a.processIdentifier() != mine]
    if not others:
        return False
    # 1 = all windows, 2 = ignore other apps. Named constants moved around
    # between PyObjC versions; the values did not.
    others[0].activateWithOptions_(1 | 2)
    return True


def show_alert(title: str, message: str) -> bool:
    """Put a message where a Finder-launched app's user will actually see it."""
    try:
        from AppKit import NSAlert, NSApplication
    except ImportError:
        return False
    try:
        NSApplication.sharedApplication()
        alert = NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        alert.runModal()
    except Exception:
        return False
    return True


def handle_port_clash(clash: str, port: int) -> int:
    """What to do when something already holds our port. Returns an exit code.

    Preferring activation over an error is the whole point: the common case is
    not a misconfiguration, it is the user asking for the app they already have.
    """
    dev = " (dev)" if os.environ.get("DECK_DEV") else ""
    detail = (f"Port {port}{dev} is already held by {clash}.\n"
              f"Quit the other Quarterdeck, or start this one on a different port "
              f"with DECK_PORT=<n>.")
    print(f"[deck] {detail} Refusing to start rather than attach to the other "
          f"one's backend.".replace("\n", " "), file=sys.stderr)
    if activate_running_instance():
        print("[deck] brought the running Quarterdeck to the front instead",
              file=sys.stderr)
        return 0
    # Not another copy of this app — someone else's process on our port, which
    # needs saying out loud rather than dying quietly.
    show_alert("Quarterdeck is already running", detail)
    return 1


_uvicorn_server: "uvicorn.Server | None" = None


def start_backend(port: int):
    """Run FastAPI backend in a thread, keeping a handle for graceful shutdown."""
    global _uvicorn_server
    project_root = Path(__file__).parent
    sys.path.insert(0, str(project_root))
    from backend.api import app
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    _uvicorn_server = uvicorn.Server(config)
    _uvicorn_server.run()


def stop_backend():
    """Signal uvicorn to shut down gracefully so the socket is released."""
    if _uvicorn_server is not None:
        _uvicorn_server.should_exit = True


def preflight() -> list[str]:
    """Check the external tools the app depends on, after fixing PATH.

    A Finder-launched bundle does not inherit the shell PATH, so tmux and
    kiro-cli are invisible unless their directories are added back.
    """
    import shutil
    sys.path.insert(0, str(Path(__file__).parent))
    from backend.config import ensure_tool_path

    ensure_tool_path()
    missing = [tool for tool in ("tmux", "kiro-cli") if not shutil.which(tool)]
    return missing


def wait_for_backend(port, timeout=10):
    """Wait until backend is responsive.

    Polls /app/ rather than /api/sessions: /api/sessions now requires the local
    token, and the token has not been injected into the webview yet at this
    point. /app/ serves static assets, which are explicitly exempt from the
    local-token check so the webview can bootstrap.
    """
    import urllib.request
    start = time.time()
    while time.time() - start < timeout:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/app/")
            return True
        except Exception:
            time.sleep(0.2)
    return False


EDIT_MENU_ITEMS = (
    ("Undo", "undo:", "z"),
    ("Redo", "redo:", "Z"),
    (None, None, None),
    ("Cut", "cut:", "x"),
    ("Copy", "copy:", "c"),
    ("Paste", "paste:", "v"),
    ("Select All", "selectAll:", "a"),
)


def install_edit_menu():
    """Give the packaged app an Edit menu, so Cmd-C works in the panes.

    WKWebView already implements `copy:` / `selectAll:` — the problem is that
    pywebview ships no Edit menu, so on macOS the shortcut has no menu item to
    route through and simply does nothing. Text in the panes could be selected
    but never copied, which is most of what anyone wants to do with a terminal
    pane. Actions carry no target: sending them to nil walks the responder
    chain and lands on whatever is focused, which is the web view.

    Runs after webview.start() has built its own menu bar; installing earlier
    would be overwritten.

    webview.start(func) calls func on a worker thread, and AppKit refuses menu
    changes off the main thread — "API misuse: setting the main menu on a
    non-main thread". That raised on every launch, was caught below, printed,
    and left the app without the Edit menu it went to this trouble for. So the
    work is handed to the main queue instead of being done where we stand.
    """
    try:
        from AppKit import NSApplication, NSMenu, NSMenuItem
        from Foundation import NSOperationQueue
    except ImportError:
        return  # not macOS, or PyObjC missing — the app simply keeps its menus

    def build():
        app = NSApplication.sharedApplication()
        main_menu = app.mainMenu()
        if main_menu is None:
            main_menu = NSMenu.alloc().init()
            app.setMainMenu_(main_menu)

        for i in range(main_menu.numberOfItems()):
            if main_menu.itemAtIndex_(i).title() == "Edit":
                return  # a future pywebview may add one; do not double up

        edit_menu = NSMenu.alloc().initWithTitle_("Edit")
        for title, selector, key in EDIT_MENU_ITEMS:
            if title is None:
                edit_menu.addItem_(NSMenuItem.separatorItem())
                continue
            edit_menu.addItem_(
                NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    title, selector, key)
            )

        holder = NSMenuItem.alloc().init()
        holder.setTitle_("Edit")
        holder.setSubmenu_(edit_menu)
        main_menu.addItem_(holder)

    def guarded():
        try:
            build()
        except Exception as exc:
            # A missing menu is a degraded app, not a broken one — say so, go on.
            print(f"[deck] could not install the Edit menu: {exc}", file=sys.stderr)

    NSOperationQueue.mainQueue().addOperationWithBlock_(guarded)


_launch_target = None  # module-level reference prevents PyObjC GC


def install_session_menu(window):
    """Add a Session menu with New Session (⌘⇧L).

    WKWebView does not reliably forward ⌘⇧L to JavaScript window event
    listeners. A native NSMenuItem with the same key equivalent fires the
    action through the macOS responder chain, bypassing the webview intercept.
    The action evaluates JS directly into the webview window.
    """
    try:
        from AppKit import NSApplication, NSMenu, NSMenuItem
        from Foundation import NSOperationQueue, NSObject
        import objc
    except ImportError:
        return

    def build():
        try:
            ns_app = NSApplication.sharedApplication()
            main_menu = ns_app.mainMenu()
            if main_menu is None:
                return

            # Don't add twice
            for i in range(main_menu.numberOfItems()):
                if main_menu.itemAtIndex_(i).title() == "Session":
                    return

            # Target object that evaluates JS on the webview
            class _LaunchTarget(NSObject):
                def openLauncher_(self, sender):
                    try:
                        window.evaluate_js(
                            "window.dispatchEvent(new CustomEvent('quarterdeck:open-launcher'))"
                        )
                    except Exception:
                        pass

            target = _LaunchTarget.alloc().init()
            global _launch_target
            _launch_target = target  # prevent GC

            session_menu = NSMenu.alloc().initWithTitle_("Session")
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "New Session", "openLauncher:", "n"
            )
            item.setKeyEquivalentModifierMask_(1 << 20 | 1 << 17)  # ⌘⇧
            item.setTarget_(target)
            session_menu.addItem_(item)

            holder = NSMenuItem.alloc().init()
            holder.setTitle_("Session")
            holder.setSubmenu_(session_menu)
            # Insert after Edit menu
            main_menu.addItem_(holder)
        except Exception as exc:
            print(f"[deck] could not install Session menu: {exc}", file=__import__('sys').stderr)

    try:
        from Foundation import NSOperationQueue
        NSOperationQueue.mainQueue().addOperationWithBlock_(build)
    except Exception:
        pass


def main():
    missing = preflight()
    if missing:
        # Say so plainly rather than letting every action fail one by one.
        print(f"[deck] missing required tools: {', '.join(missing)}", file=sys.stderr)

    port = backend_port()
    clash = claim_port(port)
    if clash:
        sys.exit(handle_port_clash(clash, port))

    backend_thread = threading.Thread(target=start_backend, args=(port,), daemon=True)
    backend_thread.start()

    if not wait_for_backend(port):
        print(f"[deck] backend did not come up on port {port}", file=sys.stderr)
        sys.exit(1)

    # Pre-warm the macOS keychain and cache the local token. Both operations
    # happen here — before the window opens — so any permission dialog fires
    # in the foreground rather than inside a request handler where the frontend
    # would see a timeout ("Backend unreachable").
    from backend.auth import ensure_local_token, _keychain_read, _KC_REMOTE_ACCOUNT
    local_token = ensure_local_token()   # sets module-level cache; also pre-warms local-token
    _keychain_read(_KC_REMOTE_ACCOUNT)   # trigger dialog for remote-token if not yet granted

    # Set dock icon
    icon_path = Path(__file__).parent / "icon.icns"
    if icon_path.exists():
        try:
            from AppKit import NSApplication, NSImage
            app = NSApplication.sharedApplication()
            icon = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
            if icon:
                app.setApplicationIconImage_(icon)
        except Exception:
            pass

    if os.environ.get("DEV"):
        url = "http://127.0.0.1:5173"
    else:
        url = f"http://127.0.0.1:{port}/app/"

    import webview
    # text_select defaults to False, which is why pane output could be read in a
    # browser but never selected or copied in the packaged app. This is a
    # terminal viewer; selecting its output is the point.
    window = webview.create_window("Quarterdeck", url, width=1200, height=800,
                          min_size=(600, 400), text_select=True)
    # private_mode defaults to True, which is an incognito window: WKWebView
    # discards localStorage and cookies when the process exits. Every preference
    # the app stores per-device — pane theme, current view, both filters, the
    # composer's arrow-up history, the /goal cap — was written correctly and
    # thrown away on quit. The settings were never broken; the browser they live
    # in was told to forget.
    #
    # storage_path is deliberately not passed: pywebview's Cocoa backend never
    # reads it (only the GTK, Qt and Edge backends do), so setting it created an
    # empty ~/.osa-kiro/webview directory and implied a guarantee it was not
    # making. On macOS the data lands in the default WKWebsiteDataStore, under
    # ~/Library/WebKit/<bundle identifier>.
    #
    # It is keyed by origin, so preferences do not carry across a port change.
    # The installed app is pinned to DEFAULT_PORT, so this only bites a dev
    # window, where a reset is not a loss.
    def install_menus():
        install_edit_menu()
        install_session_menu(window)

    # local_token was set during keychain pre-warm above; reused here for injection.
    def inject_local_token():
        """Patch window.fetch and XMLHttpRequest so every API request carries X-Local-Token.

        Called once after the page loads. The token value is baked in at
        injection time — it never changes unless manually deleted.

        All methods are patched (not just mutating ones) because the backend now
        requires the token on GET requests too — this closes the loopback bypass
        that previously let any local process read sessions, transcripts, and
        settings without authentication.
        """
        import json as _json
        token_literal = _json.dumps(local_token)
        script = f"""
(function() {{
  const _localToken = {token_literal};
  const _origFetch = window.fetch.bind(window);
  window.fetch = function(input, init) {{
    init = init ? Object.assign({{}}, init) : {{}};
    const headers = new Headers(init.headers || {{}});
    if (!headers.has('X-Local-Token')) headers.set('X-Local-Token', _localToken);
    init.headers = headers;
    return _origFetch(input, init);
  }};
  // Also patch XMLHttpRequest for any code that bypasses fetch.
  const _origOpen = XMLHttpRequest.prototype.open;
  const _origSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) {{
    this._xltPatched = true;
    return _origOpen.call(this, method, url, ...rest);
  }};
  const _origSetHeader = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.setRequestHeader = function(name, value) {{
    if (name.toLowerCase() === 'x-local-token') this._xltSet = true;
    return _origSetHeader.call(this, name, value);
  }};
  XMLHttpRequest.prototype.send = function(...args) {{
    if (this._xltPatched && !this._xltSet) {{
      _origSetHeader.call(this, 'X-Local-Token', _localToken);
    }}
    return _origSend.apply(this, args);
  }};
}})();
"""
        try:
            window.evaluate_js(script)
        except Exception:
            pass

    window.events.loaded += inject_local_token

    webview.start(install_menus, private_mode=False)

    # Window closed — shut down the backend gracefully so the socket is released
    # immediately instead of lingering in TIME_WAIT for minutes.
    stop_backend()
    backend_thread.join(timeout=3)


if __name__ == "__main__":
    main()
