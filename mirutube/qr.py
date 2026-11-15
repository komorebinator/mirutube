"""The QR code of the address other devices open MiruTube at
(server#serve_address), made here rather than by a library or a web service
(decisions#own-qr): Python's standard library has none, and a home network may
have no internet.

encode() makes the matrix — byte mode, error correction H, the smallest
version the text fits, the mask with the lowest penalty — following ISO/IEC
18004. svg() draws it the way page#top_bar#qr_button describes: rounded dots
in a deep violet gradient on a soft light card — dark on light, as every
reader expects — lightly rounded finder squares and the mark in the middle,
which the highest error correction reads through.
"""

import math

from xml.sax.saxutils import escape

# Error correction H, by version (index 0 unused): the error correction
# codewords of each block, and the number of blocks.
EC_CODEWORDS_H = [
    -1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28,
    30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30]
EC_BLOCKS_H = [
    -1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25,
    25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81]
FORMAT_LEVEL_H = 0b10        # the error correction level's two bits in the format information
MODE_BYTE = 0b0100

# Penalty weights of the mask rules
N1, N2, N3, N4 = 3, 3, 40, 10


# ------------------------------------------------------------ Reed-Solomon

_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def gf_mul(a, b):
    """Multiplication in GF(256) with the QR polynomial x^8+x^4+x^3+x^2+1."""
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def rs_generator(degree):
    """The generator polynomial (x - a^0)(x - a^1)...(x - a^(degree-1)),
    highest coefficient first, the leading 1 left out."""
    poly = [1]
    for i in range(degree):
        nxt = [0] * (len(poly) + 1)
        for j, c in enumerate(poly):
            nxt[j] ^= c
            nxt[j + 1] ^= gf_mul(c, _EXP[i])
        poly = nxt
    return poly[1:]


def rs_remainder(data, degree):
    """The error correction codewords of `data`."""
    gen = rs_generator(degree)
    rem = [0] * degree
    for b in data:
        factor = b ^ rem.pop(0)
        rem.append(0)
        for i, g in enumerate(gen):
            rem[i] ^= gf_mul(g, factor)
    return rem


# --------------------------------------------------------------- layout

def size_of(version):
    return version * 4 + 17


def raw_modules(version):
    """Modules of a version left for data and error correction, once the
    finder, timing and alignment patterns and the format and version
    information have taken theirs."""
    result = (16 * version + 128) * version + 64
    if version >= 2:
        count = version // 7 + 2
        result -= (25 * count - 10) * count - 55
        if version >= 7:
            result -= 36
    return result


def data_codewords(version):
    """Codewords of a version at level H that carry data."""
    return raw_modules(version) // 8 - EC_CODEWORDS_H[version] * EC_BLOCKS_H[version]


def alignment_positions(version):
    """The rows and columns the alignment patterns are centred on."""
    if version == 1:
        return []
    count = version // 7 + 2
    step = (version * 8 + count * 3 + 5) // (count * 4 - 4) * 2
    last = size_of(version) - 7
    return [6] + sorted(last - i * step for i in range(count - 1))


def _bits_of(value, length):
    return [(value >> i) & 1 for i in range(length - 1, -1, -1)]


def format_bits(mask):
    """The 15 bits of format information: level H and the mask, BCH coded."""
    data = FORMAT_LEVEL_H << 3 | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return (data << 10 | rem) ^ 0x5412


def version_bits(version):
    """The 18 bits of version information, for version 7 and above."""
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return version << 12 | rem


class _Matrix:
    def __init__(self, version):
        self.version = version
        self.size = size_of(version)
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.function = [[False] * self.size for _ in range(self.size)]

    def set(self, x, y, dark):
        self.dark[y][x] = bool(dark)
        self.function[y][x] = True

    def draw_function_patterns(self):
        size = self.size
        for i in range(size):
            self.set(6, i, i % 2 == 0)
            self.set(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self.set(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        positions = alignment_positions(self.version)
        last = len(positions) - 1
        for i, ay in enumerate(positions):
            for j, ax in enumerate(positions):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue  # where the finder patterns are
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0)  # reserved now, written once the mask is chosen
        if self.version >= 7:
            bits = version_bits(self.version)
            for i in range(18):
                bit = (bits >> i) & 1
                a, b = size - 11 + i % 3, i // 3
                self.set(a, b, bit)
                self.set(b, a, bit)

    def draw_format(self, mask):
        bits = format_bits(mask)
        size = self.size
        bit = lambda i: (bits >> i) & 1
        for i in range(6):
            self.set(8, i, bit(i))
        self.set(8, 7, bit(6))
        self.set(8, 8, bit(7))
        self.set(7, 8, bit(8))
        for i in range(9, 15):
            self.set(14 - i, 8, bit(i))
        for i in range(8):
            self.set(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set(8, size - 15 + i, bit(i))
        self.set(8, size - 8, True)  # the dark module

    def data_positions(self):
        """The modules data fills, in order: two columns at a time from the
        right, up and down in turn, the timing column skipped."""
        size = self.size
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(size):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = size - 1 - vert if upward else vert
                    if not self.function[y][x]:
                        yield x, y
            right -= 2


MASKS = [
    lambda x, y: (x + y) % 2 == 0,
    lambda x, y: y % 2 == 0,
    lambda x, y: x % 3 == 0,
    lambda x, y: (x + y) % 3 == 0,
    lambda x, y: (x // 3 + y // 2) % 2 == 0,
    lambda x, y: x * y % 2 + x * y % 3 == 0,
    lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
]

_FINDER_LIKE = ([1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0], [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1])


def penalty(dark):
    """The penalty of a masked matrix by the four rules of the standard:
    runs of one colour, 2x2 blocks, patterns like a finder, and the share of
    dark modules away from half."""
    size = len(dark)
    score = 0
    lines = [row for row in dark] + [[dark[y][x] for y in range(size)] for x in range(size)]
    for line in lines:
        run, colour = 0, None
        for m in line:
            if m == colour:
                run += 1
            else:
                if run >= 5:
                    score += N1 + run - 5
                run, colour = 1, m
        if run >= 5:
            score += N1 + run - 5
        bits = [int(m) for m in line]
        for i in range(size - 10):
            if bits[i:i + 11] in _FINDER_LIKE:
                score += N3
    for y in range(size - 1):
        for x in range(size - 1):
            if dark[y][x] == dark[y][x + 1] == dark[y + 1][x] == dark[y + 1][x + 1]:
                score += N2
    total = size * size
    darks = sum(sum(row) for row in dark)
    percent = darks * 100 // total
    lower = percent - percent % 5
    score += min(abs(lower - 50), abs(lower + 5 - 50)) // 5 * N4
    return score


# ---------------------------------------------------------------- encode

def codewords(data, version):
    """The data codewords of `data` in byte mode, padded to the version's
    capacity at level H."""
    capacity = data_codewords(version) * 8
    count_bits = 8 if version < 10 else 16
    bits = _bits_of(MODE_BYTE, 4) + _bits_of(len(data), count_bits)
    for b in data:
        bits += _bits_of(b, 8)
    if len(bits) > capacity:
        raise ValueError("does not fit version %d" % version)
    bits += [0] * min(4, capacity - len(bits))
    bits += [0] * (-len(bits) % 8)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = 0
    while len(words) < capacity // 8:
        words.append((0xEC, 0x11)[pad % 2])
        pad += 1
    return words


def interleave(words, version):
    """The data codewords split into blocks, each with its error correction,
    and the whole interleaved as it is laid out: the data of every block in
    turn, then the error correction of every block in turn."""
    count = EC_BLOCKS_H[version]
    ec_len = EC_CODEWORDS_H[version]
    total = raw_modules(version) // 8
    short_count = count - total % count
    short_len = total // count - ec_len   # data codewords in a short block
    blocks, at = [], 0
    for i in range(count):
        n = short_len + (0 if i < short_count else 1)
        blocks.append(words[at:at + n])
        at += n
    ecs = [rs_remainder(b, ec_len) for b in blocks]
    result = []
    for i in range(short_len + 1):
        for b in blocks:
            if i < len(b):
                result.append(b[i])
    for i in range(ec_len):
        for e in ecs:
            result.append(e[i])
    return result


def smallest_version(data):
    for version in range(1, 41):
        count_bits = 8 if version < 10 else 16
        if 4 + count_bits + 8 * len(data) <= data_codewords(version) * 8:
            return version
    raise ValueError("too long for a QR code: %d bytes" % len(data))


def encode(text, mask=None):
    """The QR code of `text` as rows of booleans, True for a dark module,
    without the quiet zone: byte mode (UTF-8), error correction H, the
    smallest version that holds it, and — unless `mask` names one — the mask
    of the lowest penalty."""
    data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    version = smallest_version(data)
    matrix = _Matrix(version)
    matrix.draw_function_patterns()
    stream = interleave(codewords(data, version), version)
    bits = [(w >> (7 - i)) & 1 for w in stream for i in range(8)]
    positions = list(matrix.data_positions())
    for n, (x, y) in enumerate(positions):
        matrix.dark[y][x] = bool(bits[n]) if n < len(bits) else False  # remainder bits

    def masked(m):
        matrix.draw_format(m)
        dark = [row[:] for row in matrix.dark]
        rule = MASKS[m]
        for x, y in positions:
            if rule(x, y):
                dark[y][x] = not dark[y][x]
        return dark

    if mask is None:
        mask = min(range(8), key=lambda m: penalty(masked(m)))
    return masked(mask)


# ------------------------------------------------------------------- SVG

QUIET = 4                    # modules of quiet zone around the code
CARD = "#f3f0fc"             # the soft light card the code is drawn on
# the dots' gradient, deep violet throughout: dark on light, the polarity
# every reader expects, so any phone camera reads it
VIOLETS = ("#5b21b6", "#4c1d95", "#2e1065")
# corner radii of a finder square's ring, of the hole in it and of its eye,
# in modules: lightly rounded, so a reader keeps the corners it finds the
# code by
FINDER_RADII = (0.35, 0.2, 0.4)
DOT = 0.45                   # a dot's radius, in modules
MARK_SHARE = 0.2             # of the code's width the mark takes, at most


def _in_finder(x, y, size):
    return (x < 7 and y < 7) or (x >= size - 7 and y < 7) or (x < 7 and y >= size - 7)


def _alignment_centres(version):
    """The centres of a version's alignment patterns."""
    positions = alignment_positions(version)
    last = len(positions) - 1
    return [(positions[j], positions[i]) for i in range(len(positions)) for j in range(len(positions))
            if (i, j) not in ((0, 0), (0, last), (last, 0))]


def mark_area(size):
    """(first, last) module, inclusive, of the square in the middle left clear
    for the mark: about a fifth of the width, with the same margin on every
    side."""
    side = max(3, int(size * MARK_SHARE))
    if side % 2 != size % 2:
        side -= 1
    first = (size - side) // 2
    return first, first + side - 1


def svg(text, ident="mirutube-qr"):
    """The QR code of `text` drawn as SVG to match the site: rounded dots in a
    deep violet gradient on a soft light card — dark on light, as every
    reader expects — lightly rounded finder squares, and MiruTube's
    mark in the middle, over a square of modules left clear for it — which
    error correction H reads through. `ident` prefixes the ids of the
    gradients, so two codes in one page do not share them."""
    dark = encode(text)
    size = len(dark)
    full = size + 2 * QUIET
    first, last = mark_area(size)
    out = []
    add = out.append
    add('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" role="img" aria-label="%s">'
        % (full, full, escape(text, {'"': "&quot;"})))
    add('<title>%s</title>' % escape(text))
    add('<defs>'
        '<linearGradient id="{0}-dots" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="{1}" y2="{1}">'
        '<stop offset="0" stop-color="{2}"/><stop offset="0.5" stop-color="{3}"/>'
        '<stop offset="1" stop-color="{4}"/></linearGradient>'
        '</defs>'.format(ident, full, VIOLETS[0], VIOLETS[1], VIOLETS[2]))
    add('<rect width="%d" height="%d" rx="%g" fill="%s"/>' % (full, full, full * 0.06, CARD))
    add('<g fill="url(#%s-dots)">' % ident)
    under_mark = lambda x, y: first <= x <= last and first <= y <= last
    # alignment patterns are drawn whole, like small finders: a reader
    # looks for them as a ring around a dot, which separate dots are not
    aligned = [(x, y) for x, y in _alignment_centres((size - 17) // 4)
               if not any(under_mark(x + dx, y + dy) for dx in (-2, 2) for dy in (-2, 2))]
    in_alignment = {(x + dx, y + dy) for x, y in aligned for dx in range(-2, 3) for dy in range(-2, 3)}
    for y in range(size):
        for x in range(size):
            if not dark[y][x] or _in_finder(x, y, size) or (x, y) in in_alignment or under_mark(x, y):
                continue
            add('<circle cx="%g" cy="%g" r="%g"/>' % (QUIET + x + 0.5, QUIET + y + 0.5, DOT))
    for x, y in aligned:
        ox, oy = QUIET + x - 2, QUIET + y - 2
        add('<path fill-rule="evenodd" d="%s %s"/>' % (_rounded(ox, oy, 5, FINDER_RADII[1]),
                                                        _rounded(ox + 1, oy + 1, 3, FINDER_RADII[1])))
        add('<circle cx="%g" cy="%g" r="0.5"/>' % (ox + 2.5, oy + 2.5))
    for fx, fy in ((0, 0), (size - 7, 0), (0, size - 7)):
        ox, oy = QUIET + fx, QUIET + fy
        # the ring: a rounded 7x7 square less a rounded 5x5 one inside it
        outer, hole, eye = FINDER_RADII
        add('<path fill-rule="evenodd" d="%s %s"/>' % (_rounded(ox, oy, 7, outer), _rounded(ox + 1, oy + 1, 5, hole)))
        add('<rect x="%g" y="%g" width="3" height="3" rx="%g"/>' % (ox + 2, oy + 2, eye))
    add('</g>')
    # the mark, as static/icons/mark.svg draws it: a violet folder with a
    # white sakura on it, its heart pink
    side = last - first + 1
    mx, my = QUIET + first + 0.25, QUIET + first + 0.25
    ms = side - 0.5
    add('<g transform="translate(%g %g) scale(%g)">' % (mx, my, ms / 512))
    add('<path d="M76 132 H204 Q214 132 221 139 L250 168 H436 Q460 168 460 192 V392 Q460 416 436 416 H76 Q52 416 52 392 V156 Q52 132 76 132 Z" fill="#8b5cf6"/>')
    add('<g transform="translate(256 294) scale(0.94)" fill="#fff">')
    for turn in range(0, 360, 72):
        add('<path d="M0 -12 C 34 -30, 46 -72, 24 -98 L 0 -82 L -24 -98 C -46 -72, -34 -30, 0 -12 Z" '
            'transform="rotate(%d)"/>' % turn)
    add('</g><g fill="#ec4899">')
    for k in range(5):
        a = math.radians(k * 72 + 36)
        add('<circle cx="%.2f" cy="%.2f" r="4.2"/>' % (256 + math.sin(a) * 15, 294 - math.cos(a) * 15))
    add('<circle cx="256" cy="294" r="6.3"/></g>')
    add('</g>')
    add('</svg>')
    return "".join(out)


def _rounded(x, y, side, r):
    """The path of a square with rounded corners."""
    return ("M{x0:g} {y}H{x1:g}A{r:g} {r:g} 0 0 1 {x2:g} {y0:g}V{y1:g}A{r:g} {r:g} 0 0 1 {x1:g} {y2:g}"
            "H{x0:g}A{r:g} {r:g} 0 0 1 {x} {y1:g}V{y0:g}A{r:g} {r:g} 0 0 1 {x0:g} {y}Z").format(
        x=x, y=y, r=r, x0=x + r, x1=x + side - r, x2=x + side, y0=y + r, y1=y + side - r, y2=y + side)
