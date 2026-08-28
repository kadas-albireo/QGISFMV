# -*- coding: utf-8 -*-
import ast
import threading
from time import monotonic
from qgis.PyQt.QtCore import QRegularExpression, QCoreApplication, Qt
from qgis.PyQt.QtGui import QIntValidator, QRegularExpressionValidator
from qgis.PyQt.QtWidgets import (QDialog, QApplication, QLabel, QMessageBox,
                                 QGroupBox, QHBoxLayout, QVBoxLayout,
                                 QLineEdit, QPushButton)
from QGIS_FMV.gui.ui_FmvOpenStream import Ui_FmvOpenStream
from QGIS_FMV.utils.QgsFmvUtils import askForFiles, parser
from QGIS_FMV.utils.QgsFmvTestServer import server, DEFAULT_PORT
from QGIS_FMV.utils.QgsUtils import QgsUtils as qgsu
from qgis.core import Qgis as QGis

try:
    from pydevd import *
except ImportError:
    None

try:
    import cv2
except ImportError:
    None


class OpenStream(QDialog, Ui_FmvOpenStream):
    """ Open Stream Dialog """

    def __init__(self, iface, parent=None):
        """ Contructor """
        super().__init__(parent)
        self.setupUi(self)
        self.parent = parent
        self.iface = iface

        self.markAsBeta()

        # Int Validator
        self.onlyInt = QIntValidator()
        self.ln_port.setValidator(self.onlyInt)

        # IP Validator - Qt 6 dropped setRegExp(), the pattern goes to the ctor
        rx = QRegularExpression(
            "((1{0,1}[0-9]{0,2}|2[0-4]{1,1}[0-9]{1,1}|25[0-5]{1,1})\\.){3,3}(1{0,1}[0-9]{0,2}|2[0-4]{1,1}[0-9]{1,1}|25[0-5]{1,1})")
        self.ln_host.setValidator(QRegularExpressionValidator(rx, self))

        self.buildStreamSections()

    def markAsBeta(self):
        ''' Live streaming is not production ready, say so in the dialog. '''
        self.setWindowTitle(QCoreApplication.translate(
            "QgsFmvOpenStream", "Streaming (BETA)"))
        banner = QLabel(QCoreApplication.translate(
            "QgsFmvOpenStream",
            "Serve and play UDP/RTP video streams."), self)
        banner.setWordWrap(True)
        banner.setStyleSheet("QLabel { color: rgb(150, 90, 0); }")
        self.verticalLayout.insertWidget(0, banner)
        self.setMinimumWidth(420)
        # setupUi fixed the height for a dialog without this banner
        self.adjustSize()

    def buildStreamSections(self):
        ''' Serving comes first, joining second: that is the order the
            two are used in when there is no other machine around.
        '''
        self.serverBox = self.buildServerSection()
        self.clientBox = self.buildClientSection()
        # after the beta banner, before the Accept row
        self.verticalLayout.insertWidget(1, self.serverBox)
        self.verticalLayout.insertWidget(2, self.clientBox)
        self.refreshTestServer()
        self.adjustSize()

    def buildClientSection(self):
        ''' The protocol, host and port of the stream to join.

            setupUi puts these three straight into the dialog. They are
            moved into a titled box here, which is only possible by moving
            the widgets: addWidget reparents them, and the row they came
            from is then an empty layout to detach.
        '''
        box = QGroupBox(QCoreApplication.translate(
            "QgsFmvOpenStream", "Open Stream"), self)
        row = QHBoxLayout(box)
        for widget in (self.cmb_protocol, self.ln_host, self.ln_port):
            row.addWidget(widget)
        self.verticalLayout.removeItem(self.horizontalLayout_2)
        return box

    def buildServerSection(self):
        ''' A transmitter that loops a local file out on the loopback.

            Trying the streaming path used to need a second machine or a
            command line left open beside Kadas. This sends a file out as
            MPEG-TS over UDP and fills in the client fields below with the
            address it just started on, so the same dialog joins it.
        '''
        box = QGroupBox(QCoreApplication.translate(
            "QgsFmvOpenStream", "Serve Stream from a file"), self)
        outer = QVBoxLayout(box)

        chooser = QHBoxLayout()
        self.ln_serverFile = QLineEdit(box)
        self.ln_serverFile.setPlaceholderText(QCoreApplication.translate(
            "QgsFmvOpenStream", "Video file to loop out"))
        self.btn_serverBrowse = QPushButton("...", box)
        self.btn_serverBrowse.setMaximumWidth(32)
        self.btn_serverBrowse.setCursor(Qt.CursorShape.PointingHandCursor)
        chooser.addWidget(self.ln_serverFile)
        chooser.addWidget(self.btn_serverBrowse)
        outer.addLayout(chooser)

        controls = QHBoxLayout()
        controls.addWidget(QLabel(QCoreApplication.translate(
            "QgsFmvOpenStream", "Port"), box))
        self.ln_serverPort = QLineEdit(str(server.port), box)
        self.ln_serverPort.setValidator(QIntValidator(1, 65535, self))
        self.ln_serverPort.setMaximumWidth(70)
        controls.addWidget(self.ln_serverPort)
        controls.addStretch()
        self.btn_serverToggle = QPushButton(box)
        self.btn_serverToggle.setCursor(Qt.CursorShape.PointingHandCursor)
        controls.addWidget(self.btn_serverToggle)
        outer.addLayout(controls)

        self.btn_serverBrowse.clicked.connect(self.browseServerFile)
        self.btn_serverToggle.clicked.connect(self.toggleTestServer)
        return box

    def refreshTestServer(self):
        ''' Show what the shared server is doing, whoever started it. '''
        running = server.isRunning()
        self.btn_serverToggle.setText(
            QCoreApplication.translate("QgsFmvOpenStream", "Stop") if running
            else QCoreApplication.translate("QgsFmvOpenStream", "Start"))
        for widget in (self.ln_serverFile, self.btn_serverBrowse,
                       self.ln_serverPort):
            widget.setEnabled(not running)
        if running:
            # the dialog may have been closed and reopened since
            self.ln_serverFile.setText(server.videoPath)
            self.ln_serverPort.setText(str(server.port))

    def browseServerFile(self):
        ''' Pick the file to loop out. '''
        try:
            exts = ast.literal_eval(parser.get("FILES", "Exts"))
        except Exception:
            exts = "*"
        filename, _ = askForFiles(self, QCoreApplication.translate(
            "QgsFmvOpenStream", "Video file to stream"), exts=exts)
        if filename:
            self.ln_serverFile.setText(filename)

    def toggleTestServer(self):
        ''' Start the transmitter, or stop the one already running. '''
        if server.isRunning():
            server.stop()
            self.refreshTestServer()
            return

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        started, reason = server.start(self.ln_serverFile.text(),
                                       self.ln_serverPort.text())
        QApplication.restoreOverrideCursor()

        if not started:
            qgsu.showUserAndLogMessage(QCoreApplication.translate(
                "QgsFmvOpenStream", "Test server could not start : "),
                reason, level=QGis.Warning)
            self.refreshTestServer()
            return

        # point the client fields at what was just started, so opening it
        # is one more click
        index = self.cmb_protocol.findText("UDP")
        if index >= 0:
            self.cmb_protocol.setCurrentIndex(index)
        self.ln_host.setText("127.0.0.1")
        self.ln_port.setText(str(server.port))
        self.refreshTestServer()

    def probeConnection(self, url, timeout=3.0):
        ''' True reachable, False refused, None could not tell in time.

            cv2.VideoCapture().read() blocks for tens of seconds on an
            address nothing answers on, which would freeze the whole GUI
            behind a wait cursor. The probe therefore runs on a worker
            thread and anything slower than timeout counts as unknown.

            OpenCV is also an optional dependency and cannot open every
            transport the splitter handles - bare RTP without an SDP in
            particular - so it must not be the only authority here.
        '''
        result = {}
        milliseconds = int(timeout * 1000)

        def probe():
            try:
                try:
                    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG,
                                           [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, milliseconds,
                                            cv2.CAP_PROP_READ_TIMEOUT_MSEC, milliseconds])
                except Exception:
                    # older OpenCV without per capture timeouts
                    cap = cv2.VideoCapture(url)
                ret, _ = cap.read()
                cap.release()
                result['value'] = bool(ret)
            except Exception as e:
                result['error'] = str(e)

        worker = threading.Thread(target=probe)
        worker.daemon = True
        worker.start()
        deadline = monotonic() + timeout
        while worker.is_alive() and monotonic() < deadline:
            # keep the dialog painting instead of locking the whole app
            QApplication.processEvents()
            worker.join(0.05)

        if 'error' in result:
            qgsu.showUserAndLogMessage("", "Stream: probe unavailable: " + result['error'], onlyLog=True)
            return None
        if 'value' not in result:
            qgsu.showUserAndLogMessage("", "Stream: probe timed out after " + str(timeout) + "s.", onlyLog=True)
            return None
        return result['value']

    def confirmUnreachable(self):
        ''' Let the user open a stream the probe could not read. '''
        answer = QMessageBox.question(
            self,
            QCoreApplication.translate("QgsFmvOpenStream", "Streaming (BETA)"),
            QCoreApplication.translate(
                "QgsFmvOpenStream",
                "No picture could be read from this address.\n\n"
                "That check uses OpenCV, which cannot open every stream the "
                "player handles. Open it anyway?"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        return answer == QMessageBox.StandardButton.Yes

    def OpenStream(self, _):
        protocol = self.cmb_protocol.currentText().lower()
        host = self.ln_host.text()
        port = self.ln_port.text()
        v = protocol + "://" + host + ":" + port
        if host != "" and port != "":
            # before the probe, which costs seconds to then refuse anyway
            if self.parent.isSourceInManager(v):
                qgsu.showUserAndLogMessage(QCoreApplication.translate(
                    "QgsFmvOpenStream", "This stream is already in the list : "),
                    v, level=QGis.Warning)
                return
            qgsu.showUserAndLogMessage(QCoreApplication.translate(
                "QgsFmvOpenStream", "Checking connection!"))
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            QApplication.processEvents()
            reachable = self.probeConnection(v)
            QApplication.restoreOverrideCursor()
            if reachable is False and not self.confirmUnreachable():
                qgsu.showUserAndLogMessage(QCoreApplication.translate(
                    "QgsFmvOpenStream", "There is no such connection!"), level=QGis.Warning)
                return
            if reachable is None:
                qgsu.showUserAndLogMessage("", "Stream: connection could not be probed, "
                                           "opening anyway.", onlyLog=True)
            self.parent.AddFileRowToManager(v, v)
            self.close()
