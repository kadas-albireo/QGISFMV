# -*- coding: utf-8 -*-

# A local transmitter, so a stream can be tried without a second machine.
#
# It loops a file out as MPEG-TS over UDP on the loopback, which the same
# dialog then joins as a client.
#
# Three details of the command are not cosmetic. -c copy passes the file own
# bitstream through: without it ffmpeg encodes with the default encoder of the
# mpegts muxer, mpeg4 at 200 kb/s, and the picture arrives unrecognisable.
# Plain mpegts over udp rather than rtp_mpegts, because the RTP demuxer holds
# packets in a resequencing queue and drops the whole queue when its delay is
# reached, which on a loopback hop cost about a third of the video packets in
# testing. pkt_size 1316 is 7 x 188, so a lost datagram destroys whole
# transport stream packets instead of straddling two of them.
#
# The streams are mapped optionally rather than by index: a file without audio
# would make -map 0:1 fail, and the metadata track is what makes the stream
# worth testing at all.

import os
import platform
import subprocess
import tempfile
from time import sleep

from qgis.PyQt.QtCore import QCoreApplication

from QGIS_FMV.utils.QgsFmvUtils import ffmpeg_path
from QGIS_FMV.utils.QgsUtils import QgsUtils as qgsu

windows = platform.system() == 'Windows'

DEFAULT_PORT = 8888
UDP_PACKET_SIZE = 1316
# ffmpeg gives up on a file it cannot read almost at once, so a short look is
# enough to turn a silent failure into a message
STARTUP_GRACE_SECONDS = 0.6


class TestStreamServer(object):
    ''' One looping ffmpeg transmitter, shared by every dialog instance.

        The dialog is built and destroyed each time the menu is opened, so the
        process cannot belong to it: the whole point is to start the server,
        close the dialog, and join the stream as a client. Reopening the
        dialog finds the server still running and offers to stop it.
    '''

    def __init__(self):
        self.process = None
        self.errors = None
        self.videoPath = ''
        self.port = DEFAULT_PORT

    def isRunning(self):
        return self.process is not None and self.process.poll() is None

    def destination(self):
        ''' What a client has to open to receive this. '''
        return 'udp://127.0.0.1:' + str(self.port)

    def command(self, videoPath, port):
        ''' The ffmpeg call, kept separate so it can be read and tested. '''
        return [ffmpeg_path,
                '-hide_banner', '-loglevel', 'error',
                '-stream_loop', '-1', '-re', '-i', videoPath,
                '-map', '0:v?', '-map', '0:a?', '-map', '0:d?',
                '-c', 'copy',
                '-f', 'mpegts',
                'udp://127.0.0.1:' + str(port) +
                '?pkt_size=' + str(UDP_PACKET_SIZE)]

    def start(self, videoPath, port):
        ''' Returns (True, '') or (False, why it did not start). '''
        if self.isRunning():
            return False, QCoreApplication.translate(
                "QgsFmvTestServer", "The test server is already running.")
        if not videoPath:
            return False, QCoreApplication.translate(
                "QgsFmvTestServer", "Choose a video file first.")
        if not os.path.isfile(videoPath):
            return False, QCoreApplication.translate(
                "QgsFmvTestServer", "This file does not exist: ") + videoPath
        try:
            port = int(port)
        except (TypeError, ValueError):
            return False, QCoreApplication.translate(
                "QgsFmvTestServer", "The port has to be a number.")
        if not 1 <= port <= 65535:
            return False, QCoreApplication.translate(
                "QgsFmvTestServer", "The port has to be between 1 and 65535.")

        startupinfo = None
        if windows:
            # otherwise a console window pops up in front of Kadas
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        # a real file rather than a pipe: nothing reads it while ffmpeg runs,
        # and a pipe nobody drains blocks the process once it fills
        self.errors = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen(
                self.command(videoPath, port), startupinfo=startupinfo,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=self.errors)
        except Exception as e:
            self.process = None
            self._closeErrors()
            return False, str(e)

        sleep(STARTUP_GRACE_SECONDS)
        if self.process.poll() is not None:
            reason = self._readErrors()
            self.process = None
            self._closeErrors()
            return False, reason or QCoreApplication.translate(
                "QgsFmvTestServer", "ffmpeg stopped straight away.")

        self.videoPath = videoPath
        self.port = port
        qgsu.showUserAndLogMessage("", "Test server: streaming " + videoPath +
                                   " to " + self.destination(), onlyLog=True)
        return True, ''

    def stop(self):
        ''' Kill the transmitter. Safe to call when nothing is running. '''
        if self.process is not None:
            try:
                self.process.kill()
                self.process.wait(timeout=5)
            except Exception:
                pass
            qgsu.showUserAndLogMessage("", "Test server stopped.", onlyLog=True)
        self.process = None
        self._closeErrors()

    def _readErrors(self):
        if self.errors is None:
            return ''
        try:
            self.errors.seek(0)
            text = self.errors.read().decode('utf-8', 'replace').strip()
        except Exception:
            return ''
        # the last line is the one that says what went wrong
        lines = [line for line in text.splitlines() if line.strip()]
        return lines[-1] if lines else ''

    def _closeErrors(self):
        if self.errors is not None:
            try:
                self.errors.close()
            except Exception:
                pass
            self.errors = None


# the one instance the dialog and the plugin unload both talk to
server = TestStreamServer()
