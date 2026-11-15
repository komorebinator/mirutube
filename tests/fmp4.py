"""Reads fragmented MP4 the way a Media Source Extensions SourceBuffer does:
the boxes at the top level, and for every track the decode time, composition
offset and duration of each sample from the moof boxes — the timestamps the
browser places the media at."""

import struct

CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"mvex", b"moof", b"traf", b"edts"}


def boxes(data, start=0, end=None):
    """(type, body start, box end) of the boxes between start and end."""
    end = len(data) if end is None else end
    i = start
    while i + 8 <= end:
        size, kind = struct.unpack(">I4s", data[i:i + 8])
        head = 8
        if size == 1:
            size = struct.unpack(">Q", data[i + 8:i + 16])[0]
            head = 16
        elif size == 0:
            size = end - i
        if size < head or i + size > end:
            raise ValueError("box %r at %d runs past its parent" % (kind, i))
        yield kind, i + head, i + size
        i += size


def child(data, start, end, kind):
    return next(((s, e) for k, s, e in boxes(data, start, end) if k == kind), None)


def top_level(data):
    return [k.decode("latin-1") for k, _, _ in boxes(data)]


def init_segment(data):
    """The ftyp and moov boxes: what a SourceBuffer takes as an
    initialization segment."""
    return b"".join(data[s - 8:e] for k, s, e in boxes(data) if k in (b"ftyp", b"moov"))


def media_segments(data):
    """The moof and mdat boxes, in order."""
    return b"".join(data[s - 8:e] for k, s, e in boxes(data) if k in (b"moof", b"mdat"))


def sample_descriptions(data):
    """The stsd box of every track, by track id: the codec configuration."""
    moov = child(data, 0, len(data), b"moov")
    found = {}
    for k, s, e in boxes(data, *moov):
        if k != b"trak":
            continue
        tkhd = child(data, s, e, b"tkhd")
        version = data[tkhd[0]]
        track = struct.unpack(">I", data[tkhd[0] + (20 if version == 1 else 12):][:4])[0]
        mdia = child(data, s, e, b"mdia")
        minf = child(data, *mdia, b"minf")
        stbl = child(data, *minf, b"stbl")
        stsd = child(data, *stbl, b"stsd")
        found[track] = data[stsd[0]:stsd[1]]
    return found


def tracks(data):
    """{track id: {"kind", "timescale", "samples": [(dts, pts, duration, sync)]}}
    with times in seconds, from the moov and every moof."""
    moov = child(data, 0, len(data), b"moov")
    info, defaults = {}, {}
    for k, s, e in boxes(data, *moov):
        if k == b"trak":
            tkhd = child(data, s, e, b"tkhd")
            version = data[tkhd[0]]
            track = struct.unpack(">I", data[tkhd[0] + (20 if version == 1 else 12):][:4])[0]
            mdia = child(data, s, e, b"mdia")
            mdhd = child(data, *mdia, b"mdhd")
            version = data[mdhd[0]]
            scale = struct.unpack(">I", data[mdhd[0] + (20 if version == 1 else 12):][:4])[0]
            hdlr = child(data, *mdia, b"hdlr")
            kind = data[hdlr[0] + 8:hdlr[0] + 12].decode("latin-1")
            info[track] = {"kind": kind, "timescale": scale, "samples": []}
        elif k == b"mvex":
            for k2, s2, e2 in boxes(data, s, e):
                if k2 == b"trex":
                    track, _, duration, size, flags = struct.unpack(">IIIII", data[s2 + 4:s2 + 24])
                    defaults[track] = (duration, flags)
    for k, s, e in boxes(data):
        if k != b"moof":
            continue
        for k2, s2, e2 in boxes(data, s, e):
            if k2 != b"traf":
                continue
            tfhd = child(data, s2, e2, b"tfhd")
            flags = int.from_bytes(data[tfhd[0] + 1:tfhd[0] + 4], "big")
            track = struct.unpack(">I", data[tfhd[0] + 4:tfhd[0] + 8])[0]
            p = tfhd[0] + 8
            duration, sample_flags = defaults.get(track, (0, 0))
            if flags & 0x01:
                p += 8
            if flags & 0x02:
                p += 4
            if flags & 0x08:
                duration = struct.unpack(">I", data[p:p + 4])[0]
                p += 4
            if flags & 0x10:
                p += 4
            if flags & 0x20:
                sample_flags = struct.unpack(">I", data[p:p + 4])[0]
            tfdt = child(data, s2, e2, b"tfdt")
            if data[tfdt[0]] == 1:
                dts = struct.unpack(">Q", data[tfdt[0] + 4:tfdt[0] + 12])[0]
            else:
                dts = struct.unpack(">I", data[tfdt[0] + 4:tfdt[0] + 8])[0]
            if dts >= 1 << 62:
                raise ValueError("track %d: negative decode time %d" % (track, dts - (1 << 64)))
            scale = info[track]["timescale"]
            for k3, s3, e3 in boxes(data, s2, e2):
                if k3 != b"trun":
                    continue
                version = data[s3]
                tflags = int.from_bytes(data[s3 + 1:s3 + 4], "big")
                count = struct.unpack(">I", data[s3 + 4:s3 + 8])[0]
                p = s3 + 8
                if tflags & 0x01:
                    p += 4
                first_flags = None
                if tflags & 0x04:
                    first_flags = struct.unpack(">I", data[p:p + 4])[0]
                    p += 4
                for n in range(count):
                    d, f, cts = duration, sample_flags, 0
                    if tflags & 0x100:
                        d = struct.unpack(">I", data[p:p + 4])[0]
                        p += 4
                    if tflags & 0x200:
                        p += 4
                    if tflags & 0x400:
                        f = struct.unpack(">I", data[p:p + 4])[0]
                        p += 4
                    if tflags & 0x800:
                        cts = struct.unpack(">i" if version == 1 else ">I", data[p:p + 4])[0]
                        p += 4
                    if n == 0 and first_flags is not None:
                        f = first_flags
                    sync = not (f & 0x10000)
                    info[track]["samples"].append((dts / scale, (dts + cts) / scale, d / scale, sync))
                    dts += d
    return info
