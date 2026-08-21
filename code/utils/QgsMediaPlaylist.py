# -*- coding: utf-8 -*-
"""
Qt6 removed QtMultimedia's QMediaPlaylist entirely (QMediaPlayer no
longer has setPlaylist()/playlist()). QGIS_FMV only ever used a small subset
of that API: a flat, ordered list of QUrl "media", a "current index", basic
add/remove, and a Loop/Sequential playback mode used to decide whether to
auto-advance at end of video.

This class reimplements just that subset. It does NOT automatically drive a
QMediaPlayer the way Qt5's QMediaPlayer.setPlaylist() used to - the caller
(QgsFmvPlayer) is responsible for reacting to currentMediaChanged and to
end-of-media to advance playback. See QgsFmvPlayer.attachPlaylist().
"""
from qgis.PyQt.QtCore import QObject, pyqtSignal


class QgsMediaPlaylist(QObject):
    """ Minimal QMediaPlaylist replacement """

    # Playback modes (subset actually used by QGIS_FMV)
    Sequential = 0
    Loop = 1

    currentIndexChanged = pyqtSignal(int)
    currentMediaChanged = pyqtSignal(object)  # emits a QUrl

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items = []
        self._currentIndex = -1
        self._mode = self.Sequential

    def addMedia(self, url):
        ''' Append a QUrl to the playlist '''
        self._items.append(url)

    def removeMedia(self, index):
        ''' Remove item at index '''
        if 0 <= index < len(self._items):
            del self._items[index]
            if self._currentIndex >= len(self._items):
                self._currentIndex = len(self._items) - 1

    def mediaCount(self):
        ''' Number of items in the playlist '''
        return len(self._items)

    def media(self, index):
        ''' Return the QUrl at index '''
        return self._items[index]

    def currentIndex(self):
        ''' Currently selected index, -1 if none '''
        return self._currentIndex

    def setCurrentIndex(self, index):
        ''' Select the item at index and emit change signals '''
        if 0 <= index < len(self._items):
            self._currentIndex = index
            self.currentIndexChanged.emit(index)
            self.currentMediaChanged.emit(self._items[index])

    def nextIndex(self):
        ''' Index that would be played next, -1 if there is none '''
        if not self._items:
            return -1
        if self._currentIndex + 1 < len(self._items):
            return self._currentIndex + 1
        if self._mode == self.Loop:
            return 0
        return -1

    def setPlaybackMode(self, mode):
        ''' Sequential or Loop '''
        self._mode = mode

    def playbackMode(self):
        return self._mode
