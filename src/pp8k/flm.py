"""FLM film table file parser.

Reads and decrypts Polaroid .FLM film table files.  These files contain
lookup tables (LUTs) that control how the PP8K's CRT intensity maps to
film density for each color channel.

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
    permutation.  The cipher is applied byte-by-byte.  Encryption and
    decryption are NOT symmetric -- the bit permutation is applied at
    different points in the pipeline.

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
    """
    red: LutChannel
    green: LutChannel
    blue: LutChannel
    scale_r: int
    scale_g: int
    scale_b: int


class FilmTable(NamedTuple):
    """A parsed .FLM film table -- everything needed to configure an exposure.

    The encrypted_data field holds the raw file bytes (still encrypted),
    ready to upload to the device via DFRCMD sub 10.  The device firmware
    decrypts internally.
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
        scale_r = dec[hdr_off + 2]
        scale_g = dec[hdr_off + 3]
        scale_b = dec[hdr_off + 4]
    else:
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
    )
