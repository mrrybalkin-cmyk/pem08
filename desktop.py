"""PyQt6 shell for the existing Web application: python desktop.py."""

import json
import logging
import signal
import sys
from time import monotonic
from PyQt6 import sip

from PyQt6.QtCore import QLockFile, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkProxy, QNetworkRequest
from PyQt6.QtWidgets import QApplication, QLabel, QMainWindow, QMessageBox
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PyQt6.QtWebEngineWidgets import QWebEngineView

from desktop_runtime import BackendWorker, DesktopError, health_ready, held_socket, navigation_action, prepare_runtime, runtime_paths

logger = logging.getLogger("competitionmonitor.desktop")


class DesktopPage(QWebEnginePage):
    def __init__(self, profile, origin, parent=None):
        super().__init__(profile, parent)
        self.origin = origin
        self.closing = False
        self.newWindowRequested.connect(self._new_window)

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if self.closing:
            return False
        action = navigation_action(self.origin, url.toString())
        if action == "internal":
            return True
        if action == "external" and is_main_frame:
            QDesktopServices.openUrl(url)
        return False

    def _new_window(self, request):
        url = request.requestedUrl()
        if not self.closing and navigation_action(self.origin, url.toString()) == "external":
            QDesktopServices.openUrl(url)


def acquire_lock(paths):
    paths.root.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(paths.root / "desktop.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise DesktopError("Приложение уже запущено или каталог данных недоступен.")
    return lock


class DesktopWindow(QMainWindow):
    shutdown_complete = pyqtSignal()

    def __init__(self, worker, error_handler=None, page_class=DesktopPage):
        super().__init__()
        self.worker = worker
        self.error_handler = error_handler or self._show_error
        self.result_code = 0
        self.ready = False
        self.loaded = False
        self.closing = False
        self.finished = False
        self.browser_disposed = False
        self.pending = None
        self.deadline = monotonic() + 30
        self.setWindowTitle("CompetitionMonitor")
        self.resize(1440, 900)
        self.setCentralWidget(QLabel("Запуск приложения…", self))
        self.profile = QWebEngineProfile(self)  # off-the-record
        self.view = QWebEngineView(self)
        self.page = page_class(self.profile, worker.origin, self.view)
        self.view.setPage(self.page)
        self.view.loadFinished.connect(self._loaded)
        self.page.renderProcessTerminated.connect(lambda *_: None if self.closing else self._fail("Browser renderer остановлен."))
        self.network = QNetworkAccessManager(self)
        self.network.setProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _show_error(self, message):
        QMessageBox.critical(self, "CompetitionMonitor", message)

    def _fail(self, message):
        if self.result_code:
            return
        self.result_code = 1
        self.error_handler(message)
        self.close()

    def _tick(self):
        if self.closing:
            if self.worker.done.is_set() and not self.worker.thread.is_alive():
                self.worker.thread.join()
                if self.worker.error and not self.result_code:
                    self.result_code = 1
                    self.error_handler(self.worker.error)
                self.finished = True
                self.timer.stop()
                logger.info("desktop_shutdown_complete")
                self.close()
                self.shutdown_complete.emit()
            elif monotonic() >= self.deadline and not self.result_code:
                self.result_code = 1
                self.error_handler("Завершение backend заняло более 90 секунд. Log содержит диагностику; thread не прерывался.")
            return
        if self.worker.done.is_set():
            self._fail(self.worker.error or "Backend неожиданно завершился.")
        elif not self.loaded and monotonic() >= self.deadline:
            self._fail("Приложение не стало готово за 30 секунд. Проверьте desktop log.")
        elif not self.ready and self.pending is None:
            reply = self.network.get(QNetworkRequest(QUrl(self.worker.origin + "/api/v2/health")))
            self.pending = reply
            timeout = QTimer(reply)
            timeout.setSingleShot(True)
            timeout.timeout.connect(reply.abort)
            timeout.start(1000)
            reply.finished.connect(lambda: self._health(reply, timeout))

    def _health(self, reply, timeout):
        timeout.stop()
        self.pending = None
        try:
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            payload = json.loads(bytes(reply.readAll())) if reply.error() == reply.NetworkError.NoError else None
            if not self.closing and health_ready(status, payload):
                self.ready = True
                logger.info("health_ready port=%s", self.worker.port)
                self.setCentralWidget(self.view)
                self.view.load(QUrl(self.worker.origin + "/"))
        except (ValueError, UnicodeError):
            pass
        finally:
            reply.deleteLater()

    def _loaded(self, ok):
        if self.closing:
            return
        if not ok and not self.loaded:
            self._fail("Не удалось загрузить интерфейс приложения.")
        else:
            self.loaded = True
            logger.info("frontend_loaded")

    def closeEvent(self, event):
        if self.finished:
            event.accept()
            return
        event.ignore()
        if self.closing:
            return
        self.closing = True
        self.deadline = monotonic() + 90
        self.page.closing = True
        self.view.stop()
        self.view.setEnabled(False)
        self.setCentralWidget(QLabel("Завершение приложения…", self))
        self.dispose_browser()
        if self.pending is not None:
            self.pending.abort()
        self.setWindowTitle("CompetitionMonitor — завершение…")
        logger.info("desktop_shutdown_requested")
        self.worker.stop()

    def dispose_browser(self):
        # Page must be destroyed before its off-the-record profile.
        if not self.browser_disposed:
            self.browser_disposed = True
            sip.delete(self.view)
            sip.delete(self.profile)


def main():
    application = QApplication(sys.argv)
    application.setQuitOnLastWindowClosed(False)
    lock = None
    worker = None
    try:
        paths = runtime_paths()
        lock = acquire_lock(paths)
        prepare_runtime(paths)
        worker = BackendWorker(held_socket())
        window = DesktopWindow(worker)
        window.shutdown_complete.connect(application.quit)
        signal.signal(signal.SIGINT, lambda *_: window.close())
        worker.start()
        window.show()
        application.exec()
        window.dispose_browser()
        application.processEvents()
        return window.result_code
    except (DesktopError, OSError) as exc:
        message = str(exc) if isinstance(exc, DesktopError) else "Не удалось подготовить desktop runtime. Проверьте доступ к каталогу данных."
        QMessageBox.critical(None, "CompetitionMonitor", message)
        return 1
    finally:
        if lock is not None:
            lock.unlock()
        logging.shutdown()


if __name__ == "__main__":
    sys.exit(main())
