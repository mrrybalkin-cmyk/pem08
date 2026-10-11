"""Explicit visible Qt integration gate; no production config or live AI.

Run with the separate desktop Python: python -m tests.desktop_smoke.
Each scenario runs in a fresh process to preserve backend import isolation.
"""

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
from threading import Event, Thread
from time import monotonic
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / ".pytest-temp/stage13-desktop"


def select_windows_file(filename, cancel=False):
    """Exercise the real Windows picker belonging ONLY to this test process."""
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
    user.GetDlgItem.restype = wintypes.HWND
    user.GetParent.argtypes = [wintypes.HWND]
    user.GetParent.restype = wintypes.HWND
    dialogs = []
    def class_name(hwnd):
        name = ctypes.create_unicode_buffer(256)
        user.GetClassNameW(hwnd, name, 256)
        return name.value
    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == os.getpid() and user.IsWindowVisible(hwnd) and class_name(hwnd) == "#32770":
            dialogs.append(hwnd)
        return True
    user.EnumWindows(callback_type(visit), 0)
    for dialog in dialogs:
        if cancel:
            user.PostMessageW(dialog, 0x0010, 0, 0)
            return False
        edits = []
        def child(hwnd, _):
            if class_name(hwnd) == "Edit" and user.GetDlgCtrlID(hwnd) == 1148:
                edits.append(hwnd)
            return True
        user.EnumChildWindows(dialog, callback_type(child), 0)
        if edits:
            text = ctypes.create_unicode_buffer(str(filename))
            user.SendMessageW(edits[-1], 0x000C, 0, ctypes.cast(text, ctypes.c_void_p).value)
            user.PostMessageW(user.GetDlgItem(dialog, 1), 0x00F5, 0, 0)  # Click Open.
            return True
    return False


def fingerprint():
    roots = [ROOT / "data", ROOT / "uploads", ROOT / "screenshots", ROOT / ".env"]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "CompetitionMonitor")
    result = {}
    for root in roots:
        files = root.rglob("*") if root.is_dir() else [root]
        for path in files:
            if path.is_file():
                result[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def process_snapshot():
    """Windows process IDs/parents only; never command lines or credentials."""
    import ctypes
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD),
                    ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                    ("parent", wintypes.DWORD), ("priority", wintypes.LONG), ("flags", wintypes.DWORD),
                    ("name", wintypes.WCHAR * 260)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    assert handle != ctypes.c_void_p(-1).value
    try:
        entry = Entry()
        entry.size = ctypes.sizeof(entry)
        result = {}
        more = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while more:
            result[entry.pid] = (entry.parent, entry.name)
            more = kernel.Process32NextW(handle, ctypes.byref(entry))
        return result
    finally:
        kernel.CloseHandle(handle)


def record_descendants(root_pid, seen):
    snapshot = process_snapshot()
    family = {root_pid} | set(seen)
    changed = True
    while changed:
        changed = False
        for pid, (parent, name) in snapshot.items():
            if parent in family and pid not in family:
                family.add(pid)
                seen[pid] = name
                changed = True
    return snapshot


def assert_no_orphans(seen):
    deadline = monotonic() + 5
    wake = Event()
    while monotonic() < deadline:
        live = {pid: name for pid, name in seen.items() if pid in process_snapshot()}
        if not live:
            return
        wake.wait(.05)
    raise AssertionError(f"Orphan processes remain (not killed): {live}")


def entrypoint_smoke():
    """Launch the actual desktop.py with pythonw, different CWD, no console."""
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    for mode in ("valid", "missing", "blank"):
        with TemporaryDirectory(prefix="entrypoint-" + mode + "-", dir=ARTIFACTS) as directory:
            runtime = Path(directory) / "CompetitionMonitor"
            runtime.mkdir()
            if mode != "missing":
                (runtime / ".env").write_text("AI_API_KEY=" + ("offline-desktop-marker" if mode == "valid" else "") + "\nAI_BASE_URL=http://127.0.0.1:1/disabled\n", encoding="utf-8")
            env = os.environ.copy()
            env["LOCALAPPDATA"] = directory
            seen = {}
            process = subprocess.Popen([str(Path(sys.executable).with_name("pythonw.exe")), str(ROOT / "desktop.py")],
                                       cwd=ARTIFACTS, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = monotonic() + 30
            closed = False
            second_checked = False
            wake = Event()
            while monotonic() < deadline and process.poll() is None:
                snapshot = record_descendants(process.pid, seen)
                pids = {process.pid} | set(seen)
                windows = []
                def visit(hwnd, _):
                    pid = wintypes.DWORD()
                    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if pid.value in pids and user.IsWindowVisible(hwnd):
                        name = ctypes.create_unicode_buffer(256)
                        user.GetClassNameW(hwnd, name, 256)
                        windows.append((hwnd, name.value))
                    return True
                user.EnumWindows(callback_type(visit), 0)
                assert not any(name == "ConsoleWindowClass" for _hwnd, name in windows)
                log = runtime / "logs/competitionmonitor.log"
                ready = mode == "valid" and log.exists() and "frontend_loaded" in log.read_text(encoding="utf-8")
                if ready and not second_checked:
                    second_seen = {}
                    second = subprocess.Popen([str(Path(sys.executable).with_name("pythonw.exe")), str(ROOT / "desktop.py")],
                                              cwd=ARTIFACTS, env=env, creationflags=subprocess.CREATE_NO_WINDOW)
                    second_deadline = monotonic() + 15
                    while second.poll() is None and monotonic() < second_deadline:
                        record_descendants(second.pid, second_seen)
                        pids = {second.pid} | set(second_seen)
                        second_windows = []
                        # visit appends to windows; restrict it to the second process.
                        first_windows, windows = windows, second_windows
                        user.EnumWindows(callback_type(visit), 0)
                        windows = first_windows
                        for second_hwnd, second_name in second_windows:
                            if second_name.startswith("Qt"):
                                user.PostMessageW(second_hwnd, 0x0010, 0, 0)
                        wake.wait(.05)
                    assert second.wait(timeout=15) == 1
                    assert_no_orphans(second_seen)
                    assert process.poll() is None
                    assert log.read_text(encoding="utf-8").count("backend_start port=") == 1
                    second_checked = True
                for hwnd, name in windows:
                    if not closed and ((ready and name.startswith("Qt")) or (mode != "valid" and name.startswith("Qt"))):
                        user.PostMessageW(hwnd, 0x0010, 0, 0)
                        closed = True
                wake.wait(.05)
            assert closed, "Native window/dialog not observed"
            assert process.wait(timeout=90) == (0 if mode == "valid" else 1)
            assert_no_orphans(seen)
            if mode == "valid":
                assert (runtime / "data/app.db").is_file()
                text = log.read_text(encoding="utf-8")
                assert "Application shutdown complete" in text and "desktop_shutdown_complete" in text
                assert "offline-desktop-marker" not in text
            else:
                assert not (runtime / "data/app.db").exists()
            print(f"ACTUAL_ENTRYPOINT_{mode.upper()}_PASS; pythonw; DIFFERENT_CWD; orphan_processes=0")
            (ARTIFACTS / ("entrypoint-" + mode + ".json")).write_text(json.dumps({
                "mode": mode, "pass": True, "pythonw": True, "different_cwd": True,
                "orphan_processes": 0, "single_instance_checked": second_checked,
                "live_ai_calls": 0,
            }, indent=2), encoding="utf-8")


def child(mode):
    from PyQt6.QtCore import QEventLoop, QPoint, QTimer, Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QFileDialog
    from PyQt6.QtWebEngineCore import QWebEnginePage
    from desktop_runtime import BackendWorker, DesktopError, held_socket, load_application, prepare_runtime, runtime_paths
    from desktop import DesktopPage, DesktopWindow, acquire_lock

    application = QApplication(["desktop-smoke"])
    application.setQuitOnLastWindowClosed(False)
    errors, console_errors = [], []
    active_started, active_release, active_finished = Event(), Event(), Event()
    captures = []
    with TemporaryDirectory(prefix=mode + "-", dir=ARTIFACTS) as directory:
        paths = runtime_paths(Path(directory))
        paths.config.write_text("AI_API_KEY=offline-desktop-marker\nAI_PROVIDER=offline\nAI_BASE_URL=http://127.0.0.1:1/disabled\nAI_MODEL=offline\n", encoding="utf-8")
        lock = acquire_lock(paths)
        try:
            acquire_lock(paths)
        except DesktopError:
            pass
        else:
            raise AssertionError("Second desktop lock accepted")
        prepare_runtime(paths)

        def loader():
            nonlocal captures
            from unittest.mock import Mock
            from tests.browser_ui_smoke import fake_boundaries
            from backend.services import ai_service as ai_module
            # Real provider construction is forbidden, even on accidental use.
            ai_module.AsyncOpenAI = Mock(side_effect=AssertionError("LIVE AI forbidden"))
            if mode in {"workflows", "active"}:
                _png, captures = fake_boundaries()
            app = load_application()
            if mode == "active":
                from backend.services.ai_service import ai_service
                from tests.browser_ui_smoke import analysis_result
                async def long_analysis(_prepared):
                    active_started.set()
                    await asyncio.to_thread(active_release.wait)
                    active_finished.set()
                    return analysis_result()
                ai_service.analyze_source = long_analysis
            if mode == "failure":
                # Actual lifespan failure, not just a loader exception.
                from backend.config import settings
                occupied = paths.root / "occupied"
                occupied.write_text("not a directory")
                settings.screenshot_dir = occupied
            return app

        worker = BackendWorker(held_socket(), loader)
        class AuditPage(DesktopPage):
            def javaScriptConsoleMessage(self, level, message, line, source):
                if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
                    console_errors.append(message)
        window = DesktopWindow(worker, errors.append, AuditPage)
        worker.start()
        window.show()

        def wait(predicate, label, timeout=30):
            deadline = monotonic() + timeout
            while monotonic() < deadline:
                if predicate():
                    return
                loop = QEventLoop()
                QTimer.singleShot(25, loop.quit)
                loop.exec()
            raise AssertionError("Timeout: " + label)

        def js(code):
            results = []
            window.page.runJavaScript(code, results.append)
            wait(lambda: bool(results), "JavaScript callback", 5)
            return results[0]

        def until_js(code, label):
            wait(lambda: js(f"Boolean({code})"), label)

        def click(selector):
            rect = js(f"(() => {{const r=document.querySelector({json.dumps(selector)}).getBoundingClientRect();return [r.x+r.width/2,r.y+r.height/2]}})()")
            QTest.mouseClick(window.view.focusProxy(), Qt.MouseButton.LeftButton, pos=QPoint(int(rect[0]), int(rect[1])))

        def screenshot(name):
            window.raise_()
            window.activateWindow()
            js("window.__paintReady=false; requestAnimationFrame(()=>requestAnimationFrame(()=>window.__paintReady=true))")
            until_js("window.__paintReady", "compositor frames")
            assert window.grab().save(str(ARTIFACTS / name))

        try:
            if mode == "early-close":
                window.close()
            elif mode == "failure":
                wait(lambda: window.finished, "startup failure cleanup")
                assert errors and window.result_code == 1
            else:
                wait(lambda: window.loaded, "Qt frontend loaded")
                until_js("document.body.innerText.includes('Пока нет конкурентов')", "ES modules rendered")
                assert js("getComputedStyle(document.querySelector('.workspace')).display") == "grid"
                assert js("document.documentElement.scrollWidth <= innerWidth")
                if mode == "idle":
                    from PyQt6.QtGui import QDesktopServices
                    opened = []
                    original_open = QDesktopServices.openUrl
                    QDesktopServices.openUrl = lambda url: opened.append(url.toString()) or True
                    try:
                        for target in ("", "_blank"):
                            js("(() => {const a=document.createElement('a'); a.id='test-external'; a.href='https://example.invalid/'; a.target=" + json.dumps(target) + "; a.textContent='External'; a.style='position:fixed;top:10px;left:10px;z-index:99999';document.body.append(a)})()")
                            click("#test-external")
                            wait(lambda: len(opened) == (1 if not target else 2), "external navigation routed")
                            assert window.page.url().toString() == worker.origin + "/"
                            js("document.querySelector('#test-external').remove()")
                    finally:
                        QDesktopServices.openUrl = original_open

                if mode in {"workflows", "active"}:
                    click("#new-competitor")
                    until_js("document.querySelector('#editor-dialog').open", "HTML dialog")
                    js("document.querySelector('#field-name').value='Desktop test'; document.querySelector('#form-submit').click()")
                    until_js("document.querySelector('#competitor-name').textContent==='Desktop test'", "competitor created")
                    click("#add-source")
                    until_js("!!document.querySelector('#field-text')", "text form")
                    js("document.querySelector('#field-label').value='Text fixture'; document.querySelector('#field-text').value='Automated competitor source evidence for desktop test.'; document.querySelector('#form-submit').click()")
                    if mode == "active":
                        wait(active_started.is_set, "mock request started")
                        window.close()
                        assert worker.thread.is_alive() and not active_finished.is_set()
                        # Release by observed shutdown state, never an arbitrary sleep.
                        QTimer.singleShot(0, active_release.set)
                    else:
                        until_js("!document.querySelector('#editor-dialog').open && document.querySelector('.summary')", "text analyzed")
                        import pymupdf
                        from PIL import Image
                        image = paths.root / "fixture.png"
                        Image.new("RGB", (160, 100), "blue").save(image)
                        pdf = paths.root / "fixture.pdf"
                        document = pymupdf.open()
                        page = document.new_page()
                        page.insert_text((40, 50), "Desktop PDF source evidence for analysis.")
                        document.save(pdf)
                        document.close()
                        picker_count = 0
                        for filename in (image, pdf):
                            click("#add-source")
                            until_js("document.querySelector('#editor-dialog').open", "source dialog")
                            js("[...document.querySelectorAll('#editor-fields button')].find(b=>b.textContent.includes('Изображение')).click()")
                            until_js("!!document.querySelector('#field-file')", "file input")
                            selected = []
                            picker_timer = QTimer()
                            picker_timer.setInterval(50)
                            picker_deadline = monotonic() + 10
                            def choose():
                                if monotonic() > picker_deadline:
                                    select_windows_file(filename, cancel=True)
                                    picker_timer.stop()
                                    return
                                dialogs = [w for w in application.allWidgets() if isinstance(w, QFileDialog) and w.isVisible()]
                                if dialogs:
                                    dialog = dialogs[0]
                                    dialog.selectFile(str(filename))
                                    dialog.accept()
                                    selected.append(True)
                                    picker_timer.stop()
                                elif sys.platform == "win32" and select_windows_file(filename):
                                    selected.append(True)
                                    picker_timer.stop()
                            picker_timer.timeout.connect(choose)
                            picker_thread = None
                            if sys.platform == "win32":
                                def native_picker():
                                    wake = Event()
                                    while monotonic() < picker_deadline:
                                        if select_windows_file(filename):
                                            selected.append(True)
                                            return
                                        wake.wait(.05)  # Poll dialog existence, not a readiness sleep.
                                    select_windows_file(filename, cancel=True)
                                picker_thread = Thread(target=native_picker, name="test-native-picker")
                                picker_thread.start()
                            else:
                                picker_timer.start()
                            click("#field-file")
                            wait(lambda: bool(selected), "native Qt file picker")
                            if picker_thread:
                                picker_thread.join(timeout=2)
                                assert not picker_thread.is_alive()
                            picker_count += 1
                            until_js("document.querySelector('#field-file').files.length===1", "file selected")
                            js("document.querySelector('#form-submit').click()")
                            until_js("!document.querySelector('#editor-dialog').open", "file analyzed")
                        assert picker_count == 2
                        until_js("document.querySelectorAll('#source-list .source-card').length===3", "Text/Image/PDF list")
                        screenshot("desktop-1440x900.png")
                        window.resize(1024, 768)
                        until_js("innerWidth < 1200 && document.documentElement.scrollWidth <= innerWidth", "resize")
                        screenshot("desktop-1024x768.png")
                        window.showMaximized()
                        wait(window.isMaximized, "maximize")
                        screenshot("desktop-maximized.png")
                        js("document.querySelector('.analysis-pane').scrollTop=100000")
                        until_js("document.querySelector('.analysis-pane').scrollTop > 0", "scrolling")
                        # Native window is visibly shown; screenshots are real Qt grabs.
                        assert js("document.querySelector('.summary') !== null")
                        assert not console_errors, console_errors
                elif mode == "capture":
                    from http.server import ThreadingHTTPServer
                    from tests.browser_local_smoke import Handler
                    from backend.services.browser_service import browser_service
                    from backend.security.url_validation import ValidatedURL, resolve_and_validate_url
                    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
                    server_thread = Thread(target=server.serve_forever)
                    server_thread.start()
                    origin = f"http://127.0.0.1:{server.server_port}"
                    original_validator = browser_service.validator
                    async def local_policy(url):
                        if url.startswith(origin + "/"):
                            return ValidatedURL(url, "http", "127.0.0.1", server.server_port, ("127.0.0.1",))
                        return await resolve_and_validate_url(url)
                    browser_service.validator = local_policy
                    try:
                        future = asyncio.run_coroutine_threadsafe(browser_service.capture(origin + "/start"), worker.loop)
                        wait(future.done, "real Chromium local capture")
                        result = future.result()
                        assert result.screenshot.startswith(b"\x89PNG")
                        assert browser_service.ready and not browser_service._contexts
                    finally:
                        browser_service.validator = original_validator
                        server.shutdown()
                        server.server_close()
                        server_thread.join(timeout=5)
                    # Browser was actually launched in the backend's loop.
                assert not console_errors, console_errors
                if not window.closing:
                    window.close()
            wait(lambda: window.finished, "window/thread clean shutdown", 90)
            assert worker.done.is_set() and not worker.thread.is_alive()
            assert worker.listener.fileno() == -1
            assert not window.isVisible()
            with socket.socket() as probe:
                if sys.platform == "win32":
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                probe.bind(("127.0.0.1", worker.port))
            if mode != "failure":
                assert not errors and window.result_code == 0, errors
                log = paths.log.read_text(encoding="utf-8")
                assert "Application stopped" in log and "Application shutdown complete" in log
                assert "offline-desktop-marker" not in log
                if mode == "active":
                    assert active_finished.is_set()
                if mode == "capture":
                    from backend.services.browser_service import browser_service
                    assert browser_service._browser is None and browser_service._runtime is None
            evidence = {"mode": mode, "pass": True, "port": worker.port, "thread_stopped": True,
                        "port_rebound": True, "native_window_closed": True, "live_ai_calls": 0}
            (ARTIFACTS / (mode + ".json")).write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            print(json.dumps(evidence))
        finally:
            active_release.set()
            if worker.thread.is_alive():
                worker.stop()
                wait(lambda: not worker.thread.is_alive(), "failure cleanup", 90)
            if not window.browser_disposed:
                window.dispose_browser()
            lock.unlock()
            application.processEvents()
            if paths.log.exists():
                (ARTIFACTS / (mode + "-lifecycle.log")).write_text(paths.log.read_text(encoding="utf-8"), encoding="utf-8")
            logging.shutdown()


def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    before = fingerprint()
    if sys.argv[1:] == ["entrypoint"]:
        entrypoint_smoke()
    elif len(sys.argv) == 2:
        child(sys.argv[1])
    else:
        for mode in ("idle", "workflows", "capture", "active", "failure", "early-close"):
            seen = {}
            with (ARTIFACTS / (mode + "-process.log")).open("w", encoding="utf-8") as output:
                process = subprocess.Popen([sys.executable, "-m", "tests.desktop_smoke", mode], cwd=ROOT,
                                           stdout=output, stderr=subprocess.STDOUT)
                deadline = monotonic() + 150
                wake = Event()
                while process.poll() is None and monotonic() < deadline:
                    record_descendants(process.pid, seen)
                    wake.wait(.05)
                assert process.poll() is not None, "Smoke timeout (process not killed): " + mode
            text = (ARTIFACTS / (mode + "-process.log")).read_text(encoding="utf-8")
            assert process.returncode == 0, mode + "\n" + text
            assert_no_orphans(seen)
            evidence_file = ARTIFACTS / (mode + ".json")
            evidence = json.loads(evidence_file.read_text())
            evidence.update(orphan_processes=0, observed_child_processes=len(seen), process_exit_code=0)
            evidence_file.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            print(text.strip() + f"; orphan_processes=0; observed_children={len(seen)}")
        entrypoint_smoke()
    assert fingerprint() == before, "Production filesystem changed"
    print("DESKTOP_SMOKE_PASS; PRODUCTION_FILESYSTEM_UNCHANGED; LIVE_AI=0")


if __name__ == "__main__":
    main()
