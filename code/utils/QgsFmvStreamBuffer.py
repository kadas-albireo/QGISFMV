# -*- coding: utf-8 -*-

# Framing and buffering for live KLV streams.
#
# In file mode ffmpeg is asked for a bounded time range and hands back a whole
# slice of metadata at once. In stream mode the data track arrives as an
# endless byte pipe with no packet boundaries of its own, so the reader has to
# find them itself.
#
# The previous reader took the pipe 16 bytes at a time and compared each block
# against two hard coded universal labels. That only works when every packet
# length happens to be a multiple of 16, which is not the case: a ST 0601
# packet is 16 (UL) + BER length + payload, 284 bytes on a Super Puma feed, so
# after the very first packet the reader is 12 bytes out of phase and never
# matches again. KlvFramer scans for a label at any offset and then uses the
# BER length to find the exact end of the packet, so it also resynchronises
# after a datagram loss.

import threading
from collections import deque
from time import monotonic

from QGIS_FMV.klvdata.streamparser import StreamParser
from QGIS_FMV.klvdata.universalset import (CATEGORY_METADATA_ELEMENT,
                                           KEY_LENGTH,
                                           UNIVERSAL_LABEL_PREFIX,
                                           iterElements)

# an announced length beyond this is a decoding accident, not real metadata
MAX_PACKET_LENGTH = 1 << 20

# ST 0601 tag and EG 0104 universal key of the precision time stamp
ST0601_TIMESTAMP_TAG = 2
EG0104_TIMESTAMP_KEY = bytes.fromhex('060e2b34010101030702010101050000')


def readBerLength(buf, pos):
    ''' Read a BER length at pos.

        Returns (length, position of the first value byte), or (None, None)
        when the length is malformed or runs past the end of the buffer.
    '''
    if pos >= len(buf):
        return None, None
    first = buf[pos]
    pos += 1
    if first < 128:
        return first, pos
    count = first - 128
    if count == 0 or count > 4 or pos + count > len(buf):
        return None, None
    return int.from_bytes(bytes(buf[pos:pos + count]), 'big'), pos + count


class KlvFramer(object):
    ''' Turn an endless byte pipe into whole KLV packets.

        Handles both layouts the plugin understands: a wrapping local set,
        whose length is known from its BER header, and a flat run of universal
        elements, which is cut where its first key comes round again.
    '''

    def __init__(self, setKeys=None):
        self._buf = bytearray()
        self._setKeys = setKeys if setKeys is not None else StreamParser.parsers
        self.resyncCount = 0
        self.droppedBytes = 0

    def pending(self):
        return len(self._buf)

    def feed(self, chunk):
        ''' Add bytes and return every complete packet they closed. '''
        if chunk:
            self._buf.extend(chunk)
        packets = []
        while True:
            packet = self._takeOne()
            if packet is None:
                break
            packets.append(packet)
        return packets

    def _consume(self, count):
        ''' Remove bytes that were handed out as a packet. '''
        if count > 0:
            del self._buf[:count]

    def _discard(self, count):
        ''' Remove bytes that were thrown away, and count them. '''
        if count <= 0:
            return
        del self._buf[:count]
        self.droppedBytes += count

    def _takeOne(self):
        buf = self._buf
        offset = buf.find(UNIVERSAL_LABEL_PREFIX)
        if offset < 0:
            # keep only what could still be the head of a label
            if len(buf) > len(UNIVERSAL_LABEL_PREFIX) - 1:
                self._discard(len(buf) - (len(UNIVERSAL_LABEL_PREFIX) - 1))
            return None
        if offset > 0:
            self._discard(offset)
            self.resyncCount += 1
            buf = self._buf
        if len(buf) < KEY_LENGTH + 1:
            return None

        key = bytes(buf[:KEY_LENGTH])
        if key in self._setKeys:
            length, valueStart = readBerLength(buf, KEY_LENGTH)
            if length is None:
                # the length itself is still incomplete
                return None
            if length > MAX_PACKET_LENGTH:
                self._skipLabel()
                return None
            end = valueStart + length
            if end > len(buf):
                return None
            packet = bytes(buf[:end])
            self._consume(end)
            return packet

        if key[4] == CATEGORY_METADATA_ELEMENT:
            # flat stream: cut where the leading key comes round again
            for elementKey, elementStart, _elementEnd in iterElements(buf):
                if elementStart > 0 and elementKey == key:
                    packet = bytes(buf[:elementStart])
                    self._consume(elementStart)
                    return packet
            if len(buf) > MAX_PACKET_LENGTH:
                self._skipLabel()
            return None

        self._skipLabel()
        return None

    def _skipLabel(self):
        ''' Step past a label that leads nowhere and look for the next one. '''
        self._discard(len(UNIVERSAL_LABEL_PREFIX))
        self.resyncCount += 1


def klvTimestampMicroseconds(packet):
    ''' Precision time stamp of a packet in microseconds, or None.

        Read straight from the bytes rather than through the full parser: this
        runs on the reader thread, once per packet.
    '''
    if packet is None or len(packet) < KEY_LENGTH + 1:
        return None
    key = bytes(packet[:KEY_LENGTH])

    if key in StreamParser.parsers:
        length, pos = readBerLength(packet, KEY_LENGTH)
        if length is None:
            return None
        end = min(pos + length, len(packet))
        while pos < end:
            tag = packet[pos]
            itemLength, pos = readBerLength(packet, pos + 1)
            if itemLength is None or pos + itemLength > end:
                return None
            if tag == ST0601_TIMESTAMP_TAG and itemLength == 8:
                return int.from_bytes(bytes(packet[pos:pos + 8]), 'big')
            pos += itemLength
        return None

    for elementKey, _start, elementEnd in iterElements(packet):
        if elementKey == EG0104_TIMESTAMP_KEY and elementEnd >= 8:
            return int.from_bytes(bytes(packet[elementEnd - 8:elementEnd]), 'big')
    return None


class TimedPacketBuffer(object):
    ''' Bounded ring of packets, keyed on the moment they were read.

        The video reaches the screen later than its metadata reaches this
        buffer: one extra ffmpeg hop, the local RTP leg and whatever
        QMediaPlayer holds. get() therefore asks for the packet that arrived
        one display latency ago rather than the newest one.
    '''

    def __init__(self, maxlen=256):
        self._items = deque(maxlen=max(1, int(maxlen)))
        self._lock = threading.Lock()

    def put(self, packet, arrival=None):
        with self._lock:
            self._items.append((monotonic() if arrival is None else arrival, packet))

    def size(self):
        with self._lock:
            return len(self._items)

    def span(self):
        ''' Seconds between the oldest and newest packet held. '''
        with self._lock:
            if len(self._items) < 2:
                return 0.0
            return self._items[-1][0] - self._items[0][0]

    def clear(self):
        with self._lock:
            self._items.clear()

    def newest(self):
        with self._lock:
            return self._items[-1][1] if self._items else None

    def get(self, latency=0.0, now=None):
        ''' Packet matching what is on screen, latency seconds in the past. '''
        with self._lock:
            if not self._items:
                return None
            target = (monotonic() if now is None else now) - max(0.0, float(latency))
            best = None
            bestDelta = None
            for arrival, packet in self._items:
                delta = abs(arrival - target)
                if bestDelta is None or delta < bestDelta:
                    best, bestDelta = packet, delta
            return best
