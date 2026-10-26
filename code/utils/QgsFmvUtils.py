# -*- coding: utf-8 -*-
from configparser import ConfigParser
from datetime import datetime
import inspect
import json
from math import sin, cos, atan, tan, sqrt, radians, pi, degrees
import os
from os.path import dirname, abspath
import platform
import shutil
from qgis.PyQt.QtCore import (QSettings,
                              QUrl)
from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtGui import QImage, QPainter
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtWidgets import QFileDialog
from qgis.core import (QgsApplication,
                       QgsRectangle,
                       QgsPointXY,
                       QgsNetworkAccessManager,
                       QgsTask,
                       QgsCoordinateReferenceSystem,
                       QgsProject,
                       QgsCoordinateTransform,
                       QgsRasterLayer,
                       Qgis as QGis)
# from subprocess import Popen, PIPE, STARTF_USESHOWWINDOW, STARTUPINFO, check_output, DEVNULL
import subprocess
import threading
import collections
import socket
from queue import Queue, Empty

from osgeo import gdal, osr

from QGIS_FMV.geo import sphere
from QGIS_FMV.klvdata.element import UnknownElement
from QGIS_FMV.klvdata.streamparser import StreamParser
from QGIS_FMV.utils.QgsFmvStreamBuffer import KlvFramer, TimedPacketBuffer
from QGIS_FMV.klvdata.universalset import isFlatUniversalStream
from QGIS_FMV.utils.KadasFmvLayers import (addLayerNoCrsDialog,
                                         HideFootPrintData,
                                         HideBeamsData,
                                         ExpandLayer,
                                         UpdateFootPrintData,
                                         UpdateTrajectoryData,
                                         UpdateBeamsData,
                                         UpdatePlatformData,
                                         UpdateFrameCenterData,
                                         UpdateFrameAxisData,
                                         SetcrtSensorSrc,
                                         SetcrtPltTailNum,
                                         RemoveAllDrawings,
                                         GetMapItems)
from QGIS_FMV.utils.QgsUtils import QgsUtils as qgsu

parser = ConfigParser()
parser.read(os.path.join(dirname(dirname(abspath(__file__))), 'settings.ini'))

frames_g = parser['LAYERS']['frames_g']
Reverse_geocoding_url = parser['GENERAL']['Reverse_geocoding_url']
min_buffer_size = int(parser['GENERAL']['min_buffer_size'])
max_vert_angle = int(parser['GENERAL']['max_vert_angle'])
# streaming (beta) - tolerate an older settings.ini
stream_latency_ms = parser.getint('GENERAL', 'stream_latency_ms', fallback=1200)
stream_buffer_size = parser.getint('GENERAL', 'stream_buffer_size', fallback=256)
# streaming (beta) - the tee re-emits the picture on a local socket. Both
# ends get a large receive buffer because a GUI hiccup longer than the
# buffer holds is a dropped datagram, which costs whole video frames.
STREAM_SOCKET_BUFFER = 33554432
# 7 x 188, so a lost datagram destroys whole transport stream packets
# instead of straddling two of them
STREAM_UDP_PACKET_SIZE = 1316
STREAM_UDP_FIFO_SIZE = 5000000
# how many ports after the preferred one to try before letting the system
# choose. A predictable port is worth a few extra probes
STREAM_PORT_SCAN = 10
Platform_lyr = parser['LAYERS']['Platform_lyr']
Footprint_lyr = parser['LAYERS']['Footprint_lyr']
FrameCenter_lyr = parser['LAYERS']['FrameCenter_lyr']
# dtm_buffer_size is no longer read: the model is sampled through the
# raster provider instead of being loaded as a window around one packet
# steepest ground the line of sight walk is allowed to assume, rise over
# run. It only has to be an upper bound: too generous costs a few extra
# samples, too small lets the walk step over a thin ridge and report the
# far side of the mountain. 12 is about 85 degrees.
dtm_max_slope = parser.getfloat('GENERAL', 'dtm_max_slope', fallback=12.0)
# how far the line of sight is followed, in metres
dtm_max_range = parser.getfloat('GENERAL', 'dtm_max_range', fallback=60000.0)
# a hole in the model, a lake mask or a void, is stepped over one pixel at
# a time. Past this much of it the ray has left the model for good
DTM_MAX_HOLE_METRES = 5000.0
# a ray running almost parallel to the ground can take very small steps
# for a long way. Past this it is grazing rather than hitting
DTM_MAX_ITERATIONS = 4000
# optional: it is overwritten with the platform path just below, so a
# settings.ini without it must not stop the module importing
ffmpegConf = parser.get('GENERAL', 'ffmpeg', fallback='')

windows = platform.system() == 'Windows'

if windows:
    ffmpegConf = os.path.join(QgsApplication.applicationDirPath(), "..", "opt", "ffmpeg")
else:
    ffmpegConf = '/usr/bin'

#try:
#    from homography import from_points
#except ImportError:
#    None

try:
    from cv2 import (COLOR_BGR2RGB,
                     cvtColor,
                     COLOR_GRAY2RGB,
                     findHomography)
    import numpy as np
except ImportError:
    None

try:
    from pydevd import *
except ImportError:
    None

settings = QSettings()
tm = QgsApplication.taskManager()
groupName = None
windows = platform.system() == 'Windows'

xSize = 0
ySize = 0

defaultTargetWidth = 200.0

iface, \
geotransform , \
geotransform_affine, \
gcornerPointUL, \
gcornerPointUR, \
gcornerPointLR, \
gcornerPointLL, \
gframeCenterLon, \
gframeCenterLat, \
frameCenterElevation, \
sensorLatitude, \
sensorLongitude, \
sensorTrueAltitude = [None] * 13

centerMode = 0

WGS84_CRS = QgsCoordinateReferenceSystem("EPSG:4326")

# The elevation model is not read into memory. Heights come from the
# raster provider, which keeps its own block cache, so there is no window
# and therefore no bounds for the walk to fall outside of.
dtm_layer = None
dtm_provider = None
dtm_to_layer = None
dtm_from_layer = None
# layer units per metre on each axis, so the walk can step in metres
# whatever the model is projected in
dtm_metres_per_unit = (1.0, 1.0)
# smallest advance, and the clearance under which the ray counts as
# touching. Both are derived from the pixel size in initElevationModel
dtm_step_floor = 2.5
dtm_graze = 1.0
dtm_pixel_metres = 10.0

tLastLon = 0.0
tLastLat = 0.0

_settings = {}

if windows:
    ffmpeg_path = os.path.join(ffmpegConf, 'ffmpeg.exe')
    ffprobe_path = os.path.join(ffmpegConf, 'ffprobe.exe')
else:
    ffmpeg_path = os.path.join(ffmpegConf, 'ffmpeg')
    ffprobe_path = os.path.join(ffmpegConf, 'ffprobe')


class NonBlockingStreamReader:
    ''' Read the splitter metadata pipe into a time indexed ring buffer.

        The pipe carries no packet boundaries of its own, so KlvFramer finds
        them; see QgsFmvStreamBuffer for why the previous 16 byte scan could
        not work. The buffer is bounded: if the player stalls, packets are
        dropped rather than accumulated, which keeps memory flat and stops
        the metadata drifting further and further behind the picture.
    '''

    def __init__(self, process, buffer_size=None):
        self._p = process
        self.stopped = False
        self.framer = KlvFramer()
        self.buffer = TimedPacketBuffer(maxlen=buffer_size or stream_buffer_size)
        self.packetCount = 0

        def _populateBuffer():
            reader = process.stdout
            # read1() returns as soon as anything is available, read() would
            # block until the full request is met and add latency
            readChunk = getattr(reader, 'read1', None) or reader.read
            while self._p.poll() is None and not self.stopped:
                try:
                    chunk = readChunk(65536)
                except (ValueError, OSError):
                    break
                if not chunk:
                    qgsu.showUserAndLogMessage('', 'reader got end of stream.', onlyLog=True)
                    break
                for packet in self.framer.feed(chunk):
                    self.buffer.put(packet)
                    self.packetCount += 1

            if self.stopped:
                qgsu.showUserAndLogMessage('', 'NonBlockingStreamReader ended because stop signal received.', onlyLog=True)

        self._t = threading.Thread(target=_populateBuffer)
        self._t.daemon = True
        self._t.start()

    def get(self, latency=0.0):
        ''' Packet matching what is on screen, latency seconds ago. '''
        return self.buffer.get(latency)

    def size(self):
        return self.buffer.size()


# Splitter class for streaming.
# Reads input stream and split AV to Port: (src + 10), and reads metadata from stdout to a Queue,
# later passed to the metadata decoder.
class Splitter(threading.Thread):

    def __init__(self, cmds, type="ffmpeg"):
        self.stdout = None
        self.stderr = None
        self.cmds = cmds
        self.type = type
        self.p = None
        self.nbsr = None
        threading.Thread.__init__(self)

    def run(self):
        if self.type == "ffmpeg":
            self.cmds.insert(0, ffmpeg_path)
        else:
            self.cmds.insert(0, ffprobe_path)

        #qgsu.showUserAndLogMessage("", "starting Splitter on thread:" + str(threading.current_thread().ident), onlyLog=True)
        #qgsu.showUserAndLogMessage("", "with args:" + ' '.join(self.cmds), onlyLog=True)

        # Hide shell windows that pops up on windows.
        if windows:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE

        self.p = subprocess.Popen(self.cmds, startupinfo=startupinfo, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
        # Dont us _spawn here as it will DeadLock, and the splitter won't work
        #self.p = _spawn(self.cmds)
        self.nbsr = NonBlockingStreamReader(self.p)
        self.nbsr._t.join()
        qgsu.showUserAndLogMessage("", "Splitter thread ended.", onlyLog=True)


def _pickFreeUdpPort(preferred):
    ''' A local port the player will be able to listen on.

        The tee only sends, and a sender never binds its destination, so
        what is tested here is whether QMediaPlayer will be able to receive
        on it. Neighbouring ports are tried before giving up to the system:
        an OS assigned port works, since both the tee and the player take it
        from the same destPort, but it cannot be predicted, firewalled or
        recognised in a log.

        Windows refuses more than ports that are in use. Hyper-V, WSL and
        WinNAT reserve whole blocks, and binding inside one of those fails
        with a permission error rather than an address in use, so the reason
        is logged instead of being flattened to busy.
    '''
    refusal = ''
    for candidate in list(range(preferred, preferred + STREAM_PORT_SCAN)) + [0]:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.bind(('127.0.0.1', candidate))
            port = probe.getsockname()[1]
            if candidate != preferred:
                qgsu.showUserAndLogMessage(
                    '', 'Stream: local port ' + str(preferred) + ' refused (' +
                    refusal + '), listening on ' + str(port) + ' instead.',
                    onlyLog=True)
            return port
        except OSError as e:
            if not refusal:
                refusal = str(e)
            continue
        finally:
            probe.close()
    return preferred


class StreamMetaReader():
    ''' Live metadata reader (beta).

        One ffmpeg subscribes to the source once and tees it: audio and video
        are copied to a local port for QMediaPlayer, the data track goes to a
        pipe read here. A live source cannot be opened twice, so the split has
        to happen upstream of both consumers.
    '''

    def __init__(self, video_path, latency_ms=None):
        self.split = video_path.split(":")
        self.srcProtocol = self.split[0]
        self.srcHost = self.split[1].lstrip('/')
        self.srcPort = int(self.split[2])
        self.destPort = _pickFreeUdpPort(self.srcPort + 10)
        self.latency = max(0.0, (stream_latency_ms if latency_ms is None else latency_ms) / 1000.0)
        # kept so the player can treat this like a BufferedMetaReader
        self.klv_index = 0
        self.disposed = False
        self.connection = (self.srcProtocol + '://' + self.srcHost + ':' +
                           str(self.srcPort) + '?buffer_size=' + str(STREAM_SOCKET_BUFFER))
        self.connectionDest = self._destUrl()
        self._startSplitter()

    def _destUrl(self):
        ''' Local leg the tee writes the picture to. '''
        return ('udp://127.0.0.1:' + str(self.destPort) +
                '?pkt_size=' + str(STREAM_UDP_PACKET_SIZE) +
                '&buffer_size=' + str(STREAM_SOCKET_BUFFER))

    def playerUrl(self):
        ''' URL QMediaPlayer has to open to see this stream.

            Never the source address: the tee already holds that, and a
            live source cannot be subscribed to twice.
        '''
        return ('udp://127.0.0.1:' + str(self.destPort) +
                '?buffer_size=' + str(STREAM_SOCKET_BUFFER) +
                '&fifo_size=' + str(STREAM_UDP_FIFO_SIZE) +
                '&overrun_nonfatal=1')

    def _startSplitter(self):
        ''' Spawn the ffmpeg that tees the source.

            The local leg is plain MPEG-TS over UDP, not rtp_mpegts. RTP
            makes the reader hold packets in a resequencing queue and drop
            the whole queue whenever its delay is reached, which on a
            localhost hop cost about a third of the video packets in
            testing against roughly a twentieth over plain UDP. Nothing is
            re-encoded either way, -c copy passes the original bitstream
            through untouched.
        '''
        self.connectionDest = self._destUrl()
        self.splitter = Splitter(['-i', self.connection, '-c', 'copy', '-map', '0:v?', '-map', '0:a?', '-f', 'mpegts', self.connectionDest, '-map', '0:d?', '-f', 'data', '-'])
        self.splitter.start()
        qgsu.showUserAndLogMessage("", "Splitter started on port " + str(self.destPort) + ".", onlyLog=True)

    def isRunning(self):
        ''' False only when the tee is known to be down. '''
        if self.disposed:
            return False
        reader = self._reader()
        if reader is not None and reader.stopped:
            return False
        process = getattr(self.splitter, 'p', None)
        if process is None:
            # the splitter thread has not spawned ffmpeg yet
            return True
        return process.poll() is None

    def ensureRunning(self):
        ''' Bring the tee back up after the player was closed.

            Closing the player disposes this reader, which kills ffmpeg.
            The manager keeps the row, so reopening it has to restart the
            splitter or the player would sit on a silent local port and
            show nothing but black.

            Returns True when a restart happened.
        '''
        if self.isRunning():
            return False
        # the port we used is ours again now that ffmpeg is gone
        self.destPort = _pickFreeUdpPort(self.destPort)
        self.disposed = False
        self._startSplitter()
        return True

    def setLatency(self, latency_ms):
        ''' Tune how far back in the buffer the metadata is taken. '''
        self.latency = max(0.0, latency_ms / 1000.0)

    def _reader(self):
        # the splitter builds it on its own thread, it may not exist yet
        return getattr(getattr(self, 'splitter', None), 'nbsr', None)

    def getSize(self):
        reader = self._reader()
        return reader.size() if reader is not None else 0

    def bufferSpan(self):
        ''' Seconds of metadata currently held, for diagnostics. '''
        reader = self._reader()
        return reader.buffer.span() if reader is not None else 0.0

    def get(self, _t=None):
        reader = self._reader()
        if reader is None:
            return None
        return reader.get(self.latency)

    def dispose(self):
        self.disposed = True
        reader = self._reader()
        if reader is not None:
            reader.stopped = True
            reader.buffer.clear()
        # kill the process if open, releases source port
        try:
            if self.splitter.p is not None:
                self.splitter.p.kill()
                qgsu.showUserAndLogMessage("", "Splitter Popen process killed.", onlyLog=True)
        except OSError:
            # can't kill a dead proc
            pass


class BufferedMetaReader():
    ''' Non-Blocking metadata reader with buffer  '''
    # if we go lower, the buffer will shrink drastically and the video may hang.
    def __init__(self, video_path, klv_index=0, pass_time=250, interval=1000):
        ''' Constructor '''
        # don't go too low with pass_time or we won't catch any metadata at
        # all.
        # 8 x 500 = 4000ms buffer time
        # min_buffer_size x buffer_interval = Miliseconds buffer time
        self.video_path = video_path
        self.pass_time = pass_time
        self.interval = interval
        self._meta = {}
        self._min_buffer_size = min_buffer_size
        self.klv_index = klv_index
        self._initialize('00:00:00.0000', self._min_buffer_size)

    def _initialize(self, start, size):
        self.bufferParalell(start, size)

    def _check_buffer(self, start):
        self.bufferParalell(start, self._min_buffer_size)

    def getSize(self, t):
        size = 0
        s_date = datetime.strptime(t, '%H:%M:%S.%f')
        last_date = None
        #calculate buffer size ahead of supplied time (contiguous values only).       
        od = collections.OrderedDict(sorted(self._meta.items()))
        for ele in od.keys():       
            c_date = datetime.strptime(ele, '%H:%M:%S.%f')
            if last_date == None:
                last_date = c_date
                continue

            if c_date > s_date:
                #qgsu.showUserAndLogMessage("", "Comparing: ele:" + ele + " greater than t (as date):" + t + " : yes", onlyLog=True)
                #qgsu.showUserAndLogMessage("", "c_date:" + c_date.strftime('%H:%M:%S.%f') + " last_date:" + last_date.strftime('%H:%M:%S.%f'), onlyLog=True)
                delta_millisec = (c_date - last_date).microseconds / 1000 + (c_date - last_date).seconds * 1000
                #qgsu.showUserAndLogMessage("", "delta: " + str(delta_millisec), onlyLog=True)
                if delta_millisec <= self.interval:
                    size += 1
                    #qgsu.showUserAndLogMessage("", "Smaller or equal than pass_time:" + str(delta_millisec), onlyLog=True)
                else:
                    #qgsu.showUserAndLogMessage("", "Greater than pass_time:" + str(delta_millisec), onlyLog=True)
                    break
            
            last_date = c_date            
            
        return size

    def bufferParalell(self, start, size):
        start_sec = _time_to_seconds(start)
        start_milisec = int(start_sec * 1000)
        
        for k in range(start_milisec, start_milisec + (size * self.interval), self.interval):
            cTime = k / 1000.0
            nTime = (k + self.pass_time) / 1000.0
            new_key = _seconds_to_time_frac(cTime)

            if new_key not in self._meta:
                # qgsu.showUserAndLogMessage("QgsFmvUtils", 'buffering: ' + _seconds_to_time_frac(cTime) + " to " + _seconds_to_time_frac(nTime), onlyLog=True)
                self._meta[new_key] = callBackMetadataThread(cmds=['-i', self.video_path,
                                                                   '-ss', new_key,
                                                                   '-to', _seconds_to_time_frac(
                                                                       nTime),
                                                                   '-map', '0:d:'+str(self.klv_index),
                                                                   '-f', 'data', '-'])
                self._meta[new_key].start()

    def get(self, t):
        ''' read a value and check the buffer '''
        value = b''
        # get the closest value for this time from the buffer
        s = t.split(".")
        new_t = ''
        try:
            milis = int(s[1][:-1])
            
            if self.interval > 1000:
               inte = 1000
               
            r_milis = round(milis / inte) * inte
            if r_milis != 1000:
                if r_milis < 1000:
                    new_t = s[0] + "." + str(r_milis) + "0"
                if r_milis < 100:
                    new_t = s[0] + ".0" + str(r_milis) + "0"
                if r_milis < 10:
                    new_t = s[0] + ".00" + str(r_milis) + "0"
            else:
                date = datetime.strptime(s[0], '%H:%M:%S')
                new_t = _add_secs_to_time(date, 1) + ".0000"
        except Exception:
            qgsu.showUserAndLogMessage(
                "", "wrong value for time, need . decimal" + t, onlyLog=True)
        try:
            # after skip, buffer may not have been initialized
            if new_t not in self._meta:
                qgsu.showUserAndLogMessage(
                    "", "Meta reader -> get: " + t + " cache: " + new_t + " values have not been init yet.", onlyLog=True)
                self._check_buffer(new_t)
                value = 'BUFFERING'
            elif self._meta[new_t].p is None:
                value = 'NOT_READY'
                qgsu.showUserAndLogMessage(
                    "", "Meta reader -> get: " + t + " cache: " + new_t + " values not ready yet.", onlyLog=True)
            elif self._meta[new_t].p.returncode is None:
                value = 'NOT_READY'
                qgsu.showUserAndLogMessage(
                    "", "Meta reader -> get: " + t + " cache: " + new_t + " values not ready yet.", onlyLog=True)
            elif self._meta[new_t].stdout:
                value = self._meta[new_t].stdout
            else:
                qgsu.showUserAndLogMessage(
                    "", "Meta reader -> get: " + t + " cache: " + new_t + " values ready but empty.", onlyLog=True)
            
            bSize = self.getSize(t)            
            self._check_buffer(new_t)
            
            #debug            
            #qgsu.showUserAndLogMessage("Buffer size:" + str(bSize), "Buffer size:" + str(bSize), onlyLog=False)

        except Exception as e:
            qgsu.showUserAndLogMessage(
                "", "No value found for: " + t + " rounded: " + new_t + " e:" + str(e), onlyLog=True)

        # qgsu.showUserAndLogMessage("QgsFmvUtils", "meta_reader -> get: " + t + " return code: "+ str(self._meta[new_t].p.returncode), onlyLog=True)
        # qgsu.showUserAndLogMessage("QgsFmvUtils", "meta_reader -> get: " + t + " cache: "+ new_t +" len: " + str(len(value)), onlyLog=True)

        return value

    def dispose(self):
        ''' Kill the ffmpeg processes still buffering for this video. '''
        for thread in list(self._meta.values()):
            try:
                if thread.p is not None and thread.p.poll() is None:
                    thread.p.kill()
            except Exception:
                pass
        self._meta = {}


class callBackMetadataThread(threading.Thread):
    ''' CallBack metadata in other thread  '''

    def __init__(self, cmds = []):
        self.cmds = cmds
        self.p = None
        self.stdout = b''
        threading.Thread.__init__(self)
        # never keep the host alive: these threads sit in communicate()
        self.daemon = True
    
    def setCmds(self, cmds):
        self.cmds = cmds
        
    def run(self):
        #qgsu.showUserAndLogMessage("", "callBackMetadataThread run: commands:" + str(self.cmds), onlyLog=True)                                        
        self.p = _spawn(self.cmds)
        # print (self.cmds)
        self.stdout, _ = self.p.communicate()
        #qgsu.showUserAndLogMessage("", "callBackMetadataThread run: stdout:" + str(self.stdout), onlyLog=True)  
        # print (self.stdout)
        # print (_)
        


def AddVideoToSettings(row_id, path):
    ''' Add video to settings list '''
    settings.setValue(getNameSpace() + "/Manager_List/" + row_id, path)


def RemoveVideoToSettings(row_id):
    ''' Remove video in settings list '''
    settings.remove(getNameSpace() + "/Manager_List/%s" % row_id)


def getVideoManagerList():
    ''' Get Video Manager List '''
    VideoList = []
    try:
        settings.beginGroup(getNameSpace() + "/Manager_List")
        VideoList = settings.childKeys()
        settings.endGroup()
    except Exception:
        None
    return VideoList


def getVideoFolder(video_file):
    ''' Get or create Video Temporal folder '''
    home = os.path.expanduser("~")

    qgsu.createFolderByName(home, "QGIS_FMV")

    root, _ = os.path.splitext(os.path.basename(video_file))
    homefmv = os.path.join(home, "QGIS_FMV")

    qgsu.createFolderByName(homefmv, root)
    return os.path.join(homefmv, root)


def RemoveVideoFolder(filename):
    ''' Remove video temporal folder if exist '''
    file, _ = os.path.splitext(filename)
    folder = getVideoFolder(file)
    try:
        shutil.rmtree(folder, ignore_errors=True)
    except Exception:
        None
    return


def getNameSpace():
    ''' Get plugin name space '''
    namespace = _callerName().split(".")[0]
    return namespace


def setCenterMode(mode, interface):
    ''' Set map center mode '''
    global centerMode, iface
    centerMode = mode
    iface = interface

def getKlvStreamIndex(videoPath, islocal=False):
    if islocal:
        return 0
    else:
        #search for klv data in 5 streams
        for i in range(6):
            p = _spawn(['-i', videoPath,
                        '-ss', '00:00:00',
                        '-to', '00:00:01',
                        '-map', '0:d:'+str(i),
                        '-f', 'data', '-'])

            stdout_data, _ = p.communicate()
            
            if stdout_data == b'':
                continue
            else:
                #look if stream has valid klv data
                if (b'\x06\x0e+4\x02\x0b\x01\x01\x0e\x01\x03\x01\x01\x00\x00\x00' in stdout_data
                        or b'\x06\x0e+4\x02\x01\x01\x01\x0e\x01\x01\x02\x01\x01\x00\x00' in stdout_data
                        # legacy pre 0601 streams carry no wrapping set key to
                        # look for, so they are recognised on their layout
                        or isFlatUniversalStream(stdout_data, StreamParser.parsers)):
                    return i
                else:
                    qgsu.showUserAndLogMessage("", "skipping stream " + str(i) + " not a klv stream.", onlyLog=True)
                    continue
                
        qgsu.showUserAndLogMessage("Error interpreting klv data, metadata cannot be read.", "the parser did not recognize KLV data", level=QGis.Warning)
        return 0

def parseReverseGeocoding(payload):
    ''' Address string out of a reverse geocoding answer, '-' if unusable. '''
    try:
        data = json.loads(payload)
        address = data.get("address", {})
        state = address.get("state")
        for key in ("village", "town"):
            if key in address and state:
                return address[key] + ", " + state
        return data.get("display_name", "-")
    except Exception:
        return "-"


def requestReverseGeocoding(lat, lon, onResult):
    ''' Ask the geocoding service for an address, without blocking.

        onResult(text) is called later from the event loop. Returns the reply
        so the caller can abort it, or None when nothing was sent.
    '''
    if not Reverse_geocoding_url or lat is None or lon is None:
        return None

    try:
        url = QUrl(Reverse_geocoding_url.format(str(lat), str(lon)))
        reply = QgsNetworkAccessManager.instance().get(QNetworkRequest(url))
    except Exception:
        qgsu.showUserAndLogMessage(
            "", "requestReverseGeocoding: request could not be sent.", onlyLog=True)
        return None

    def finished():
        try:
            onResult(parseReverseGeocoding(bytes(reply.readAll().data())))
        except Exception:
            qgsu.showUserAndLogMessage(
                "", "requestReverseGeocoding: failed to get address from reverse geocoding service.", onlyLog=True)
        finally:
            reply.deleteLater()

    reply.finished.connect(finished)
    return reply


def getVideoLocationInfo(videoPath, islocal=False, klv_folder=None, klv_index=0):
    """ Get basic location info about the video """
    location = []
    try:
        if islocal:
            dataFile = os.path.join(klv_folder, "0.0.klv")
            f = open(dataFile, 'rb')
            stdout_data = f.read()
        else:
            p = _spawn(['-i', videoPath,
                        '-ss', '00:00:00',
                        '-to', '00:00:01',
                        '-map', '0:d:'+str(klv_index),
                        '-f', 'data', '-'])

            stdout_data, _ = p.communicate()
            #qgsu.showUserAndLogMessage("Video Loc info raw result", stdout_data, onlyLog=True)
        if stdout_data == b'':
            #qgsu.showUserAndLogMessage("Error interpreting klv data, metadata cannot be read.", "the parser did not recognize KLV data", level=QGis.Warning)                                                                                                                                    
            return
        for packet in StreamParser(stdout_data):
            if isinstance(packet, UnknownElement):
                qgsu.showUserAndLogMessage(
                    "Error interpreting klv data, metadata cannot be read.", "the parser did not recognize KLV data", level=QGis.Warning)
                continue
            packet.MetadataList()
            
            centerLat = packet.FrameCenterLatitude
            centerLon = packet.FrameCenterLongitude
            
            #Target maybe unavailable because of horizontal view
            if centerLat == None and centerLon == None:
                centerLat = packet.SensorLatitude
                centerLon = packet.SensorLongitude
            
            # The reverse geocoding used to run here behind a nested
            # QEventLoop, which re-entered the event loop while the manager
            # row was still being built. See requestReverseGeocoding().
            loc = "-"

            location = [centerLat, centerLon, loc]

            qgsu.showUserAndLogMessage("", "Got Location: lon: " + str(centerLon) +
                                       " lat: " + str(centerLat) + " location: " + str(loc), onlyLog=True)

            break
        else:

            qgsu.showUserAndLogMessage(QCoreApplication.translate(
                "QgsFmvUtils", "This video doesn't have Metadata ! : "))

    except Exception as e:
        qgsu.showUserAndLogMessage(QCoreApplication.translate(
            "QgsFmvUtils", "Video info callback failed! : "), str(e))

    return location


def pluginSetting(name, namespace=None, typ=None):

    def _find_in_cache(name, key):
        ''' Find key in QGIS settings '''
        try:
            for setting in _settings[namespace]:
                if setting["name"] == name:
                    return setting[key]
        except Exception:
            return None
        return None

    def _type_map(t):
        """Return setting python type"""
        if t == "bool":
            return bool
        elif t == "number":
            return float
        else:
            return str

    namespace = namespace or _callerName().split(".")[0]
    full_name = namespace + "/" + name
    if settings.contains(full_name):
        if typ is None:
            typ = _type_map(_find_in_cache(name, 'type'))
        v = settings.value(full_name, None, type=typ)
        return v
    else:
        return _find_in_cache(name, 'default')


def _callerName():
    ''' Get QGIS plugin name '''
    stack = inspect.stack()
    parentframe = stack[2][0]
    name = []
    module = inspect.getmodule(parentframe)
    name.append(module.__name__)
    if 'self' in parentframe.f_locals:
        name.append(parentframe.f_locals['self'].__class__.__name__)
    codename = parentframe.f_code.co_name
    if codename != '<module>':
        name.append(codename)
    del parentframe
    return ".".join(name)


def askForFiles(parent, msg=None, isSave=False, allowMultiple=False, exts="*"):
    ''' dialog for save or load files '''
    msg = msg or 'Select file'
    caller = _callerName().split(".")
    name = "/".join(["LAST_PATH", caller[-1]])
    namespace = caller[0]
    path = pluginSetting(name, namespace)
    f = None
    if not isinstance(exts, list):
        exts = [exts]
    extString = ";; ".join([" %s files (*.%s *.%s)" % (e.upper(), e, e.upper())
                            if e != "*" else "All files (*.*)" for e in exts])

    dlg = QFileDialog()
    
    if allowMultiple:
        ret = dlg.getOpenFileNames(parent, msg, path, '*.' + extString)
        if ret:
            f = ret[0]
        else:
            f = ret = None
    else:
        if isSave:
            ret = dlg.getSaveFileName(
                parent, msg, path, extString) or None
            if ret[0] != "":
                name, ext = os.path.splitext(ret[0])
                if not ext:
                    ret[0] += "." + exts[0]  # Default extension
        else:
            ret = dlg.getOpenFileName(
                parent, msg, path, extString) or None
        f = ret

    if f is not None:
        setPluginSetting(name, os.path.dirname(f[0]), namespace)

    return ret


def setPluginSetting(name, value, namespace=None):
    ''' Set plugin name in QGIS settings '''
    namespace = namespace or _callerName().split(".")[0]
    settings.setValue(namespace + "/" + name, value)


def askForFolder(parent, msg=None, options=QFileDialog.Option.ShowDirsOnly):
    ''' dialog for save or load folder '''
    msg = msg or 'Select folder'
    caller = _callerName().split(".")
    name = "/".join(["LAST_PATH", caller[-1]])
    namespace = caller[0]
    path = pluginSetting(name, namespace)
    folder = QFileDialog.getExistingDirectory(parent, msg, path, options)
    if folder:
        setPluginSetting(name, folder, namespace)
    return folder


def convertQImageToMat(img, cn=3):
    '''  Converts a QImage into an opencv MAT format  '''
    img = img.convertToFormat(QImage.Format.Format_RGB888)
    ptr = img.bits()
    ptr.setsize(img.sizeInBytes())
    return np.array(ptr).reshape(img.height(), img.width(), cn)


def convertMatToQImage(img, t=QImage.Format.Format_RGB888):
    '''  Converts an opencv MAT image to a QImage  '''
    height, width = img.shape[:2]
    if img.ndim == 3:
        rgb = cvtColor(img, COLOR_BGR2RGB)
    elif img.ndim == 2:
        rgb = cvtColor(img, COLOR_GRAY2RGB)
    else:
        raise Exception("Unstatistified image data format!")
    return QImage(rgb, width, height, t)


def SetGCPsToGeoTransform(cornerPointUL, cornerPointUR, cornerPointLR, cornerPointLL, frameCenterLon, frameCenterLat, ele):
    ''' Make Geotranform from pixel to lon lat coordinates '''
    gcps = []

    global gcornerPointUL, gcornerPointUR, gcornerPointLR, gcornerPointLL, gframeCenterLat, gframeCenterLon, geotransform_affine, geotransform
         
    gcornerPointUL = cornerPointUL
    gcornerPointUR = cornerPointUR
    gcornerPointLR = cornerPointLR
    gcornerPointLL = cornerPointLL
    
    gframeCenterLat = frameCenterLat
    gframeCenterLon = frameCenterLon

    Height = GetFrameCenter()[2]

    gcp = gdal.GCP(cornerPointUL[1], cornerPointUL[0],
                   Height, 0, 0, "Corner Upper Left", "1")
    gcps.append(gcp)
    gcp = gdal.GCP(cornerPointUR[1], cornerPointUR[0],
                   Height, xSize, 0, "Corner Upper Right", "2")
    gcps.append(gcp)
    gcp = gdal.GCP(cornerPointLR[1], cornerPointLR[0],
                   Height, xSize, ySize, "Corner Lower Right", "3")
    gcps.append(gcp)
    gcp = gdal.GCP(cornerPointLL[1], cornerPointLL[0],
                   Height, 0, ySize, "Corner Lower Left", "4")
    gcps.append(gcp)
    gcp = gdal.GCP(frameCenterLon, frameCenterLat, Height,
                   xSize / 2, ySize / 2, "Center", "5")
    gcps.append(gcp)

    geotransform_affine = gdal.GCPsToGeoTransform(gcps)
    

    src = np.float64(
        np.array([[0.0, 0.0], [xSize, 0.0], [xSize, ySize], [0.0, ySize], [xSize / 2.0, ySize / 2.0]]))
    dst = np.float64(
        np.array([[cornerPointUL[0], cornerPointUL[1]], [cornerPointUR[0], cornerPointUR[1]], [cornerPointLR[0], cornerPointLR[1]], [cornerPointLL[0], cornerPointLL[1]], [frameCenterLat, frameCenterLon]]))

    try:
    
        #geotransform = from_points(src, dst)
        geotransform, status = findHomography(src, dst)

    except Exception:
        pass

    if geotransform is None:
        qgsu.showUserAndLogMessage(
            "", "Unable to extract a geotransform.", onlyLog=True)

    return


def GetSensor():
    ''' Get Sensor values '''
    return [sensorLatitude, sensorLongitude, sensorTrueAltitude]


def GetFrameCenter():
    ''' Get Frame Center values '''
    global sensorTrueAltitude
    global frameCenterElevation
    global gframeCenterLat
    global gframeCenterLon
    # if sensor height is null, compute it from sensor altitude.
    if(frameCenterElevation is None):                                   
        if sensorTrueAltitude is not None:
            frameCenterElevation = sensorTrueAltitude - 500
        else:
            frameCenterElevation = 0
    return [gframeCenterLat, gframeCenterLon, frameCenterElevation]


def GetcornerPointUL():
    ''' Get Corner upper Left values '''
    return gcornerPointUL


def GetcornerPointUR():
    ''' Get Corner upper Right values '''
    return gcornerPointUR


def GetcornerPointLR():
    ''' Get Corner lower Right values '''
    return gcornerPointLR


def GetcornerPointLL():
    ''' Get Corner lower left values '''
    return gcornerPointLL


def GetGCPGeoTransform():
    ''' Return Geotransform '''
    return geotransform

def hasElevationModel():
    ''' Check if DEM is loaded '''
    return dtm_provider is not None


def SetImageSize(w, h):
    ''' Set Image Size '''
    global xSize, ySize
    xSize = w
    ySize = h
    return


def GetImageWidth():
    ''' Get Image Width '''
    return xSize


def GetImageHeight():
    ''' Get Image Height '''
    return ySize


def _check_output(cmds, t="ffmpeg"):
    ''' Check Output Commands in Python '''

    if t == "ffmpeg":
        cmds.insert(0, ffmpeg_path)
    else:
        cmds.insert(0, ffprobe_path)

    return subprocess.check_output(cmds, shell=True, close_fds=(not windows))


def _spawn(cmds, t="ffmpeg"):
    ''' Subprocess and Shell Commands in Python '''

    if t == "ffmpeg":
        cmds.insert(0, ffmpeg_path)
    else:
        cmds.insert(0, ffprobe_path)
    
    
    #qgsu.showUserAndLogMessage("", "spawned : " + " ".join(cmds), onlyLog=True)
    
    return subprocess.Popen(cmds, shell=windows, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            bufsize=0,
                            close_fds=(not windows))


def ResetData():
    ''' Reset Global Data '''
    global tLastLon, tLastLat
    
    
    SetcrtSensorSrc()
    SetcrtPltTailNum()
    # the elevation model is not cleared here: it belongs to the project,
    # not to one video, and initElevationModel rebinds it on every open
    tLastLon = 0.0
    tLastLat = 0.0
    RemoveAllDrawings()


def _heightmapFromProject():
    ''' Raster Kadas is set to use as heightmap, or None.

        Kadas stores the choice as a plain project entry, written when the
        user picks the raster in the layer tree. readEntry() answers
        (value, ok) in Python where the C++ takes an out parameter.
    '''
    try:
        layerId, found = QgsProject.instance().readEntry("Heightmap", "layer")
    except Exception:
        return None
    if not found or not layerId:
        return None
    layer = QgsProject.instance().mapLayer(layerId)
    if not isinstance(layer, QgsRasterLayer) or not layer.isValid():
        return None
    return layer


def _heightmapFromSettings(dtm_path):
    ''' The dtm_file of settings.ini, for a project with no heightmap. '''
    if not dtm_path or not os.path.exists(dtm_path):
        return None
    layer = QgsRasterLayer(dtm_path, "FMV elevation model")
    return layer if layer.isValid() else None


def initElevationModel(frameCenterLat, frameCenterLon, dtm_path):
    ''' Bind the elevation model used to drop the line of sight on ground.

        The heightmap configured in the Kadas project wins: it is already
        loaded, the user chose it, and it can be in any CRS. The path in
        settings.ini is the fallback for a project that has none.

        Nothing is read into memory. The old code loaded a square window
        around the first packet, which put the model in the wrong place
        whenever that packet was bad, and silently ran out of data as soon
        as the aircraft left it.
    '''
    global dtm_layer, dtm_provider, dtm_to_layer, dtm_from_layer
    global dtm_metres_per_unit, dtm_step_floor, dtm_graze, dtm_pixel_metres

    layer = _heightmapFromProject()
    origin = "the Kadas project heightmap"
    if layer is None:
        layer = _heightmapFromSettings(dtm_path)
        origin = "dtm_file in settings.ini"
    if layer is None:
        dtm_layer = None
        dtm_provider = None
        qgsu.showUserAndLogMessage("", "No elevation model: no project heightmap and no readable dtm_file.", onlyLog=True)
        return

    crs = layer.crs()
    project = QgsProject.instance()
    dtm_layer = layer
    dtm_provider = layer.dataProvider()
    dtm_to_layer = QgsCoordinateTransform(WGS84_CRS, crs, project)
    dtm_from_layer = QgsCoordinateTransform(crs, WGS84_CRS, project)

    if crs.isGeographic():
        lat = frameCenterLat if frameCenterLat is not None else 0.0
        dtm_metres_per_unit = (max(1.0, 111320.0 * abs(cos(radians(lat)))),
                               111320.0)
    else:
        dtm_metres_per_unit = (1.0, 1.0)

    pixel = max(abs(layer.rasterUnitsPerPixelX()),
                abs(layer.rasterUnitsPerPixelY()))
    pixelMetres = pixel * dtm_metres_per_unit[1]
    # The floor keeps the walk moving when the safe advance goes to zero,
    # but it is also the one place the safety bound leaks: a step longer
    # than clearance / closing can pass over a dip. Keeping it to a quarter
    # of a pixel bounds that leak, and a ray closer to the ground than
    # dtm_graze is treated as touching it, which is where a model of this
    # resolution stops being able to tell anyway.
    dtm_step_floor = max(0.5, 0.25 * pixelMetres)
    dtm_graze = max(0.5, 0.1 * pixelMetres)
    dtm_pixel_metres = max(1.0, pixelMetres)

    qgsu.showUserAndLogMessage("", "Elevation model: " + layer.name() +
                               " (" + crs.authid() + ") from " + origin +
                               ", pixel " + str(round(pixelMetres, 1)) +
                               " m.", onlyLog=True)


def UpdateLayers(packet, parent=None, mosaic=False, group=None):
    ''' Update Layers Values '''
    global frameCenterElevation, sensorLatitude, sensorLongitude, sensorTrueAltitude, groupName, geotransform

    groupName = group
    frameCenterLat = packet.FrameCenterLatitude
    frameCenterLon = packet.FrameCenterLongitude
    frameCenterElevation = packet.FrameCenterElevation
    sensorLatitude = packet.SensorLatitude
    sensorLongitude = packet.SensorLongitude
    sensorTrueAltitude = packet.SensorTrueAltitude
    sensorRelativeElevationAngle = packet.SensorRelativeElevationAngle
    slantRange = packet.SlantRange
    OffsetLat1 = packet.OffsetCornerLatitudePoint1
    LatitudePoint1Full = packet.CornerLatitudePoint1Full

    UpdatePlatformData(packet, hasElevationModel())
    UpdateTrajectoryData(packet, hasElevationModel())
     
    frameCenterPoint = [packet.FrameCenterLatitude, packet.FrameCenterLongitude, packet.FrameCenterElevation]
    
    #If no framcenter (f.i. horizontal target) don't comptute footprint, beams and frame center
    if (frameCenterPoint[0]==None and frameCenterPoint[1]==None):
        geotransform = None
        return True
    
    #No framecenter altitude
    if(frameCenterPoint[2]==None):
        if(sensorRelativeElevationAngle != None and slantRange != None):
            frameCenterPoint[2] = sensorTrueAltitude - sin(sensorRelativeElevationAngle) * slantRange
        else:
            frameCenterPoint[2] = 0.0
     
    #qgsu.showUserAndLogMessage("", "FC Alt:"+str(frameCenterPoint[2]), onlyLog=True)  
     
    if OffsetLat1 is not None and LatitudePoint1Full is None:
        if hasElevationModel() and frameCenterPoint[2] == 0.0:
            frameCenterPoint = GetLine3DIntersectionWithDEM(GetSensor(), frameCenterPoint)
        
        #qgsu.showUserAndLogMessage("", "CornerEstimationWithOffsets", onlyLog=True) 
        CornerEstimationWithOffsets(packet)
        if mosaic:
            georeferencingVideo(parent)

    elif OffsetLat1 is None and LatitudePoint1Full is None:
        if hasElevationModel() and frameCenterPoint[2] == 0.0:
            frameCenterPoint = GetLine3DIntersectionWithDEM(GetSensor(), frameCenterPoint)
        
        #qgsu.showUserAndLogMessage("", "CornerEstimationWithoutOffsets", onlyLog=True) 
        CornerEstimationWithoutOffsets(packet)
        if mosaic:
            georeferencingVideo(parent)

    else:

        cornerPointUL = [packet.CornerLatitudePoint1Full,
                         packet.CornerLongitudePoint1Full]
        if None in cornerPointUL:
            return False

        cornerPointUR = [packet.CornerLatitudePoint2Full,
                         packet.CornerLongitudePoint2Full]
        if None in cornerPointUR:
            return False

        cornerPointLR = [packet.CornerLatitudePoint3Full,
                         packet.CornerLongitudePoint3Full]

        if None in cornerPointLR:
            return False

        cornerPointLL = [packet.CornerLatitudePoint4Full,
                         packet.CornerLongitudePoint4Full]

        if None in cornerPointLL:
            return False
        
        UpdateFootPrintData(
            packet, cornerPointUL, cornerPointUR, cornerPointLR, cornerPointLL, hasElevationModel())

        UpdateBeamsData(packet, cornerPointUL, cornerPointUR,
                        cornerPointLR, cornerPointLL, hasElevationModel())

        SetGCPsToGeoTransform(cornerPointUL, cornerPointUR,
                              cornerPointLR, cornerPointLL, frameCenterPoint[1], frameCenterPoint[0], hasElevationModel())

        if mosaic:
            georeferencingVideo(parent)
    
    UpdateFrameCenterData(frameCenterPoint, hasElevationModel())
    
    UpdateFrameAxisData(packet.ImageSourceSensor, GetSensor(), frameCenterPoint, hasElevationModel())
    
    items = GetMapItems()
    
    if items["footprint"] and items["platform"] and items["framecenter"]:        
        
        transP = items["platform"].asGeometry().asPoint()
        transT = items["framecenter"].asGeometry().asPoint()
        
        rect = items["footprint"].asGeometry().boundingBox()
        rectLL = QgsPointXY(rect.xMinimum(),rect.yMinimum())
        rectUR = QgsPointXY(rect.xMaximum(),rect.yMaximum())
        
        f_lyr_out_extent = QgsRectangle(rectLL, rectUR)
        t_lyr_out_extent = QgsRectangle(transT.x(), transT.y(), transT.x(), transT.y())
        p_lyr_out_extent = QgsRectangle(transP.x(), transP.y(), transP.x(), transP.y())
        
        bValue = parent.iface.mapCanvas().extent().xMaximum() - parent.iface.mapCanvas().center().x()
        
        #create a detection buffer 
        map_detec_buffer = parent.iface.mapCanvas().extent().buffered(bValue * -0.7)
        
        #qgsu.showUserAndLogMessage("", "map Max X:"+str(parent.iface.mapCanvas().extent().xMaximum()), onlyLog=True)
        #qgsu.showUserAndLogMessage("", "map_detec_buffer Max X:"+str(map_detec_buffer.xMaximum()), onlyLog=True)
        
        # recenter map on platform
        if not map_detec_buffer.contains(p_lyr_out_extent) and centerMode == 1:
            # recenter map on platform
            parent.iface.mapCanvas().setExtent(p_lyr_out_extent)
            
        # recenter map on footprint
        elif not map_detec_buffer.contains(f_lyr_out_extent) and centerMode == 2:
            #zoom a bit wider than the footprint itself
            parent.iface.mapCanvas().setExtent( f_lyr_out_extent.buffered(f_lyr_out_extent.width()*0.5))
        # recenter map on target
        elif not map_detec_buffer.contains(t_lyr_out_extent) and centerMode == 3:
            parent.iface.mapCanvas().setExtent(t_lyr_out_extent)
        parent.iface.mapCanvas().refresh()
                
    return True


def georeferencingVideo(parent):
    """ Extract Current Frame Thread
    :param packet: Parent class
    """
    image = parent.videoWidget.currentFrame()

    folder = getVideoFolder(parent.fileName)
    qgsu.createFolderByName(folder, "mosaic")
    out = os.path.join(folder, "mosaic")

    position = str(parent.player.position())

    taskGeoreferencingVideo = QgsTask.fromFunction('Georeferencing Current Frame Task',
                                                   GeoreferenceFrame,
                                                   image=image, output=out, p=position,
                                                   on_finished=parent.finishedTask,
                                                   flags=QgsTask.CanCancel)

    QgsApplication.taskManager().addTask(taskGeoreferencingVideo)
    return


def GeoreferenceFrame(task, image, output, p):
    ''' Save Current Image '''
    global groupName
    ext = ".tiff"
    t = "out_" + p + ext
    name = "g_" + p

    src_file = os.path.join(output, t)

    image.save(src_file)

    # Opens source dataset
    src_ds = gdal.OpenEx(src_file, gdal.OF_RASTER |
                         gdal.OF_READONLY, open_options=['NUM_THREADS=ALL_CPUS'])

    # Open destination dataset
    dst_filename = os.path.join(output, name + ext)
    dst_ds = gdal.GetDriverByName("GTiff").CreateCopy(dst_filename, src_ds, 0,
                                                      options=['TILED=NO', 'BIGTIFF=NO', 'COMPRESS_OVERVIEW=DEFLATE', 'COMPRESS=LZW', 'NUM_THREADS=ALL_CPUS', 'predictor=2'])
    src_ds = None
    # Get raster projection
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)

    # Set projection
    dst_ds.SetProjection(srs.ExportToWkt())

    # Set location
    dst_ds.SetGeoTransform(geotransform_affine)
    dst_ds.GetRasterBand(1).SetNoDataValue(0)
    dst_ds.FlushCache()
    # Close files
    dst_ds = None

    # Add Layer to canvas
    layer = QgsRasterLayer(dst_filename, name)
    addLayerNoCrsDialog(layer, False, frames_g, isSubGroup=True)
    ExpandLayer(layer, False)
    if task.isCanceled():
        return None
    return {'task': task.description()}


def GetGeotransform_affine():
    ''' Get current frame affine transformation '''
    return geotransform_affine


def CornerEstimationWithOffsets(packet):
    ''' Corner estimation using Offsets
    :param packet: Metada packet
    '''
    global geotransform
    
    try:

        OffsetLat1 = packet.OffsetCornerLatitudePoint1
        OffsetLon1 = packet.OffsetCornerLongitudePoint1
        OffsetLat2 = packet.OffsetCornerLatitudePoint2
        OffsetLon2 = packet.OffsetCornerLongitudePoint2
        OffsetLat3 = packet.OffsetCornerLatitudePoint3
        OffsetLon3 = packet.OffsetCornerLongitudePoint3
        OffsetLat4 = packet.OffsetCornerLatitudePoint4
        OffsetLon4 = packet.OffsetCornerLongitudePoint4
        frameCenterLat = packet.FrameCenterLatitude
        frameCenterLon = packet.FrameCenterLongitude

        # Lat,Lon
        cornerPointUL = (OffsetLat1 + frameCenterLat,
                         OffsetLon1 + frameCenterLon)
        cornerPointUR = (OffsetLat2 + frameCenterLat,
                         OffsetLon2 + frameCenterLon)
        cornerPointLR = (OffsetLat3 + frameCenterLat,
                         OffsetLon3 + frameCenterLon)
        cornerPointLL = (OffsetLat4 + frameCenterLat,
                         OffsetLon4 + frameCenterLon)
         
        frameCenterPoint = [packet.FrameCenterLatitude, packet.FrameCenterLongitude, packet.FrameCenterElevation]
        
        #If no framcenter (f.i. horizontal target) don't comptute footprint, beams and frame center
        if (frameCenterPoint[0]==None and frameCenterPoint[1]==None):
            geotransform = None
            return True

        #should not be required here, as we have the lon/lat offsets
        #if hasElevationModel():
        #    cornerPointUL = GetLine3DIntersectionWithDEM(
        #        GetSensor(), cornerPointUL)
        #    cornerPointUR = GetLine3DIntersectionWithDEM(
        #        GetSensor(), cornerPointUR)
        #    cornerPointLR = GetLine3DIntersectionWithDEM(
        #        GetSensor(), cornerPointLR)
        #    cornerPointLL = GetLine3DIntersectionWithDEM(
        #        GetSensor(), cornerPointLL)
        #    frameCenterPoint = GetLine3DIntersectionWithDEM(
        #        GetSensor(), frameCenterPoint)

        UpdateFootPrintData(packet,
                            cornerPointUL, cornerPointUR, cornerPointLR, cornerPointLL, hasElevationModel())

        UpdateBeamsData(packet, cornerPointUL, cornerPointUR,
                        cornerPointLR, cornerPointLL, hasElevationModel())

        SetGCPsToGeoTransform(cornerPointUL, cornerPointUR,
                              cornerPointLR, cornerPointLL, frameCenterPoint[1], frameCenterPoint[0], hasElevationModel())

    except Exception:
        return False

    return True


def CornerEstimationWithoutOffsets(packet=None, sensor=None, frameCenter=None, FOV=None, others=None):
    ''' Corner estimation without Offsets '''
    global geotransform, geotransform_affine
        
    try:
        if packet is not None:
            sensorLatitude = packet.SensorLatitude
            sensorLongitude = packet.SensorLongitude
            sensorTrueAltitude = packet.SensorTrueAltitude
            frameCenterLat = packet.FrameCenterLatitude
            frameCenterLon = packet.FrameCenterLongitude
            frameCenterElevation = packet.FrameCenterElevation
            sensorVerticalFOV = packet.SensorVerticalFieldOfView
            sensorHorizontalFOV = packet.SensorHorizontalFieldOfView
            headingAngle = packet.PlatformHeadingAngle
            sensorRelativeAzimut = packet.SensorRelativeAzimuthAngle
            targetWidth = packet.targetWidth
            slantRange = packet.SlantRange
        else:
            sensorLatitude = sensor[1]
            sensorLongitude = sensor[0]
            sensorTrueAltitude = sensor[2]
            frameCenterLat = frameCenter[1]
            frameCenterLon = frameCenter[0]
            frameCenterElevation = frameCenter[2]
            sensorVerticalFOV = FOV[0]
            sensorHorizontalFOV = FOV[1]
            headingAngle = others[0]
            sensorRelativeAzimut = others[1]
            targetWidth = others[2]
            slantRange = others[3]

        # If target width = 0 (occurs on some platforms), compute it with the slate range.
        # Otherwise it leaves the footprint as a point.
        # In some case targetWidth don't have value then equal to 0
        if targetWidth is None:
            targetWidth = 0
        if slantRange is None:
            slantRange = 0
        if targetWidth == 0 and slantRange != 0:
            targetWidth = 2.0 * slantRange * \
                tan(radians(sensorHorizontalFOV / 2.0))
        elif targetWidth == 0 and slantRange == 0:
            # default target width to not leave footprint as a point.
            targetWidth = defaultTargetWidth
#             qgsu.showUserAndLogMessage(QCoreApplication.translate(
#                 "QgsFmvUtils", "Target width unknown, defaults to: " + str(targetWidth) + "m."))

        # compute distance to ground
        if frameCenterElevation != 0 and sensorTrueAltitude is not None and frameCenterElevation is not None:
            sensorGroundAltitude = sensorTrueAltitude - frameCenterElevation
        elif frameCenterElevation != 0 and sensorTrueAltitude is not None:
            sensorGroundAltitude = sensorTrueAltitude
        else:
            #can't compute footprint without sensorGroundAltitude
            return False                              

        if sensorLatitude == 0:
            return False

        if sensorLongitude is None or sensorLatitude is None:
            return False

        initialPoint = (sensorLongitude, sensorLatitude)

        if frameCenterLon is None or frameCenterLat is None:
            return False

        destPoint = (frameCenterLon, frameCenterLat)

        distance = sphere.distance(initialPoint, destPoint)
        if distance == 0:
            return False

        if sensorVerticalFOV > 0 and sensorHorizontalFOV > sensorVerticalFOV:
            aspectRatio = sensorVerticalFOV / sensorHorizontalFOV

        else:
            aspectRatio = 0.75

        value2 = (headingAngle + sensorRelativeAzimut) % 360.0  # Heading
        value3 = targetWidth / 2.0

        value5 = sqrt(pow(distance, 2.0) + pow(sensorGroundAltitude, 2.0))
        value6 = targetWidth * aspectRatio / 2.0

        degrees_value = degrees(atan(value3 / distance))

        value8 = degrees(atan(distance / sensorGroundAltitude))
        value9 = degrees(atan(value6 / value5))
        value10 = value8 + value9
        value11 = sensorGroundAltitude * tan(radians(value10))
        value12 = value8 - value9
        value13 = sensorGroundAltitude * tan(radians(value12))
        value14 = distance - value13
        value15 = value11 - distance
        value16 = value3 - value14 * tan(radians(degrees_value))
        value17 = value3 + value15 * tan(radians(degrees_value))
        distance2 = sqrt(pow(value14, 2.0) + pow(value16, 2.0))
        value19 = sqrt(pow(value15, 2.0) + pow(value17, 2.0))
        value20 = degrees(atan(value16 / value14))
        value21 = degrees(atan(value17 / value15))

        # CP Up Left
        bearing = (value2 + 360.0 - value21) % 360.0
        cornerPointUL = list(
            reversed(sphere.destination(destPoint, value19, bearing)))

        # CP Up Right
        bearing = (value2 + value21) % 360.0
        cornerPointUR = list(
            reversed(sphere.destination(destPoint, value19, bearing)))

        # CP Low Right
        bearing = (value2 + 180.0 - value20) % 360.0
        cornerPointLR = list(
            reversed(sphere.destination(destPoint, distance2, bearing)))

        # CP Low Left
        bearing = (value2 + 180.0 + value20) % 360.0
        cornerPointLL = list(
            reversed(sphere.destination(destPoint, distance2, bearing)))
        
        frameCenterPoint = [packet.FrameCenterLatitude, packet.FrameCenterLongitude, packet.FrameCenterElevation]
        
        #If no framcenter (f.i. horizontal target) don't comptute footprint, beams and frame center
        if (frameCenterPoint[0]==None and frameCenterPoint[1]==None):
            geotransform = None
            return True
        
        #qgsu.showUserAndLogMessage("", "value8: {}".format(value8), onlyLog=True)
        
        if hasElevationModel() and value8 < max_vert_angle:
            cornerPointUL = GetLine3DIntersectionWithDEM(
                GetSensor(), cornerPointUL)
            cornerPointUR = GetLine3DIntersectionWithDEM(
                GetSensor(), cornerPointUR)
            cornerPointLR = GetLine3DIntersectionWithDEM(
                GetSensor(), cornerPointLR)
            cornerPointLL = GetLine3DIntersectionWithDEM(
                GetSensor(), cornerPointLL)
            if frameCenterPoint[2] is not None:
                if frameCenterPoint[2] == 0:
                    frameCenterPoint = GetLine3DIntersectionWithDEM(GetSensor(), frameCenterPoint)

        if sensor is not None:
            return cornerPointUL, cornerPointUR, cornerPointLR, cornerPointLL
        
        if value8 < max_vert_angle:
            UpdateFootPrintData(packet,
                            cornerPointUL, cornerPointUR, cornerPointLR, cornerPointLL, hasElevationModel())

            UpdateBeamsData(packet, cornerPointUL, cornerPointUR,
                        cornerPointLR, cornerPointLL, hasElevationModel())

            SetGCPsToGeoTransform(cornerPointUL, cornerPointUR,
                                  cornerPointLR, cornerPointLL,
                                  frameCenterPoint[1], frameCenterPoint[0], hasElevationModel())
        else:
            # too close to the horizon: the corner estimation diverges, so the
            # homography built from it is meaningless. Dropping it is what
            # switches off the cursor coordinates and the drawing tools.
            HideFootPrintData()
            HideBeamsData()
            geotransform = None
            geotransform_affine = None

    except Exception as e:
        qgsu.showUserAndLogMessage(QCoreApplication.translate(
            "QgsFmvUtils", "CornerEstimationWithoutOffsets failed! : "), str(e))
        return False

    return True

def _sampleLayerXY(x, y):
    ''' Ground height at a point already in the model own CRS. '''
    if dtm_provider is None:
        return None
    try:
        value, ok = dtm_provider.sample(QgsPointXY(x, y), 1)
    except Exception:
        return None
    # sample answers not ok outside the model, and nan is never a height
    if not ok or value != value:
        return None
    return value


def GetDemAltAt(lon, lat):
    ''' Ground height below a WGS84 position, 0 when it is not known. '''
    if dtm_provider is None:
        return 0
    try:
        point = dtm_to_layer.transform(QgsPointXY(lon, lat))
    except Exception:
        return 0
    alt = _sampleLayerXY(point.x(), point.y())
    return 0 if alt is None else alt

def _bisectGround(sx, sy, ux, uy, ua, sensorAlt, lo, hi, rounds=12):
    ''' Narrow the interval where the ray crossed the ground.

        lo is known to be clear of the ground and hi touching it. Twelve
        take an interval of a few hundred metres well under one pixel.
    '''
    mx, my = dtm_metres_per_unit
    for _ in range(rounds):
        mid = 0.5 * (lo + hi)
        ground = _sampleLayerXY(sx + mid * ux / mx, sy + mid * uy / my)
        if ground is None:
            break
        if sensorAlt + mid * ua - ground <= dtm_graze:
            hi = mid
        else:
            lo = mid
    return hi


def GetLine3DIntersectionWithDEM(sensorPt, targetPt):
    ''' First point where the line of sight meets the ground.

        Walked from the sensor towards the target and never the other way:
        a line can cross two ridges and only the first one is visible, the
        second one is the back of the mountain.

        The step is not fixed. With c the clearance above the ground at the
        current sample, the ray descending at |ua| per metre travelled and
        the ground rising at most dtm_max_slope per horizontal metre, the
        gap cannot close before c / (|ua| + slope * ph). Advancing by that
        much takes long strides while the ray is still high up and cannot
        step over a ridge however thin, which a fixed coarse step does. The
        interval that changed sign is then bisected.
    '''
    sensorLat = sensorPt[0]
    sensorLon = sensorPt[1]
    sensorAlt = sensorPt[2]
    targetLat = targetPt[0]
    targetLon = targetPt[1]
    try:
        targetAlt = targetPt[2]
    except Exception:
        targetAlt = GetFrameCenter()[2]
    if targetAlt is None:
        targetAlt = 0.0

    # what the caller gets when the ground cannot be found: the target as
    # the metadata reported it
    fallback = [targetLat, targetLon, targetAlt]
    if dtm_provider is None or sensorAlt is None:
        return fallback
    if None in (sensorLat, sensorLon, targetLat, targetLon):
        return fallback

    try:
        start = dtm_to_layer.transform(QgsPointXY(sensorLon, sensorLat))
        aim = dtm_to_layer.transform(QgsPointXY(targetLon, targetLat))
    except Exception:
        return fallback

    mx, my = dtm_metres_per_unit
    dx = (aim.x() - start.x()) * mx
    dy = (aim.y() - start.y()) * my
    da = targetAlt - sensorAlt
    slant = sqrt(dx * dx + dy * dy + da * da)
    if slant <= 0.0:
        return fallback

    ux, uy, ua = dx / slant, dy / slant, da / slant
    ph = sqrt(ux * ux + uy * uy)
    closing = abs(ua) + dtm_max_slope * ph
    if closing <= 0.0:
        return fallback

    sx, sy = start.x(), start.y()
    k = 0.0
    previous = 0.0
    hole = 0.0
    # a hit only counts once the ray has actually been above the ground
    airborne = False
    iterations = 0
    while k <= dtm_max_range:
        iterations += 1
        if iterations > DTM_MAX_ITERATIONS:
            return fallback
        ground = _sampleLayerXY(sx + k * ux / mx, sy + k * uy / my)
        if ground is None:
            # no data here: cross it a pixel at a time, nothing can be
            # hidden in a cell the model does not describe
            hole += dtm_pixel_metres
            if hole > DTM_MAX_HOLE_METRES:
                return fallback
            previous = k
            k += dtm_pixel_metres
            continue
        hole = 0.0
        clearance = sensorAlt + k * ua - ground
        if clearance <= dtm_graze:
            if not airborne:
                # the ray has not been above the ground yet, so this is a
                # sensor position under the terrain, bad metadata rather
                # than an intersection. Walk on until it clears the ground
                previous = k
                k += dtm_step_floor
                continue
            hit = _bisectGround(sx, sy, ux, uy, ua, sensorAlt, previous, k)
            try:
                back = dtm_from_layer.transform(
                    QgsPointXY(sx + hit * ux / mx, sy + hit * uy / my))
            except Exception:
                return fallback
            return [back.y(), back.x(), sensorAlt + hit * ua]
        airborne = True
        step = clearance / closing
        if step < dtm_step_floor:
            step = dtm_step_floor
        previous = k
        k += step

    return fallback


def GetLine3DIntersectionWithPlane(sensorPt, demPt, planeHeight):
    ''' Get Altitude from DEM '''
    sensorLat = sensorPt[0]
    sensorLon = sensorPt[1]
    sensorAlt = sensorPt[2]
    demPtLat = demPt[1]
    demPtLon = demPt[0]
    demPtAlt = demPt[2]

    distance = sphere.distance([sensorLat, sensorLon], [demPtLat, demPtLon])
    distance = sqrt(distance ** 2 + (demPtAlt - demPtAlt) ** 2)
    dLat = (demPtLat - sensorLat) / distance
    dLon = (demPtLon - sensorLon) / distance
    dAlt = (demPtAlt - sensorAlt) / distance

    k = ((demPtAlt - planeHeight) / (sensorAlt - demPtAlt)) * distance
    pt = [sensorLon + (distance + k) * dLon, sensorLat +
          (distance + k) * dLat, sensorAlt + (distance + k) * dAlt]

    return pt


def _convert_timestamp(ts):
    '''Translates the values from a regex match for two timestamps of the
    form 00:12:34,567 into seconds.'''
    start = int(ts.group(1)) * 3600 + int(ts.group(2)) * 60
    start += int(ts.group(3))
    start += float(ts.group(4)) / 10 ** len(ts.group(4))
    end = int(ts.group(5)) * 3600 + int(ts.group(6)) * 60
    end += int(ts.group(7))
    end += float(ts.group(8)) / 10 ** len(ts.group(8))
    return start, end


def _add_secs_to_time(timeval, secs_to_add):
    ''' Seconds to time '''
    secs = timeval.hour * 3600 + timeval.minute * 60 + timeval.second
    secs += secs_to_add
    return _seconds_to_time(secs)


def _time_to_seconds(dateStr):
    '''
    Time to seconds
    @type dateStr: String
    @param dateStr: Date string value
    '''
    timeval = datetime.strptime(dateStr, '%H:%M:%S.%f')
    secs = timeval.hour * 3600 + timeval.minute * 60 + \
        timeval.second + timeval.microsecond / 1000000

    return secs


def _seconds_to_time(sec):
    '''Returns a string representation of the length of time provided.
    For example, 3675.14 -> '01:01:15'
    @type sec: String
    @param sec: seconds string value
    '''
    hours = int(sec / 3600)
    sec -= hours * 3600
    minutes = int(sec / 60)
    sec -= minutes * 60
    return '%02d:%02d:%02d' % (hours, minutes, sec)


def _seconds_to_time_frac(sec, comma=False):
    '''Returns a string representation of the length of time provided,
    including partial seconds.
    For example, 3675.14 -> '01:01:15.140000'
    @type sec: String
    @param sec: seconds string value
    '''
    hours = int(sec / 3600)
    sec -= hours * 3600
    minutes = int(sec / 60)
    sec -= minutes * 60
    if comma:
        frac = int(round(sec % 1.0 * 1000))
        return '%02d:%02d:%02d,%03d' % (hours, minutes, sec, frac)
    else:
        return '%02d:%02d:%07.4f' % (hours, minutes, sec)


def BurnDrawingsImage(source, overlay):
    '''Burn drawings into image
    @type source: QImage
    @param source: Original Image

    @type overlay: QImage
    @param overlay: Drawings image
    @return: QImage
    '''
    base = source.scaled(overlay.size(), Qt.AspectRatioMode.IgnoreAspectRatio)

    p = QPainter()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.begin(base)
    #with CompositionMode_SourceOut we have a black image at the end.
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    p.drawImage(0, 0, overlay)
    p.end()

    # Restore size
    base = base.scaled(source.size(), Qt.AspectRatioMode.IgnoreAspectRatio)
    return base
