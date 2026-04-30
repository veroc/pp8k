"""FLM film table file parser and writer.

Reads, writes, and decrypts Polaroid .FLM film table files.  These files
contain lookup tables (LUTs) that control how the PP8K's CRT intensity
maps to film density for each color channel.

File structure (15,639 bytes, encrypted):
    Bytes 0-188:     File header (name, camera type, flags, aspect ratio,
                     internal name, per-channel scale factors, metadata)
    Bytes 189-1724:  LUT Set 0 (Base) -- no per-set header, 1536 bytes
                     of LUT data (3 channels x 256 entries x uint16 LE)
    Bytes 1725+:     Sets 1-9, each with a 10-byte header followed by
                     1536 bytes of LUT data

Each LUT set corresponds to a resolution tier.  The set headers encode
the target resolution (1024, 2048, 4032, 4096, 4097, 8192 horizontal
pixels).  Set 7 is the standard 4096 (4K) set; set 9 is the 8192 (8K)
set.  Lower-resolution sets have higher per-channel scale factors because
fewer scan lines means more CRT exposure time per line.

Encryption:
    FLM files use a stream cipher based on a linear congruential PRNG
    (seed=0x35, multiplier=13, increment=7) combined with a fixed bit
    permutation.  The cipher is applied byte-by-byte.

    Credit: cipher reverse-engineered by Phil Pemberton (dp_filmtable_crypt.c).
"""

import struct
from pathlib import Path
from typing import NamedTuple

from .constants import BW_FILTER_NAMES, CAMERA_TYPES


# ---------------------------------------------------------------------------
# FLM file format constants
# ---------------------------------------------------------------------------

FLM_FILE_SIZE = 15639          # exact size of every .FLM file
FILE_HEADER_SIZE = 189         # bytes before the first LUT data
LUT_DATA_SIZE = 1536           # 3 channels x 256 entries x 2 bytes (uint16 LE)
SET_HEADER_SIZE = 10           # per-set header for sets 1-9
LUT_SETS_COUNT = 10            # sets 0 through 9
CHANNEL_ENTRIES = 256          # 256 uint16 values per channel per set


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class LutChannel(NamedTuple):
    """One color channel of a LUT set: 256 uint16 values.

    Each value represents the CRT drive level for a given input pixel
    value (0-255).  The display-space value is: stored_value x scale_factor.
    """
    values: tuple


class LutSet(NamedTuple):
    """One complete LUT set: three color channels plus scale factors.

    The scale factors convert stored uint16 values to display values:
        display_value = stored_value x scale_factor

    Original Polaroid FLM files typically have scale_r varying (1-50)
    with scale_g and scale_b fixed at 1.  Third-party tools (CFR) may
    set all three scales independently.

    The `header` field holds the 10 raw bytes of the per-set header for
    sets 1-9 (None for set 0, which has no per-set header).  Preserving
    it lets us round-trip FLM files byte-perfectly.
    """
    red: LutChannel
    green: LutChannel
    blue: LutChannel
    scale_r: int
    scale_g: int
    scale_b: int
    header: bytes = None


class FilmTable(NamedTuple):
    """A parsed .FLM film table -- everything needed to configure an exposure.

    The `encrypted_data` field holds the raw file bytes (still encrypted),
    ready to upload to the device via DFRCMD sub 10.  The device firmware
    decrypts internally.

    The `flags` and `raw_extended` fields preserve original header bytes
    for byte-perfect round-trip through serialize_flm().
    """
    name: str                           # display name (e.g. "Ektachrome 100")
    internal_name: str                  # 8-char unique ID (e.g. "EKTA100")
    camera_type: int                    # numeric type (0-5)
    camera_type_name: str               # human name (e.g. "35mm")
    is_bw: bool                         # True if B&W film table
    bw_filter: int                      # filter index (0-3), meaningful only if is_bw
    bw_filter_name: str                 # filter name (e.g. "Green")
    aspect_w: int                       # aspect ratio width component
    aspect_h: int                       # aspect ratio height component
    lut_sets: tuple                     # 10 LUT sets (one per resolution tier)
    encrypted_data: bytes               # raw FLM file bytes for device upload
    flags: int = 0                      # raw flags byte at file offset 25
    raw_extended: bytes = b""           # file bytes 28-188 (161 bytes of extended header)


# ---------------------------------------------------------------------------
# Stream cipher
# ---------------------------------------------------------------------------

class _FilmTableCrypto:
    """Stream cipher for .FLM film table files.

    Uses a linear congruential PRNG:  next = (13 * current + 7) mod 256
    starting from seed 0x35.  Each PRNG output byte is XOR'd with the
    input, then a fixed bit permutation is applied (or vice versa for
    encryption).
    """

    def __init__(self):
        self._keystream = 0x35

    def _reset(self):
        self._keystream = 0x35

    def _next_key(self):
        """Advance the PRNG and return the current state (before update)."""
        x = self._keystream
        self._keystream = ((13 * self._keystream) + 7) & 0xFF
        return x

    @staticmethod
    def _bitperm(b):
        """Fixed bit permutation: swap bits 0<->7, swap bits 3<->4,
        XOR bits 5-6 with bits 1-2."""
        return (
            ((b & 0x01) << 7)
            | ((b & 0x80) >> 7)
            | (b & 0x06)
            | ((b & 0x08) << 1)
            | ((b & 0x10) >> 1)
            | ((b & 0x60) ^ ((b & 0x06) << 4))
        )

    def decrypt(self, data):
        """Decrypt FLM file contents.  Order: XOR with keystream, then permute."""
        self._reset()
        return bytes(self._bitperm(self._next_key() ^ b) for b in data)

    def encrypt(self, data):
        """Encrypt FLM file contents.  Inverse of decrypt.

        The bit permutation is self-inverse (bits 0<->7 swap, 3<->4 swap,
        and bits 5/6 XOR with 1/2 are all self-inverse), so encrypt
        uses the same _bitperm function as decrypt, just in the opposite
        order: permute first, then XOR with the keystream.
        """
        self._reset()
        return bytes(self._next_key() ^ self._bitperm(b) for b in data)


_crypto = _FilmTableCrypto()


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _lut_set_offsets(set_index):
    """Calculate byte offsets for a LUT set within the decrypted data.

    Returns (header_offset, data_offset).  Set 0 has no per-set header
    so header_offset is None.

    Layout:
        [file_header: 189 bytes]
        [set 0 LUT data: 1536 bytes]              <- no header
        [set 1 header: 10 bytes][set 1 data: 1536] <- sets 1-9
        [set 2 header: 10 bytes][set 2 data: 1536]
        ...
    """
    if set_index == 0:
        return (None, FILE_HEADER_SIZE)
    base = FILE_HEADER_SIZE + LUT_DATA_SIZE + (set_index - 1) * (SET_HEADER_SIZE + LUT_DATA_SIZE)
    return (base, base + SET_HEADER_SIZE)


def _parse_lut_channel(dec, offset):
    """Parse 256 uint16 little-endian values starting at offset."""
    values = tuple(
        struct.unpack_from("<H", dec, offset + i * 2)[0]
        for i in range(CHANNEL_ENTRIES)
    )
    return LutChannel(values=values)


def _parse_lut_set(dec, set_index):
    """Parse one LUT set (header + 3 channels) from decrypted data."""
    hdr_off, data_off = _lut_set_offsets(set_index)

    # Per-channel scale factors:
    #   Sets 1-9: bytes 2, 3, 4 of the set header
    #   Set 0: file header bytes 180, 182, 183
    if hdr_off is not None:
        header = bytes(dec[hdr_off:hdr_off + SET_HEADER_SIZE])
        scale_r = header[2]
        scale_g = header[3]
        scale_b = header[4]
    else:
        header = None
        scale_r = dec[180]
        scale_g = dec[182]
        scale_b = dec[183]

    return LutSet(
        red=_parse_lut_channel(dec, data_off),
        green=_parse_lut_channel(dec, data_off + CHANNEL_ENTRIES * 2),
        blue=_parse_lut_channel(dec, data_off + CHANNEL_ENTRIES * 4),
        scale_r=scale_r,
        scale_g=scale_g,
        scale_b=scale_b,
        header=header,
    )


def load_flm(path):
    """Load and parse a .FLM film table file.

    Reads the encrypted file, decrypts it to extract metadata and LUT
    data, and returns a FilmTable with both the parsed data and the
    original encrypted bytes (needed for device upload).

    Args:
        path: Path to a .FLM file.

    Returns:
        FilmTable with all metadata, 10 LUT sets, and raw encrypted data.

    Raises:
        ValueError: If the file is not exactly 15,639 bytes.
        FileNotFoundError: If the file does not exist.
    """
    path = Path(path)
    raw = path.read_bytes()

    if len(raw) != FLM_FILE_SIZE:
        raise ValueError(
            f"Invalid FLM file: expected {FLM_FILE_SIZE} bytes, "
            f"got {len(raw)} ({path.name})"
        )

    # Decrypt for parsing -- the raw (encrypted) bytes are kept for upload
    dec = _crypto.decrypt(raw)

    # --- Parse file header (bytes 0-188) ---

    # Film name: up to 24 ASCII characters, null-terminated
    name = dec[0:24].split(b"\x00")[0].decode("ascii", errors="replace")

    # Internal name: 8 characters at offset 32, null-terminated
    internal_name = dec[32:40].split(b"\x00")[0].decode("ascii", errors="replace")

    # Camera type: single byte at offset 24
    camera_type = dec[24]
    camera_type_name = CAMERA_TYPES.get(camera_type, f"Unknown({camera_type})")

    # Flags byte at offset 25:
    #   Bit 4 (0x10) = B&W flag
    #   Bits 2-3 = filter selection (only meaningful for B&W)
    flags = dec[25]
    is_bw = bool(flags & 0x10)
    bw_filter = (flags >> 2) & 0x03 if is_bw else 0
    bw_filter_name = BW_FILTER_NAMES.get(bw_filter, f"Unknown({bw_filter})")

    # Aspect ratio at offsets 26-27
    aspect_w = dec[26]
    aspect_h = dec[27]

    # Preserve the extended header (bytes 28-188) verbatim for round-trip
    raw_extended = bytes(dec[28:FILE_HEADER_SIZE])

    # --- Parse all 10 LUT sets ---
    lut_sets = tuple(_parse_lut_set(dec, i) for i in range(LUT_SETS_COUNT))

    return FilmTable(
        name=name,
        internal_name=internal_name,
        camera_type=camera_type,
        camera_type_name=camera_type_name,
        is_bw=is_bw,
        bw_filter=bw_filter,
        bw_filter_name=bw_filter_name,
        aspect_w=aspect_w,
        aspect_h=aspect_h,
        lut_sets=lut_sets,
        encrypted_data=raw,
        flags=flags,
        raw_extended=raw_extended,
    )


# ---------------------------------------------------------------------------
# Serializer
# ---------------------------------------------------------------------------

def serialize_flm(table):
    """Serialize a FilmTable back to encrypted .FLM bytes.

    Produces a 15,639-byte encrypted blob suitable for either writing to
    disk or uploading to the device via DFRCMD sub 10.  Tables loaded
    via load_flm() and serialized again round-trip byte-perfectly.

    Args:
        table: A FilmTable.  Must have exactly 10 LUT sets; sets 1-9
               must have a 10-byte `header` populated (set 0 must not).

    Returns:
        15,639 bytes of encrypted FLM data.

    Raises:
        ValueError: If the table structure is invalid.
    """
    if len(table.lut_sets) != LUT_SETS_COUNT:
        raise ValueError(
            f"FilmTable must have {LUT_SETS_COUNT} LUT sets, "
            f"got {len(table.lut_sets)}"
        )

    buf = bytearray(FLM_FILE_SIZE)

    # --- File header ---

    # Film name (bytes 0-23, ASCII, null-terminated).  The firmware reads
    # this as a C string, so byte 23 must be 0 -- otherwise the read
    # overruns into camera_type/flags/aspect (bytes 24-27) and corrupts
    # later metadata reads on the device.  Cap the payload at 23 bytes
    # to guarantee at least one trailing null.
    name_bytes = table.name.encode("ascii", errors="replace")[:23]
    buf[0:len(name_bytes)] = name_bytes
    buf[23] = 0

    # Camera type (byte 24)
    buf[24] = table.camera_type & 0xFF

    # Flags (byte 25): reconstruct from is_bw + bw_filter if B&W, else use raw flags
    if table.is_bw:
        buf[25] = 0x10 | ((table.bw_filter & 0x03) << 2)
    else:
        buf[25] = table.flags & 0xFF

    # Aspect ratio (bytes 26-27)
    buf[26] = table.aspect_w & 0xFF
    buf[27] = table.aspect_h & 0xFF

    # Extended header (bytes 28-188) -- preserved verbatim from raw_extended
    ext_len = FILE_HEADER_SIZE - 28  # 161
    if len(table.raw_extended) >= ext_len:
        buf[28:FILE_HEADER_SIZE] = table.raw_extended[:ext_len]
    else:
        # Pad with zeros if raw_extended is short (shouldn't happen for loaded tables)
        buf[28:28 + len(table.raw_extended)] = table.raw_extended

    # Internal name (bytes 32-39, overwrites into raw_extended area).
    # 33/58 original Polaroid FLMs fill all 8 bytes with no terminator
    # and the firmware handles them correctly, so no cap is enforced.
    iname = table.internal_name.encode("ascii", errors="replace")[:8]
    buf[32:32 + len(iname)] = iname
    # Null-pad remaining bytes in the 8-byte internal name slot
    for i in range(32 + len(iname), 40):
        buf[i] = 0

    # --- 10 LUT sets ---
    for i, lut_set in enumerate(table.lut_sets):
        hdr_off, data_off = _lut_set_offsets(i)

        if hdr_off is not None:
            if lut_set.header is None:
                raise ValueError(f"LUT set {i} missing required 10-byte header")
            if len(lut_set.header) != SET_HEADER_SIZE:
                raise ValueError(
                    f"LUT set {i} header must be {SET_HEADER_SIZE} bytes, "
                    f"got {len(lut_set.header)}"
                )
            buf[hdr_off:hdr_off + SET_HEADER_SIZE] = lut_set.header

        # Channel data: 256 uint16 LE values per channel, R then G then B
        for j, val in enumerate(lut_set.red.values):
            struct.pack_into("<H", buf, data_off + j * 2, val & 0xFFFF)
        for j, val in enumerate(lut_set.green.values):
            struct.pack_into("<H", buf, data_off + CHANNEL_ENTRIES * 2 + j * 2, val & 0xFFFF)
        for j, val in enumerate(lut_set.blue.values):
            struct.pack_into("<H", buf, data_off + CHANNEL_ENTRIES * 4 + j * 2, val & 0xFFFF)

    return _crypto.encrypt(bytes(buf))


def save_flm(path, table):
    """Serialize and write a FilmTable to a .FLM file.

    Args:
        path: Destination file path.
        table: FilmTable to save.
    """
    encrypted = serialize_flm(table)
    Path(path).write_bytes(encrypted)


# ---------------------------------------------------------------------------
# Master-curve propagation
# ---------------------------------------------------------------------------
#
# Verified across 57/58 original Polaroid film tables, every FLM follows a
# 2-master authoring convention:
#
#     Sets 0, 2, 4, 6, 7  -- byte-identical copies of "Master A"
#     Sets 1, 3, 5        -- ceil(Master A / 2)
#     Set 8               -- independently authored "Master B"
#     Set 9               -- floor(Master B / 2)
#
# The single counter-example, KG6-100.FLM, is a corrupted derivative
# (Set 0 was edited in isolation by some curve editor without propagating
# to the master-equivalent sets).  A file that violates the convention
# loads different curves at different HRES values, breaking calibration.
#
# We pick **Set 7 as canonical Master A** (loaded by the firmware at
# HRES=4096, the 4K production resolution) and **Set 9 as canonical
# Master B** (loaded at HRES=8192, the 8K production resolution).
#
# `serialize_flm` does not auto-normalize -- byte-perfect round-trip is
# preserved.  Callers that have edited a table should explicitly call
# `normalize_masters` before serializing.


_MASTER_A_COPIES = (0, 2, 4, 6, 7)   # all byte-identical to Set 7
_MASTER_A_HALVES = (1, 3, 5)         # ceil(Set 7 / 2)


def _ceil_half(values):
    return tuple((v + 1) // 2 for v in values)


def _double_clamped(values):
    return tuple(min(0xFFFF, v * 2) for v in values)


def _replace_channels(lut_set, red, green, blue):
    return lut_set._replace(
        red=LutChannel(values=tuple(red)),
        green=LutChannel(values=tuple(green)),
        blue=LutChannel(values=tuple(blue)),
    )


def normalize_masters(table):
    """Return a new FilmTable with derived sets recomputed from the
    canonical masters (Set 7 and Set 9).

    Set 7 propagates byte-identically to Sets 0, 2, 4, 6.  Sets 1, 3, 5
    are recomputed as ceil(Set 7 / 2).  Set 8 is recomputed as 2 x Set 9
    (clamped to u16).  Per-set headers and scale factors are preserved
    untouched.

    Args:
        table: A FilmTable.

    Returns:
        A new FilmTable with normalized LUT sets.
    """
    sets = list(table.lut_sets)
    src_a = sets[7]
    src_b = sets[9]

    # Sets 0, 2, 4, 6 <- Set 7 (Set 7 itself unchanged)
    for idx in _MASTER_A_COPIES:
        if idx == 7:
            continue
        sets[idx] = _replace_channels(
            sets[idx], src_a.red.values, src_a.green.values, src_a.blue.values
        )

    # Sets 1, 3, 5 <- ceil(Set 7 / 2)
    half_r = _ceil_half(src_a.red.values)
    half_g = _ceil_half(src_a.green.values)
    half_b = _ceil_half(src_a.blue.values)
    for idx in _MASTER_A_HALVES:
        sets[idx] = _replace_channels(sets[idx], half_r, half_g, half_b)

    # Set 8 <- 2 * Set 9 (clamped to u16)
    sets[8] = _replace_channels(
        sets[8],
        _double_clamped(src_b.red.values),
        _double_clamped(src_b.green.values),
        _double_clamped(src_b.blue.values),
    )

    return table._replace(lut_sets=tuple(sets))


def validate_masters(table):
    """Return a list of human-readable inconsistency messages for any
    LUT set that doesn't match the 2-master convention.  Empty list = the
    file conforms.

    Useful as a load-time warning ("this file looks corrupted; saving
    will normalize") and to flag legacy hand-edited files.
    """
    issues = []
    sets = table.lut_sets
    src_a = sets[7]

    for idx in (0, 2, 4, 6):
        for ch_name, ch_a, ch_dst in zip(
            "RGB",
            (src_a.red, src_a.green, src_a.blue),
            (sets[idx].red, sets[idx].green, sets[idx].blue),
        ):
            if tuple(ch_dst.values) != tuple(ch_a.values):
                issues.append(f"Set {idx} {ch_name} differs from Set 7 (Master A)")
                break

    for idx in (1, 3, 5):
        for ch_name, ch_a, ch_dst in zip(
            "RGB",
            (src_a.red, src_a.green, src_a.blue),
            (sets[idx].red, sets[idx].green, sets[idx].blue),
        ):
            if tuple(ch_dst.values) != _ceil_half(ch_a.values):
                issues.append(f"Set {idx} {ch_name} is not ceil(Master A / 2)")
                break

    src_b = sets[9]
    set8 = sets[8]
    for ch_name, ch_b, ch_8 in zip(
        "RGB",
        (src_b.red, src_b.green, src_b.blue),
        (set8.red, set8.green, set8.blue),
    ):
        if tuple(ch_8.values) != _double_clamped(ch_b.values):
            issues.append(f"Set 8 {ch_name} is not 2 x Master B (Set 9)")
            break

    return issues
