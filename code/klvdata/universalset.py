# -*- coding: utf-8 -*-

# Support for flat (unwrapped) universal metadata streams.
#
# MISB ST 0601 wraps every packet in a Local Set: one 16 byte universal key,
# one BER length, then 1 byte tags. Systems predating ST 0601 and built
# directly on the SMPTE RP 210 dictionary (the MISB EG 0104 family) emit no
# wrapper at all: the stream is a flat run of "16 byte universal key + BER
# length + value" elements, one run per video frame.
#
# StreamParser looks the leading key up in its registry of set keys, finds
# nothing and yields UnknownElement, so such a video is reported as having no
# readable metadata. This module recognises the flat layout and slices it back
# into frames, so each frame can be handed to UAVBasicUniversalMetadataSet,
# which already knows these element keys and maps them onto ST 0601 tags.

from QGIS_FMV.klvdata.common import bytes_to_int

UNIVERSAL_LABEL_PREFIX = b'\x06\x0e\x2b\x34'
KEY_LENGTH = 16

# byte 5 of a SMPTE universal label: 01 = metadata dictionary element,
# 02 = group (set or pack). Only category 01 appears at the top level of a
# flat stream; category 02 there means a regular wrapping set.
CATEGORY_METADATA_ELEMENT = 0x01

# an element larger than this is a decoding accident, not real metadata
MAX_ELEMENT_LENGTH = 1 << 20

# how many consecutive well formed elements are needed before a buffer is
# accepted as a flat stream
MIN_ELEMENTS_TO_ACCEPT = 4


def _readLength(buf, pos):
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
    return bytes_to_int(buf[pos:pos + count]), pos + count


def iterElements(buf, pos=0):
    ''' Yield (key, start, end) for every well formed element from pos on.

        Stops silently on the first malformed or truncated element, which is
        what a stream cut mid element by ffmpeg looks like.
    '''
    while pos + KEY_LENGTH <= len(buf):
        key = bytes(buf[pos:pos + KEY_LENGTH])
        if not key.startswith(UNIVERSAL_LABEL_PREFIX):
            return
        length, valueStart = _readLength(buf, pos + KEY_LENGTH)
        if length is None or length > MAX_ELEMENT_LENGTH:
            return
        end = valueStart + length
        if end > len(buf):
            return
        yield key, pos, end
        pos = end


def firstLabelOffset(buf, start=0):
    ''' Offset of the next universal label, or -1. '''
    return buf.find(UNIVERSAL_LABEL_PREFIX, start)


def isFlatUniversalStream(buf, knownSetKeys=()):
    ''' True when buf is a run of standalone universal elements.

        A wrapped stream is rejected on its set key, either because that key
        is registered on StreamParser or because it is a group (category 02).
        Anything else has to walk as a plausible run of elements before it is
        accepted, so random binary is not mistaken for metadata.
    '''
    if not buf:
        return False
    offset = firstLabelOffset(buf)
    if offset < 0:
        return False
    key = bytes(buf[offset:offset + KEY_LENGTH])
    if len(key) < KEY_LENGTH:
        return False
    if key in knownSetKeys:
        return False
    if key[4] != CATEGORY_METADATA_ELEMENT:
        return False
    count = 0
    for _ in iterElements(buf, offset):
        count += 1
        if count >= MIN_ELEMENTS_TO_ACCEPT:
            return True
    return False


def _frameDelimiter(buf, offset):
    ''' Key that marks the start of a frame, and whether it is trustworthy.

        When the buffer starts on a label the first key is the frame start, as
        ffmpeg hands out whole packets. Otherwise the stream was cut mid frame
        and the delimiter is taken to be the first key that repeats, which is
        still one frame apart but no longer aligned on the real frame start.
    '''
    if offset == 0:
        return bytes(buf[:KEY_LENGTH]), True
    seen = set()
    for key, _start, _end in iterElements(buf, offset):
        if key in seen:
            return key, False
        seen.add(key)
    return None, False


def splitFrames(buf):
    ''' Slice a flat stream into one bytes object per frame. '''
    offset = firstLabelOffset(buf)
    if offset < 0:
        return []
    delimiter, aligned = _frameDelimiter(buf, offset)
    if delimiter is None:
        return []

    frames = []
    start = None
    end = offset
    for key, elemStart, elemEnd in iterElements(buf, offset):
        if key == delimiter:
            if start is not None:
                frames.append(bytes(buf[start:elemStart]))
            start = elemStart
        end = elemEnd
    if start is not None and end > start:
        frames.append(bytes(buf[start:end]))

    # the leading slice of an unaligned stream straddles two frames
    if not aligned and frames:
        frames = frames[1:]
    return frames
