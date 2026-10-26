# -*- coding: utf-8 -*-
from math import hypot
from os.path import abspath, dirname, isfile, join

from qgis.PyQt.QtCore import QRectF
from qgis.PyQt.QtGui import QPainter

try:
    from qgis.PyQt.QtSvg import QSvgRenderer
except ImportError:
    from PyQt6.QtSvg import QSvgRenderer

from qgis.core import (QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform,
                       QgsCsException,
                       QgsGeometry,
                       QgsPointXY,
                       QgsProject)
from qgis.gui import QgsMapCanvasItem

_PLUGIN_DIR = dirname(dirname(abspath(__file__)))
_RESOURCE_PREFIX = ":/imgFMV/"


def loadSvgRenderer(path):
    ''' Load an SVG, falling back to the file shipped in the plugin folder.

        Compiled Qt resources are not always readable: depending on the tool
        used to build resources_rc.py and on the compression picked for a
        given entry, QFile(":/...").open() can fail with "Input/output error"
        while QFile.exists() still returns True. The plugin ships its SVG
        files in images/ anyway, so ":/imgFMV/xxx" is retried as
        "<plugin folder>/xxx".

        Returns (QSvgRenderer, path actually used) or (None, None).
    '''
    candidates = [path]
    if path.startswith(_RESOURCE_PREFIX):
        candidates.append(join(_PLUGIN_DIR, *path[len(_RESOURCE_PREFIX):].split("/")))
    elif path.startswith(":/"):
        candidates.append(join(_PLUGIN_DIR, *path[2:].split("/")))

    for candidate in candidates:
        if not candidate.startswith(":") and not isfile(candidate):
            continue
        renderer = QSvgRenderer(candidate)
        if renderer.isValid():
            return renderer, candidate
    return None, None


class FmvSvgMarkerItem(QgsMapCanvasItem):
    ''' An SVG marker anchored to a point, sized in pixels and rotated.

        QgsRubberBand cannot do this: setSymbol() only honours line and fill
        symbols, and its SVG icons are drawn at the viewBox size and never
        rotated (drawShape() only translates to the point). This item owns its
        CRS and reprojects itself whenever the canvas extent changes, the way
        the Kadas map items used to.
    '''

    def __init__(self, canvas, crs=None):
        super().__init__(canvas)
        # QgsMapCanvasItem.canvas() only exists from QGIS 4.4 on
        self._canvas = canvas
        self._crs = crs if crs is not None else QgsCoordinateReferenceSystem("EPSG:4326")
        self._itemPoint = None
        self._mapPoint = None
        self._xform = None
        self._xformDest = None
        self._renderer = None
        self._path = None
        self._resolvedPath = None
        self._anchorX = 0.5
        self._anchorY = 0.5
        self._width = 50.0
        self._height = 50.0
        self._angle = 0.0
        self.setZValue(100)

    def setup(self, path, anchorX=0.5, anchorY=0.5, width=50, height=50):
        ''' SVG file (or Qt resource), anchor in [0,1] and size in pixels. '''
        self.prepareGeometryChange()
        if path != self._path:
            self._renderer, self._resolvedPath = loadSvgRenderer(path)
            self._path = path
        self._anchorX = float(anchorX)
        self._anchorY = float(anchorY)
        self._width = float(width) if width else 50.0
        self._height = float(height) if height else 50.0
        self.update()

    def isValid(self):
        return self._renderer is not None

    def resolvedPath(self):
        ''' Path the SVG was actually read from, resource or plugin folder. '''
        return self._resolvedPath

    def setPosition(self, x, y):
        ''' Position expressed in the item CRS (lon, lat for EPSG:4326). '''
        self._itemPoint = QgsPointXY(x, y)
        self.updatePosition()

    def position(self):
        return self._itemPoint

    def setAngle(self, angle):
        ''' Heading in degrees, clockwise from north. '''
        angle = float(angle)
        if angle != self._angle:
            self._angle = angle
            self.update()

    def angle(self):
        return self._angle

    def asGeometry(self):
        ''' Position in canvas CRS, matching QgsRubberBand.asGeometry(). '''
        if self._mapPoint is None:
            return QgsGeometry()
        return QgsGeometry.fromPointXY(self._mapPoint)

    def reset(self, geometryType=None):
        ''' Drop the marker from the canvas. '''
        self._itemPoint = None
        self._mapPoint = None
        self.hide()
        scene = self.scene()
        if scene is not None:
            scene.removeItem(self)

    def updatePosition(self):
        ''' Called by the canvas on every extent change and resize. '''
        if self._itemPoint is None:
            return
        canvasCrs = self._canvas.mapSettings().destinationCrs()
        if self._xform is None or self._xformDest != canvasCrs:
            self._xform = QgsCoordinateTransform(self._crs, canvasCrs,
                                                 QgsProject.instance())
            self._xformDest = canvasCrs
        try:
            self._mapPoint = self._xform.transform(self._itemPoint)
        except QgsCsException:
            return
        self.setPos(self.toCanvasCoordinates(self._mapPoint))
        self.update()

    def boundingRect(self):
        # generous: the diagonal covers any rotation angle
        d = hypot(self._width, self._height)
        return QRectF(-d, -d, 2 * d, 2 * d)

    # QgsMapCanvasItem declares both paint(painter) and
    # paint(painter, option, widget); accept either signature.
    def paint(self, painter, option=None, widget=None):
        if self._renderer is None or self._itemPoint is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.rotate(self._angle)
        painter.translate(-self._anchorX * self._width,
                          -self._anchorY * self._height)
        self._renderer.render(painter, QRectF(0.0, 0.0, self._width, self._height))
        painter.restore()
