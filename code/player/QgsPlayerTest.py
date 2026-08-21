# PyQt6 Video player
#!/usr/bin/env python

from qgis.PyQt.QtCore import QDir, Qt, QUrl, QFile, QBuffer, QIODevice
from qgis.PyQt.QtMultimedia import QMediaPlayer, QAudioOutput
try:
    # Kadas's qgis.PyQt shim does not always re-export QtMultimediaWidgets
    # (unlike QtMultimedia, which it does). Fall back to importing PyQt6
    # directly - this is the same binary/DLLs Kadas itself already uses
    # (no separate package like the standalone PySide6 that caused a DLL
    # conflict earlier in this migration), so it's safe here.
    from qgis.PyQt.QtMultimediaWidgets import QVideoWidget
except ImportError:
    from PyQt6.QtMultimediaWidgets import QVideoWidget
from qgis.PyQt.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLabel,
        QPushButton, QSizePolicy, QSlider, QStyle, QVBoxLayout, QWidget)
from qgis.PyQt.QtWidgets import QMainWindow, QWidget, QPushButton
from qgis.PyQt.QtGui import QIcon, QAction
import sys

class VideoWindow(QMainWindow):
    
    def __init__(self, parent=None):
        super(VideoWindow, self).__init__(parent)
        self.setWindowTitle("PyQt6 Video Player Widget Example - pythonprogramminglanguage.com") 

        self.mediaPlayer = QMediaPlayer(self)
        self.audioOutput = QAudioOutput(self)
        self.mediaPlayer.setAudioOutput(self.audioOutput)

        videoWidget = QVideoWidget()

        self.playButton = QPushButton()
        self.playButton.setEnabled(False)
        self.playButton.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.playButton.clicked.connect(self.play)

        self.positionSlider = QSlider(Qt.Orientation.Horizontal)
        self.positionSlider.setRange(0, 0)
        self.positionSlider.sliderMoved.connect(self.setPosition)

        self.errorLabel = QLabel()
        self.errorLabel.setSizePolicy(QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Maximum)

        # Create new action
        openAction = QAction(QIcon('open.png'), '&Open', self)        
        openAction.setShortcut('Ctrl+O')
        openAction.setStatusTip('Open movie')
        openAction.triggered.connect(self.openFile)

        # Create exit action
        exitAction = QAction(QIcon('exit.png'), '&Exit', self)        
        exitAction.setShortcut('Ctrl+Q')
        exitAction.setStatusTip('Exit application')
        exitAction.triggered.connect(self.exitCall)

        # Create menu bar and add action
        menuBar = self.menuBar()
        fileMenu = menuBar.addMenu('&File')
        #fileMenu.addAction(newAction)
        fileMenu.addAction(openAction)
        fileMenu.addAction(exitAction)

        # Create a widget for window contents
        wid = QWidget(self)
        self.setCentralWidget(wid)

        # Create layouts to place inside widget
        controlLayout = QHBoxLayout()
        controlLayout.setContentsMargins(0, 0, 0, 0)
        controlLayout.addWidget(self.playButton)
        controlLayout.addWidget(self.positionSlider)

        layout = QVBoxLayout()
        layout.addWidget(videoWidget)
        layout.addLayout(controlLayout)
        layout.addWidget(self.errorLabel)

        # Set widget to contain window contents
        wid.setLayout(layout)

        self.mediaPlayer.setVideoOutput(videoWidget)
        self.mediaPlayer.playbackStateChanged.connect(self.mediaStateChanged)
        self.mediaPlayer.positionChanged.connect(self.positionChanged)
        self.mediaPlayer.durationChanged.connect(self.durationChanged)
        self.mediaPlayer.errorOccurred.connect(self.handleError)

    def openFile(self):
        fileName, _ = QFileDialog.getOpenFileName(self, "Open Movie",
                QDir.homePath())
        if fileName != '':
            self.mediaPlayer.setSource(QUrl.fromLocalFile(fileName))
            self.playButton.setEnabled(True)

            
    def exitCall(self):
        sys.exit(app.exec())

    def play(self):
        if self.mediaPlayer.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.mediaPlayer.pause()
        else:
            self.mediaPlayer.play()

    def mediaStateChanged(self, state):
        if self.mediaPlayer.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.playButton.setIcon(
                    self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause))
        else:
            self.playButton.setIcon(
                    self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))

    def positionChanged(self, position):
        self.positionSlider.setValue(position)

    def durationChanged(self, duration):
        self.positionSlider.setRange(0, duration)

    def setPosition(self, position):
        self.mediaPlayer.setPosition(position)

    def handleError(self, error, errorString):
        self.playButton.setEnabled(False)
        self.errorLabel.setText("Error: " + errorString)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    player = VideoWindow()
    player.resize(640, 480)
    player.show()
    sys.exit(app.exec())
