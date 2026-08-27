# -*- coding: utf-8 -*-
import os
from os.path import dirname, abspath
from qgis.PyQt.QtGui import QColor, QFont, QPolygonF, QPen, QPainter, QBrush, qRgba
from qgis.PyQt.QtWidgets import QApplication
from qgis.PyQt.QtCore import QCoreApplication, QPointF, Qt

from configparser import ConfigParser
from QGIS_FMV.utils.QgsUtils import QgsUtils as qgsu
from qgis.PyQt.QtCore import QMetaType, QSettings
from qgis.core import (Qgis,
                       QgsCoordinateReferenceSystem,
                       QgsDistanceArea,
                       QgsFeature,
                       QgsField,
                       QgsFields,
                       QgsFontMarkerSymbolLayer,
                       QgsSimpleMarkerSymbolLayer,
                       QgsGeometry,
                       QgsLayerTreeLayer,
                       QgsMarkerSymbol,
                       QgsPoint,
                       QgsPointXY,
                       QgsProject,
                       QgsProperty,
                       QgsSymbolLayer,
                       QgsVectorFileWriter,
                       QgsVectorLayer,
                       )
from qgis.gui import QgsRubberBand

from qgis.utils import iface
from QGIS_FMV.utils.QgsFmvStyles import FmvLayerStyles as S
from QGIS_FMV.utils.QgsFmvCanvasItems import FmvSvgMarkerItem
from itertools import groupby

try:
    from pydevd import *
except ImportError:
    None

parser = ConfigParser()
parser.read(os.path.join(dirname(dirname(abspath(__file__))), 'settings.ini'))

frames_g = parser['LAYERS']['frames_g']
epsg = parser['LAYERS']['epsg']
groupName = None

encoding = "utf-8"

_layerreg = QgsProject.instance()
crtSensorSrc = crtSensorSrc2 = crtPltTailNum = 'DEFAULT'

TYPE_MAP = {
    str: QMetaType.Type.QString,
    float: QMetaType.Type.Double,
    int: QMetaType.Type.Int,
    bool: QMetaType.Type.Bool
}

Point = 'Point'
PointZ = 'PointZ'
LineZ = 'LineStringZ'
Line = 'LineString'
Polygon = 'Polygon'

platformMarker = None
frameCenterRubberBand = None
rbFrameAxisMarker = None
footprintRubberBand = None
rbTrajectoryMarker = None
lastTrajectoryEle=''
linesEle=[]
pointsEle=[]
pointsLblEle=[]
polygonsEle=[]
rbBeamMarkerUR = None
rbBeamMarkerUL = None
rbBeamMarkerLL = None
rbBeamMarkerLR = None

def SetDefaultLineStyle(lineRubberband:QgsRubberBand):
    ''' Line Symbol '''
    style = S.getDrawingLine()
    lineRubberband.setStrokeColor(QColor(style['COLOR']))
    lineRubberband.setWidth(int(style['WIDTH']))
    

def SetDefaultPolygonStyle(polygonRubberband:QgsRubberBand):
    ''' Polygon Symbol '''
    style = S.getDrawingPolygon()
        
    polygonRubberband.setStrokeColor(QColor(style['OUTLINE_COLOR']))
    polygonRubberband.setWidth(int(style['OUTLINE_WIDTH']))
    
    # b = QBrush()
    tmp = style['COLOR'].split(',')

    c = qRgba(int(tmp[0]), int(tmp[1]), int(tmp[2]), int(tmp[3]))

    polygonRubberband.setFillColor(QColor.fromRgba(c))


templatePointSymbol = QgsMarkerSymbol()
templatePointSymbol.deleteSymbolLayer(0)  # remove default layer

simple_marker = QgsSimpleMarkerSymbolLayer()
simple_marker.setColor(QColor('red'))
simple_marker.setStrokeColor(QColor('red'))
templatePointSymbol.appendSymbolLayer(simple_marker)

font_marker = QgsFontMarkerSymbolLayer()
font_marker.setFontFamily('Tahoma')
font_marker.setSize(4.2)
font_marker.setOffset(QPointF(0.6, -1.6))
font_marker.setVerticalAnchorPoint(Qgis.VerticalAnchorPoint.Bottom)
font_marker.setHorizontalAnchorPoint(Qgis.HorizontalAnchorPoint.Left)
font_marker.setDataDefinedProperty(
    QgsSymbolLayer.Property.Character,
    QgsProperty.fromExpression("'placeholder will be replaced with number'")
)
templatePointSymbol.appendSymbolLayer(font_marker)



rbPointsElement = []

rbLinesEle = QgsRubberBand(iface.mapCanvas(), Qgis.GeometryType.Line)
SetDefaultLineStyle(rbLinesEle)

rbLinesEle.setZValue(90)

rbPolygonsEle = QgsRubberBand(iface.mapCanvas(), Qgis.GeometryType.Polygon)
SetDefaultPolygonStyle(rbPolygonsEle)
rbPolygonsEle.setZValue(90)


def AddDrawPointOnMap(pointIndex, Longitude, Latitude, Altitude):
    '''  add pin point on the map '''
    global pointsEle
    global pointsLblEle
    
    #RemoveAllDrawPointOnMap()
    
    pointsEle.append(QgsPointXY(Longitude, Latitude))

    UpdateDrawPointOnMap()
    


def AddDrawLineOnMap(drawLines):
    '''  add Line on the map '''
    global linesEle
    
    RemoveAllDrawLineOnMap()
    
    for k, v in groupby(drawLines, key=lambda x: x == [None, None, None]):
        points = []
        if k is False:
            list1 = list(v)
            for i in range(0, len(list1)):
                pt = QgsPointXY(list1[i][0], list1[i][1])
                points.append(pt)
            geom = QgsGeometry.fromPolylineXY(points)
            linesEle.append(points)

    UpdateDrawLineOnMap()

def GetMapItems():
    global platformMarker, frameCenterRubberBand, footprintRubberBand
    return {
        "platform": platformMarker,
        "framecenter": frameCenterRubberBand,
        "footprint": footprintRubberBand

    }
    

def RemoveAllDrawings():

    global crtSensorSrc, crtPltTailNum, linesEle, pointsEle, pointsLblEle, polygonsEle, lastTrajectoryEle
    global footprintRubberBand, frameCenterRubberBand, platformMarker
    global rbBeamMarkerUR, rbBeamMarkerUL, rbBeamMarkerLL, rbBeamMarkerLR
    global rbTrajectoryMarker, rbFrameAxisMarker

    
    rbLinesEle.reset(Qgis.GeometryType.Line)
    
    for ele in rbPointsElement:
        ele.reset(Qgis.GeometryType.Point)
    rbPointsElement.clear()



    rbPolygonsEle.reset(Qgis.GeometryType.Polygon)
    
    if footprintRubberBand is not None:
        footprintRubberBand.reset(Qgis.GeometryType.Polygon)
        footprintRubberBand = None

    if frameCenterRubberBand is not None:
        frameCenterRubberBand.reset(Qgis.GeometryType.Point)
        frameCenterRubberBand = None

    if platformMarker is not None:
        platformMarker.reset()
        platformMarker = None

    if rbBeamMarkerUR is not None:
        rbBeamMarkerUR.reset(Qgis.GeometryType.Line)        
        rbBeamMarkerUR = None 

    
    if rbBeamMarkerUL is not None:
        rbBeamMarkerUL.reset(Qgis.GeometryType.Line)        
        rbBeamMarkerUL = None 

    
    if rbBeamMarkerLL is not None:
        rbBeamMarkerLL.reset(Qgis.GeometryType.Line)        
        rbBeamMarkerLL = None 

    
    if rbBeamMarkerLR is not None:
        rbBeamMarkerLR.reset(Qgis.GeometryType.Line)        
        rbBeamMarkerLR = None 

    if rbTrajectoryMarker is not None:
        rbTrajectoryMarker.reset(Qgis.GeometryType.Line)
        rbTrajectoryMarker = None

    if rbFrameAxisMarker is not None:
        rbFrameAxisMarker.reset(Qgis.GeometryType.Line)
        rbFrameAxisMarker = None

    crtSensorSrc, crtPltTailNum, lastTrajectoryEle = 'DEFAULT', 'DEFAULT', ''
    linesEle, pointsEle, pointsLblEle, polygonsEle = [], [], [], []


def UpdateDrawPointOnMap():
    global pointsEle

    pointsEleGeom = QgsGeometry.fromMultiPointXY(pointsEle)


    for ele in rbPointsElement:
        ele.reset(Qgis.GeometryType.Point)
    rbPointsElement.clear()

    for i, point in enumerate(pointsEle):
        pointRubberBand = QgsRubberBand(iface.mapCanvas(), Qgis.GeometryType.Point)
        pointRubberBand.setToGeometry(QgsGeometry.fromPointXY(point), QgsCoordinateReferenceSystem("EPSG:4326"))

        c = templatePointSymbol.clone()
        c.symbolLayer(1).setDataDefinedProperty(
            QgsSymbolLayer.Property.Character,
            QgsProperty.fromExpression(f"'{i + 1}'")
        )

        pointRubberBand.setSymbol(c)
        pointRubberBand.setZValue(100)

        rbPointsElement.append(pointRubberBand)
    iface.mapCanvas().refresh()

def UpdateDrawLineOnMap():
    global linesEle

    rbLinesEle.setToGeometry(QgsGeometry.fromMultiPolylineXY(linesEle),  QgsCoordinateReferenceSystem("EPSG:4326"))
    iface.mapCanvas().refresh()

def UpdateDrawPolygonOnMap():
    global polygonsEle

    rbPolygonsEle.setToGeometry(QgsGeometry.fromMultiPolygonXY(polygonsEle),  QgsCoordinateReferenceSystem("EPSG:4326"))
    iface.mapCanvas().refresh()


def RemoveAllDrawLineOnMap():
    ''' Remove all features on Line Layer '''
    global linesEle
        
    rbLinesEle.reset(Qgis.GeometryType.Line)
    
    linesEle = []
    

def RemoveLastDrawPolygonOnMap():
    '''  Remove Last Feature on Polygon Layer '''
    global polygonsEle

    if polygonsEle:
        polygonsEle.pop()

    UpdateDrawPolygonOnMap()

def RemoveLastDrawPointOnMap():
    ''' Remove Last features on Point Layer '''
    global pointsEle, pointsLblEle
    
    if pointsEle:
        pointsEle.pop()

    UpdateDrawPointOnMap()


def RemoveAllDrawPointOnMap():
    ''' Remove all features on Point Layer '''
    global pointsEle, pointsLblEle

    for ele in rbPointsElement:
        ele.reset(Qgis.GeometryType.Point)
    rbPointsElement.clear()

    
    pointsEle = []
    pointsLblEle = []
    


def RemoveAllDrawPolygonOnMap():
    ''' Remove all features on Polygon Layer '''
    global polygonsEle

    rbPolygonsEle.reset(Qgis.GeometryType.Polygon)
    
    polygonsEle = []
    

def AddDrawPolygonOnMap(poly_coordinates):
    ''' Add Polygon Layer '''
    global polygonsEle
    
    #RemoveAllDrawPolygonOnMap()
    
    feature = QgsFeature()
    point = QPointF()
    # create  float polygon --> construcet out of 'point'

    exterior_ring = []
    for x in range(0, len(poly_coordinates)):
        if x % 2 == 0:
            point = QgsPointXY(poly_coordinates[x], poly_coordinates[x + 1])
            exterior_ring.append(point)
    point = QgsPointXY(poly_coordinates[0], poly_coordinates[1])
    exterior_ring.append(point)

    geomP = QgsGeometry.fromPolygonXY([exterior_ring])

    feature.setGeometry(geomP)

    # Calculate Area WSG84 (Meters)
    area_wsg84 = QgsDistanceArea()
    area_wsg84.setSourceCrs(QgsCoordinateReferenceSystem.fromOgcWmsCrs(
        'EPSG:4326'), _layerreg.transformContext())
    if (area_wsg84.sourceCrs().isGeographic()):
        area_wsg84.setEllipsoid(
            area_wsg84.sourceCrs().ellipsoidAcronym())

    # Calculate Centroid
    try:
        centroid = feature.geometry().centroid().asPoint()
    except Exception:
        return False

    feature.setAttributes([centroid.x(), centroid.y(
    ), 0.0, area_wsg84.measurePolygon(geomP.asPolygon()[0])])
    
    polygonsEle.append([exterior_ring])

    UpdateDrawPolygonOnMap()
    return True


def SetcrtSensorSrc():
    ''' Set Style based on Sensor type '''
    global crtSensorSrc, crtSensorSrc2
    crtSensorSrc = crtSensorSrc2 = 'DEFAULT'


def SetcrtPltTailNum():
    ''' Set Style based on Platform Type Number '''
    global crtPltTailNum
    crtPltTailNum = 'DEFAULT'


def UpdateFootPrintData(packet, cornerPointUL, cornerPointUR, cornerPointLR, cornerPointLL, ele):
    ''' Update Footprint Values '''
    global crtSensorSrc, groupName
    global footprintRubberBand
    imgSS = packet.ImageSourceSensor
    
    if all(v is not None for v in [cornerPointUL, cornerPointUR, cornerPointLR, cornerPointLL]) and all(v >= 2 for v in [len(cornerPointUL), len(cornerPointUR), len(cornerPointLR), len(cornerPointLL)]):
        
        if footprintRubberBand is None:
            footprintRubberBand = QgsRubberBand(iface.mapCanvas(), Qgis.GeometryType.Polygon)
            footprintRubberBand.setZValue(80)
        
        if(imgSS != crtSensorSrc):
            SetDefaultFootprintStyle(footprintRubberBand, imgSS)
            crtSensorSrc = imgSS
            
        footprintRubberBand.reset(Qgis.GeometryType.Polygon)
        geom = QgsGeometry.fromPolygonXY([[
                    QgsPointXY(cornerPointUL[1],
                               cornerPointUL[0]),
                    QgsPointXY(
                        cornerPointUR[1], cornerPointUR[0]),
                    QgsPointXY(
                        cornerPointLR[1], cornerPointLR[0]),
                    QgsPointXY(
                        cornerPointLL[1], cornerPointLL[0]),
                    QgsPointXY(cornerPointUL[1], cornerPointUL[0])]])
        
        footprintRubberBand.setToGeometry(geom, QgsCoordinateReferenceSystem("EPSG:4326"))
    
    return


def UpdateBeamsData(packet, cornerPointUL, cornerPointUR, cornerPointLR, cornerPointLL, ele):
    global rbBeamMarkerUR, rbBeamMarkerUL, rbBeamMarkerLL, rbBeamMarkerLR
    
    ''' Update Beams Values '''
    lat = packet.SensorLatitude
    lon = packet.SensorLongitude
    alt = packet.SensorTrueAltitude
    if all(v is not None for v in [lat, lon, alt, cornerPointUL, cornerPointUR, cornerPointLR, cornerPointLL]) and all(v >= 2 for v in [len(cornerPointUL), len(cornerPointUR), len(cornerPointLR), len(cornerPointLL)]):
        
        #ul
        if rbBeamMarkerUL is None:
            rbBeamMarkerUL = QgsRubberBand(iface.mapCanvas(), Qgis.GeometryType.Line)
            rbBeamMarkerUL.setZValue(80)            
            SetDefaultBeamsStyle(rbBeamMarkerUL)
        
        rbBeamMarkerUL.reset()
        geom = QgsGeometry.fromPolyline([QgsPoint(lon, lat, alt), QgsPoint(cornerPointUL[1], cornerPointUL[0])])
        rbBeamMarkerUL.setToGeometry(geom, QgsCoordinateReferenceSystem("EPSG:4326"))
        
        #ur
        if rbBeamMarkerUR is None:
            rbBeamMarkerUR = QgsRubberBand(iface.mapCanvas(), Qgis.GeometryType.Line)
            rbBeamMarkerUR.setZValue(80)
            SetDefaultBeamsStyle(rbBeamMarkerUR)
        
        rbBeamMarkerUR.reset()
        geom = QgsGeometry.fromPolyline([QgsPoint(lon, lat, alt), QgsPoint(cornerPointUR[1], cornerPointUR[0])])
        rbBeamMarkerUR.setToGeometry(geom, QgsCoordinateReferenceSystem("EPSG:4326"))
        
        #lr
        if rbBeamMarkerLR is None:
            rbBeamMarkerLR = QgsRubberBand(iface.mapCanvas(), Qgis.GeometryType.Line)
            rbBeamMarkerLR.setZValue(80)
            SetDefaultBeamsStyle(rbBeamMarkerLR)
        
        rbBeamMarkerLR.reset()
        geom = QgsGeometry.fromPolyline([QgsPoint(lon, lat, alt), QgsPoint(cornerPointLR[1], cornerPointLR[0])])
        rbBeamMarkerLR.setToGeometry(geom, QgsCoordinateReferenceSystem("EPSG:4326"))
        
        #ll
        if rbBeamMarkerLL is None:
            rbBeamMarkerLL = QgsRubberBand(iface.mapCanvas(), Qgis.GeometryType.Line)
            rbBeamMarkerLL.setZValue(80)
            SetDefaultBeamsStyle(rbBeamMarkerLL)
        
        rbBeamMarkerLL.reset()
        geom = QgsGeometry.fromPolyline([QgsPoint(lon, lat, alt), QgsPoint(cornerPointLL[1], cornerPointLL[0])])
        rbBeamMarkerLL.setToGeometry(geom, QgsCoordinateReferenceSystem("EPSG:4326"))
        
        

def UpdateTrajectoryData(packet, ele):
    global lastTrajectoryEle
    global rbTrajectoryMarker
    ''' Update Trajectory Values '''
    lat = packet.SensorLatitude
    lon = packet.SensorLongitude
    alt = packet.SensorTrueAltitude
    if all(v is not None for v in [lat, lon, alt]):
    
        if lastTrajectoryEle != '':
            if rbTrajectoryMarker is None:
                rbTrajectoryMarker = QgsRubberBand(iface.mapCanvas(), Qgis.GeometryType.Line)

                SetDefaultTrajectoryStyle(rbTrajectoryMarker)
            

            geom = QgsGeometry.fromPolyline([QgsPoint(lastTrajectoryEle.SensorLongitude, lastTrajectoryEle.SensorLatitude, alt), QgsPoint(lon, lat, alt)])
            rbTrajectoryMarker.addGeometry(geom, QgsCoordinateReferenceSystem("EPSG:4326"))

        lastTrajectoryEle = packet
        
    return


#def UpdateFrameAxisData(packet, ele):
def UpdateFrameAxisData(imgSS, sensor, framecenter, ele):
    ''' Update Frame Axis Values '''
    global crtSensorSrc2, groupName
    global rbFrameAxisMarker

    lat = sensor[0]
    lon = sensor[1]
    alt = sensor[2]
    fc_lat = framecenter[0]
    fc_lon = framecenter[1]
    fc_alt = framecenter[2]
    
    if all(v is not None for v in [lat, lon, alt, fc_lat, fc_lon]):
    
        if rbFrameAxisMarker is None:
            rbFrameAxisMarker = QgsRubberBand(iface.mapCanvas(), Qgis.GeometryType.Line)
            SetDefaultFrameAxisStyle(rbFrameAxisMarker)
        

        rbFrameAxisMarker.reset()
        geom = QgsGeometry.fromPolyline([QgsPoint(lon, lat, alt), QgsPoint(fc_lon, fc_lat, fc_alt)])
        rbFrameAxisMarker.setToGeometry(geom, QgsCoordinateReferenceSystem("EPSG:4326"))
        
    
    return


def UpdateFrameCenterData(pt, ele):
    global frameCenterRubberBand
    ''' Update FrameCenter Values '''
    lat = pt[0]
    lon = pt[1]
    alt = pt[2]
    
    if alt is None:
        alt = 0.0
    
    if all(v is not None for v in [lat, lon, alt]):
    
        if frameCenterRubberBand is None:
            frameCenterRubberBand = QgsRubberBand(iface.mapCanvas(), Qgis.GeometryType.Point)
            SetDefaultFrameCenterStyle(frameCenterRubberBand)
        
    frameCenterRubberBand.reset(Qgis.GeometryType.Point)
    frameCenterRubberBand.setToGeometry(QgsGeometry.fromPointXY(QgsPointXY(lon,lat)),  QgsCoordinateReferenceSystem("EPSG:4326"))
    iface.mapCanvas().refresh()

    return


def UpdatePlatformData(packet, ele):
    
    ''' Update PlatForm Values '''
    global crtPltTailNum, groupName
    global platformMarker

    lat = packet.SensorLatitude
    lon = packet.SensorLongitude
    alt = packet.SensorTrueAltitude
    PlatformHeading = packet.PlatformHeadingAngle
    platformTailNumber = packet.PlatformTailNumber
    platformDesignation = packet.PlatformDesignation
    
    #Indago drone doesn't provide platformTailNumber, add it there for style support.
    if platformDesignation is not None and "Indago" in platformDesignation:
        platformTailNumber="INDAGO"
        
        
    if all(v is not None for v in [lat, lon, alt, PlatformHeading]):
    
        if platformMarker is None:
            platformMarker = FmvSvgMarkerItem(iface.mapCanvas(), QgsCoordinateReferenceSystem("EPSG:4326"))
            SetDefaultPlatformStyle(platformMarker, platformTailNumber)
            platformMarker.setZValue(100)
        
        if platformTailNumber != crtPltTailNum:
            SetDefaultPlatformStyle(platformMarker, platformTailNumber)
            crtPltTailNum = platformTailNumber
                
        platformMarker.setPosition(lon, lat)
        platformMarker.setAngle(float(PlatformHeading))
        iface.mapCanvas().refresh()

    return


def CommonLayer(value):
    return
    ''' Common commands Layers '''
    value.commitChanges()
    value.updateExtents()
    iface.layerTreeView().refreshLayerSymbology(value.id())


def CreateGroupByName(name=frames_g):
    ''' Create Group if not exist '''
    global groupName
    root = _layerreg.layerTreeRoot()
    videogroup = root.findGroup(groupName)
    group = videogroup.findGroup(name)
    if group is None:
        # group = root.insertGroup(-1, name)  # Insert on bottom
        group = videogroup.insertGroup(-1, name)  # Insert on bottom
        # Unchecked visibility
        group.setItemVisibilityCheckedRecursive(False)
        group.setExpanded(False)
    return


def RemoveGroupByName(name=frames_g):
    ''' Remove Group if not exist '''
    root = _layerreg.layerTreeRoot()
    group = root.findGroup(name)
    if group is not None:
        for child in group.children():
            dump = child.name()
            _layerreg.removeMapLayer(dump.split("=")[-1].strip())
        root.removeChildNode(group)
    return


def CreateVideoLayers(ele, name):
    ''' Create Video Layers '''
    global groupName
    groupName = name
    
    #return
    #
    #if qgsu.selectLayerByName(Footprint_lyr, groupName) is None:
    #    lyr_footprint = newPolygonsLayer(
    #        None,
    #        ["Corner Longitude Point 1",
    #         "Corner Latitude Point 1",
    #         "Corner Longitude Point 2",
    #         "Corner Latitude Point 2",
    #         "Corner Longitude Point 3",
    #         "Corner Latitude Point 3",
    #         "Corner Longitude Point 4",
    #         "Corner Latitude Point 4"],
    #        epsg,
    #        Footprint_lyr)
    #    SetDefaultFootprintStyle(lyr_footprint)
    #    addLayerNoCrsDialog(lyr_footprint, group=groupName)
    #
    #    # 3D Style
    #    if ele:
    #        SetDefaultFootprintStyle(lyr_footprint)
    #
    #if qgsu.selectLayerByName(Beams_lyr, groupName) is None:
    #    lyr_beams = newLinesLayer(
    #        None,
    #        ["longitude",
    #         "latitude",
    #         "altitude",
    #         "Corner Longitude",
    #         "Corner Latitude"],
    #        epsg,
    #        Beams_lyr, LineZ)
    #    SetDefaultBeamsStyle(lyr_beams)
    #    addLayerNoCrsDialog(lyr_beams, group=groupName)
    #    # 3D Style
    #    if ele:
    #        SetDefaultBeamsStyle(lyr_beams)
    #
    #if qgsu.selectLayerByName(Trajectory_lyr, groupName) is None:
    #    lyr_Trajectory = newLinesLayer(
    #        None,
    #        ["longitude", "latitude", "altitude"], epsg, Trajectory_lyr, LineZ)
    #    SetDefaultTrajectoryStyle(lyr_Trajectory)
    #    addLayerNoCrsDialog(lyr_Trajectory, group=groupName)
    #    # 3D Style
    #    if ele:
    #        SetDefaultTrajectoryStyle(lyr_Trajectory)
    #
    #if qgsu.selectLayerByName(FrameAxis_lyr, groupName) is None:
    #    lyr_frameaxis = newLinesLayer(
    #        None, ["longitude", "latitude", "altitude", "Corner Longitude", "Corner Latitude", "Corner altitude"], epsg, FrameAxis_lyr, LineZ)
    #    SetDefaultFrameAxisStyle(lyr_frameaxis)
    #    addLayerNoCrsDialog(lyr_frameaxis, group=groupName)
    #    # 3D Style
    #    if ele:
    #        SetDefaultFrameAxisStyle(lyr_frameaxis)
    #
    #if qgsu.selectLayerByName(Platform_lyr, groupName) is None:
    #    lyr_platform = newPointsLayer(
    #        None,
    #        ["longitude", "latitude", "altitude"], epsg, Platform_lyr, PointZ)
    #    SetDefaultPlatformStyle(lyr_platform)
    #    addLayerNoCrsDialog(lyr_platform, group=groupName)
    #    # 3D Style
    #    if ele:
    #        SetDefaultPlatformStyle(lyr_platform)
    #
    #if qgsu.selectLayerByName(Point_lyr, groupName) is None:
    #    lyr_point = newPointsLayer(
    #        None, ["number", "longitude", "latitude", "altitude"], epsg, Point_lyr)
    #    SetDefaultPointStyle(lyr_point)
    #    addLayerNoCrsDialog(lyr_point, group=groupName)
    #
    #if qgsu.selectLayerByName(FrameCenter_lyr, groupName) is None:
    #    lyr_framecenter = newPointsLayer(
    #        None, ["longitude", "latitude", "altitude"], epsg, FrameCenter_lyr)
    #    SetDefaultFrameCenterStyle(lyr_framecenter)
    #    addLayerNoCrsDialog(lyr_framecenter, group=groupName)
    #    # 3D Style
    #    if ele:
    #        SetDefaultFrameCenterStyle(lyr_framecenter)
    #
    #if qgsu.selectLayerByName(Line_lyr, groupName) is None:
    #    #         lyr_line = newLinesLayer(
    #    # None, ["longitude", "latitude", "altitude"], epsg, Line_lyr)
    #    lyr_line = newLinesLayer(None, [], epsg, Line_lyr)
    #    SetDefaultLineStyle(lyr_line)
    #    addLayerNoCrsDialog(lyr_line, group=groupName)
    #
    #if qgsu.selectLayerByName(Polygon_lyr, groupName) is None:
    #    lyr_polygon = newPolygonsLayer(
    #        None, ["Centroid_longitude", "Centroid_latitude", "Centroid_altitude", "Area"], epsg, Polygon_lyr)
    #    SetDefaultPolygonStyle(lyr_polygon)
    #    addLayerNoCrsDialog(lyr_polygon, group=groupName)
    #
    #QApplication.processEvents()
    #return


def ExpandLayer(layer, value=True):
    '''Collapse/Expand layer'''
    ltl = _layerreg.layerTreeRoot().findLayer(layer.id())
    ltl.setExpanded(value)
    QApplication.processEvents()
    return


def SetDefaultFootprintStyle(mapRubberBand:QgsRubberBand, sensor='DEFAULT'):
    ''' Footprint Symbol '''
    style = S.getSensor(sensor)
    
    mapRubberBand.setWidth(int(style['OUTLINE_WIDTH']))
    mapRubberBand.setStrokeColor(QColor(style['OUTLINE_COLOR']))
    
    # b = QBrush()
    tmp = style['COLOR'].split(',')
    c = qRgba(int(tmp[0]), int(tmp[1]), int(tmp[2]), int(tmp[3]))
    mapRubberBand.setFillColor(c)
    


def SetDefaultFootprint3DStyle(layer):
    ''' Platform 3D Symbol '''
    material = QgsPhongMaterialSettings()
    material.setDiffuse(QColor(255, 0, 0))
    material.setAmbient(QColor(255, 0, 0))
    symbol = QgsPolygon3DSymbol()
    symbol.setAltitudeClamping(2)
    symbol.setMaterial(material)

    renderer = QgsVectorLayer3DRenderer()
    renderer.setLayer(layer)
    renderer.setSymbol(symbol)
    layer.setRenderer3D(renderer)
    return


def SetDefaultTrajectoryStyle(mapRubberBand:QgsRubberBand):
    ''' Trajectory Symbol '''
    style = S.getTrajectory('DEFAULT')
    
    mapRubberBand.setStrokeColor(QColor(style['COLOR']))
    mapRubberBand.setWidth(int(style['WIDTH']))
    mapRubberBand.setLineStyle(Qt.PenStyle.DashDotLine)
    

def SetDefaultPlatformStyle(marker:FmvSvgMarkerItem, platform='DEFAULT'):
    ''' Platform Symbol '''
    style = S.getPlatform(platform)
    size = int(style['SIZE'])
    marker.setup(style['NAME'], 0.5, 0.5, size, size)
    if not marker.isValid():
        qgsu.showUserAndLogMessage(
            "", "FMV: platform SVG missing or invalid: " + str(style['NAME']),
            onlyLog=True)
    return


def SetDefaultPlatform3DStyle(layer):
    ''' Platform 3D Symbol '''
    material = QgsPhongMaterialSettings()
    material.setDiffuse(QColor(255, 0, 0))
    material.setAmbient(QColor(255, 0, 0))
    symbol = QgsPoint3DSymbol()
    symbol.setShape(1)
    S = {}
    S['radius'] = 20
    symbol.setShapeProperties(S)
    symbol.setAltitudeClamping(2)
    symbol.setMaterial(material)

    renderer = QgsVectorLayer3DRenderer()
    renderer.setLayer(layer)
    renderer.setSymbol(symbol)
    layer.setRenderer3D(renderer)
    return


def SetDefaultTrajectory3DStyle(layer):
    ''' Trajectory 3D Symbol '''
    material = QgsPhongMaterialSettings()
    material.setDiffuse(QColor(0, 0, 255))
    material.setAmbient(QColor(0, 0, 255))
    symbol = QgsLine3DSymbol()

    symbol.setWidth(5)
    symbol.setAltitudeClamping(2)
    symbol.setMaterial(material)

    renderer = QgsVectorLayer3DRenderer()
    renderer.setLayer(layer)
    renderer.setSymbol(symbol)
    layer.setRenderer3D(renderer)
    return


def SetDefaultFrameAxis3DStyle(layer):
    ''' Frame Axis 3D Symbol '''
    material = QgsPhongMaterialSettings()
    material.setDiffuse(QColor(0, 0, 255))
    material.setAmbient(QColor(0, 0, 255))
    symbol = QgsLine3DSymbol()

    symbol.setWidth(5)
    symbol.setAltitudeClamping(2)
    symbol.setMaterial(material)

    renderer = QgsVectorLayer3DRenderer()
    renderer.setLayer(layer)
    renderer.setSymbol(symbol)
    layer.setRenderer3D(renderer)
    return


def SetDefaultBeams3DStyle(layer):
    ''' Beams 3D Symbol '''
    material = QgsPhongMaterialSettings()
    material.setDiffuse(QColor(255, 255, 255))
    material.setAmbient(QColor(255, 255, 255))
    symbol = QgsLine3DSymbol()

    symbol.setWidth(5)
    symbol.setAltitudeClamping(2)
    symbol.setMaterial(material)

    renderer = QgsVectorLayer3DRenderer()
    renderer.setLayer(layer)
    renderer.setSymbol(symbol)
    layer.setRenderer3D(renderer)
    return


def SetDefaultFrameCenterStyle(mapRubberBand:QgsRubberBand):
    ''' Frame Center Symbol '''
    style = S.getFrameCenterPoint()
    
    if style['NAME'] == 'cross':
        mapRubberBand.setIcon(QgsRubberBand.IconType.ICON_CROSS )

    
    mapRubberBand.setStrokeColor(QColor(style['LINE_COLOR']))
    mapRubberBand.setWidth(int(style['LINE_WIDTH']))
    mapRubberBand.setIconSize(int(style['SIZE']))

def SetDefaultFrameCenter3DStyle(layer):
    ''' Frame Center 3D Symbol '''
    material = QgsPhongMaterialSettings()
    material.setDiffuse(QColor(255, 255, 255))
    material.setAmbient(QColor(255, 255, 255))
    symbol = QgsPoint3DSymbol()
    symbol.setShape(1)
    S = {}
    S['radius'] = 20
    symbol.setShapeProperties(S)
    symbol.setAltitudeClamping(2)
    symbol.setMaterial(material)

    renderer = QgsVectorLayer3DRenderer()
    renderer.setLayer(layer)
    renderer.setSymbol(symbol)
    layer.setRenderer3D(renderer)
    return


def SetDefaultFrameAxisStyle(mapRubberBand:QgsRubberBand, sensor='DEFAULT'):
    ''' Line Symbol '''
    sensor_style = S.getSensor(sensor)
    style = S.getFrameAxis()


    mapRubberBand.setStrokeColor(QColor(sensor_style['OUTLINE_COLOR']))
    mapRubberBand.setWidth(int(style['OUTLINE_WIDTH']))
    mapRubberBand.setLineStyle(Qt.PenStyle.DashLine)


def SetDefaultPointStyle(mapItem, textItem):
    ''' Point Symbol '''
    style = S.getDrawingPoint()
    
    mPen = QPen()
    mPen.setColor(QColor(style['LINE_COLOR']))
    mPen.setWidth(int(style['LINE_WIDTH']))
    
    mapItem.setIconOutline(mPen)
    mapItem.setIconSize(int(style['SIZE']))
    
    textItem.setOutlineColor(QColor(style['LABEL_FONT_COLOR']))
    textItem.setFillColor( QColor(style['LABEL_FONT_COLOR']) )
    
    font = QFont()
    font.setFamily( style['LABEL_FONT'] )
    font.setPointSize( style['LABEL_FONT_SIZE'] )
    textItem.setFont( font )
    
    return



def SetDefaultBeamsStyle(mapRubberBand:QgsRubberBand, beam='DEFAULT'):
    ''' Beams Symbol'''
    style = S.getBeam(beam)
    
    mapRubberBand.setStrokeColor( QColor.fromRgba(style['COLOR']) );

# TODO : Update layer symbology if draw color change?
# def UpdateStylesDrawLayers(NameSpace):
#     ''' Update Symbology Drawing Layers '''
#     s = QSettings()
#     pointLyr = qgsu.selectLayerByName(Point_lyr, groupName)
#     if pointLyr is None:
#         return
#
#     style = S.getDrawingPoint()
#     LINE_COLOR = s.value(NameSpace + "/Options/drawings/points/pen")
#
#     symbol = QgsMarkerSymbol.createSimple(
#         {'name': style["NAME"],
#          'line_color': LINE_COLOR.name(),
#          'line_width': s.value(NameSpace + "/Options/drawings/points/width"),
#          'size': style["SIZE"]})
#
#     renderer = QgsSingleSymbolRenderer(symbol)
#     pointLyr.setRenderer(renderer)
#     CommonLayer(pointLyr)
#
#     linelyr = qgsu.selectLayerByName(Line_lyr, groupName)
#     if linelyr is None:
#         return
#
#     style = S.getDrawingLine()
#     symbol = linelyr.renderer().symbol()
#
#     COLOR = s.value(NameSpace + "/Options/drawings/lines/pen")
#
#     symbol.setColor(COLOR.name())
#     symbol.setWidth(s.value(NameSpace + "/Options/drawings/lines/width"))
#     CommonLayer(linelyr)
#
#     polyLyr = qgsu.selectLayerByName(Polygon_lyr, groupName)
#     if polyLyr is None:
#         return
#
#     style = S.getDrawingPolygon()
#
#     OUTLINE_COLOR = s.value(NameSpace + "/Options/drawings/polygons/pen")
#     COLOR = s.value(NameSpace + "/Options/drawings/polygons/brush")
#
#     fill_sym = QgsFillSymbol.createSimple({'color': COLOR.name(),
#                                        'outline_color': OUTLINE_COLOR.name(),
#                                        'outline_style': style['OUTLINE_STYLE'],
#                                        'outline_width': s.value(NameSpace + "/Options/drawings/polygons/width")})
#
#     renderer = QgsSingleSymbolRenderer(fill_sym)
#     polyLyr.setRenderer(renderer)
#     CommonLayer(polyLyr)
#     QApplication.processEvents()
#     return


def addLayer(layer, loadInLegend=True, group=None, isSubGroup=False):
    """
    Add one or several layers to the QGIS session and layer registry.
    @param layer: The layer object or list with layers  to add the QGIS layer registry and session.
    @param loadInLegend: True if this layer should be added to the legend.
    :return: The added layer
    """
    global groupName
    if not hasattr(layer, "__iter__"):
        layer = [layer]
    if group is not None:
        _layerreg.addMapLayers(layer, False)
        root = _layerreg.layerTreeRoot()
        if isSubGroup:
            vg = root.findGroup(groupName)
            g = vg.findGroup(group)
            g.insertChildNode(0, QgsLayerTreeLayer(layer[0]))
        else:
            g = root.findGroup(group)
            g.insertChildNode(0, QgsLayerTreeLayer(layer[0]))
    else:
        _layerreg.addMapLayers(layer, loadInLegend)
    return layer


def addLayerNoCrsDialog(layer, loadInLegend=True, group=None, isSubGroup=False):
    '''
    Tries to add a layer from layer object
    Same as the addLayer method, but it does not ask for CRS, regardless of current
    configuration in QGIS settings
    '''
    settings = QSettings()
    prjSetting3 = settings.value('/Projections/defaultBehavior')
    settings.setValue('/Projections/defaultBehavior', '')
    layer = addLayer(layer, loadInLegend, group, isSubGroup)
    settings.setValue('/Projections/defaultBehavior', prjSetting3)
    QApplication.processEvents()
    return layer


def _toQgsField(f):
    ''' Create QgsFiel '''
    if isinstance(f, QgsField):
        return f
    return QgsField(f[0], TYPE_MAP.get(f[1], QMetaType.Type.QString))


def newPointsLayer(filename, fields, crs, name=None, geometryType=Point, encoding=encoding):
    ''' Create new Point Layer '''
    return newVectorLayer(filename, fields, geometryType, crs, name, encoding)


def newLinesLayer(filename, fields, crs, name=None, geometryType=Line, encoding=encoding):
    ''' Create new Line Layer '''
    return newVectorLayer(filename, fields, geometryType, crs, name, encoding)


def newPolygonsLayer(filename, fields, crs, name=None, encoding=encoding):
    ''' Create new Polygon Layer '''
    return newVectorLayer(filename, fields, Polygon, crs, name, encoding)


def newVectorLayer(filename, fields, geometryType, crs, name=None, encoding=encoding):
    '''
    Creates a new vector layer
    @param filename: The filename to store the file. The extensions determines the type of file.
    If extension is not among the supported ones, a shapefile will be created and the file will
    get an added '.shp' to its path.
    If the filename is None, a memory layer will be created
    @param fields: the fields to add to the layer. Accepts a QgsFields object or a list of tuples (field_name, field_type)
    Accepted field types are basic Python types str, float, int and bool
    @param geometryType: The type of geometry of the layer to create.
    @param crs: The crs of the layer to create. Accepts a QgsCoordinateSystem object or a string with the CRS authId.
    @param encoding: The layer encoding
    '''
    if isinstance(crs, str):
        crs = QgsCoordinateReferenceSystem(crs)
    if filename is None:
        uri = geometryType
        if crs.isValid():
            uri += '?crs=' + crs.authid() + '&'
        fieldsdesc = ['field=' + f for f in fields]

        fieldsstring = '&'.join(fieldsdesc)
        uri += fieldsstring

        if name is None:
            name = "mem_layer"
        layer = QgsVectorLayer(uri, name, 'memory')

    else:
        formats = QgsVectorFileWriter.supportedFiltersAndFormats()
        OGRCodes = {}
        for (key, value) in formats.items():
            extension = str(key)
            extension = extension[extension.find('*.') + 2:]
            extension = extension[:extension.find(' ')]
            OGRCodes[extension] = value

        extension = os.path.splitext(filename)[1][1:]
        if extension not in OGRCodes:
            extension = 'shp'
            filename = filename + '.shp'

        if isinstance(fields, QgsFields):
            qgsfields = fields
        else:
            qgsfields = QgsFields()
            for field in fields:
                qgsfields.append(_toQgsField(field))

        QgsVectorFileWriter(filename, encoding, qgsfields,
                            geometryType, crs, OGRCodes[extension])

        layer = QgsVectorLayer(filename, os.path.basename(filename), 'ogr')

    return layer
