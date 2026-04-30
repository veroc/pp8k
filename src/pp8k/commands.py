"""SCSI command wrappers for the ProPalette 8000.

Pure functions that operate on a Transport.  No state, no classes.
Each function builds a 6-byte CDB (Command Descriptor Block), calls
transport.execute() to send it, and decodes the response into a
Python dict or typed value.

The PP8K uses 6-byte SCSI CDBs exclusively.  The general CDB format is:
    Byte 0: opcode
    Byte 1: usually 0
    Bytes 2-4: command-specific parameters
    Byte 5: control byte (usually 0)

For DFRCMD (opcode 0x0C), byte 2 is the subcommand selector and
bytes 3-5 carry subcommand-specific parameters.
"""

import struct

from .constants import SERVO_FULL
from .errors import SCSIError
from .transport import (
    OP_DFRCMD,
    OP_INQUIRY,
    OP_MODE_SELECT,
    OP_MODE_SENSE,
    OP_PRINT,
    OP_REQUEST_SENSE,
    OP_STOP_PRINT,
    OP_TEST_UNIT_READY,
    SUB_ASPECT_RATIO,
    SUB_CURRENT_STATUS,
    SUB_FILM_NAME,
    SUB_GET_COLOR_TAB,
    SUB_INQUIRY_BLOCK,
    SUB_RESET_TO_DFLT,
    SUB_SET_COLOR_TAB,
    SUB_START_EXPOSURE,
    SUB_TERMINATE_EXPOSURE,
    SUB_UPLOAD_FILM_TABLE,
)


def inquiry(t):
    """INQUIRY (0x12) -- identify the device.

    Requests 63 bytes of identification data.  The PP8K returns:
        Bytes 8-14:   Identification ("DP2SCSI")
        Bytes 16-31:  Product name ("ProPalette 8K")
        Bytes 32-35:  Firmware revision (" 568")
        Bytes 40-41:  Buffer size in KB (big-endian uint16)
        Bytes 46-47:  Max horizontal resolution (big-endian uint16)
        Bytes 50-51:  Max vertical resolution (big-endian uint16)
    """
    data = t.execute( bytes([OP_INQUIRY, 0, 0, 0, 63, 0]), data_in_len=63)
    ident = data[8:15].decode("ascii", errors="replace").rstrip("\x00 ").strip()
    product = data[16:32].decode("ascii", errors="replace").rstrip("\x00 ").strip()
    revision = data[32:36].decode("ascii", errors="replace").rstrip("\x00 ").strip()
    buffer_kb = struct.unpack_from(">H", data, 40)[0]

    # Extract firmware version number from revision string
    fw_digits = "".join(c for c in revision if c.isdigit())
    fw = int(fw_digits) if fw_digits else 0

    return {
        "identification": ident,
        "product": product,
        "firmware": fw,
        "revision": revision,
        "buffer_kb": buffer_kb,
        "hres_max": struct.unpack_from(">H", data, 46)[0],
        "vres_max": struct.unpack_from(">H", data, 50)[0],
    }


def test_unit_ready(t):
    """TEST UNIT READY (0x00) -- check device readiness.

    Returns True if the device accepts the command without error.
    A CHECK CONDITION response means the device is not ready (still
    calibrating, mechanical error, etc.).
    """
    try:
        t.execute( bytes([OP_TEST_UNIT_READY, 0, 0, 0, 0, 0]))
        return True
    except SCSIError:
        return False


def request_sense(t):
    """REQUEST SENSE (0x03) -- read error details.

    Returns 10 bytes of sense data, decoded into sense key, EOM flag,
    and Additional Sense Code (ASC).
    """
    data = t.execute( bytes([OP_REQUEST_SENSE, 0, 0, 0, 10, 0]), data_in_len=10)
    sense_key = data[2] & 0x0F if len(data) > 2 else 0
    eom = bool(data[2] & 0x40) if len(data) > 2 else False
    asc = (data[8] << 8 | data[9]) if len(data) > 9 else 0
    return {"sense_key": sense_key, "eom": eom, "asc": asc, "raw": data}


def mode_sense(t):
    """MODE SENSE (0x1A) -- read current device configuration.

    Returns a 61-byte parameter block with the active film slot,
    resolution, luminance/color balance/exposure time per channel,
    camera back type, and frame counter.

    Field offsets in the 61-byte response:
        4-5:   Buffer size (KB, big-endian)
        8:     Film slot number (currently selected via MODE SELECT)
        10-11: Horizontal resolution (big-endian)
        17-18: Vertical resolution (big-endian)
        22-24: Luminance R, G, B (0-200 each)
        26-28: Color balance R, G, B
        30-32: Exposure time R, G, B
        46-49: Camera back identifier (ASCII)
        58-59: Lifetime exposure counter (big-endian, unit-lifetime, not session)

    Byte 6 is a vendor status byte (typically non-zero on a powered unit)
    and is not the film slot -- earlier driver revisions parsed it that
    way by mistake.
    """
    data = t.execute( bytes([OP_MODE_SENSE, 0, 0, 0, 61, 0]), data_in_len=61)
    return {
        "buffer_kb": struct.unpack_from(">H", data, 4)[0],
        "film_number": data[8],
        "hres": struct.unpack_from(">H", data, 10)[0],
        "vres": struct.unpack_from(">H", data, 17)[0],
        "lum_rgb": (data[22], data[23], data[24]),
        "cbal_rgb": (data[26], data[27], data[28]),
        "etime_rgb": (data[30], data[31], data[32]),
        "camera_back": data[46:50].decode("ascii", errors="replace").strip(),
        "lifetime_exposures": struct.unpack_from(">H", data, 58)[0],
    }


def mode_select(
    t,
    film=4,
    hres=4096,
    vres=2730,
    servo=SERVO_FULL,
):
    """MODE SELECT (0x15) -- configure device for exposure.

    Sends a 43-byte parameter block that sets the film slot, resolution,
    servo mode, and default exposure parameters.

    Parameter block layout:
        3:     Descriptor length (39)
        4:     Film table slot number
        6-7:   Horizontal resolution (big-endian)
        10-11: Line length = horizontal resolution (big-endian)
        13-14: Vertical resolution (big-endian)
        18-20: Luminance R/G/B (default: 100/100/100)
        22-24: Color balance R/G/B (default: 3/3/3)
        26-28: Exposure time R/G/B (default: 100/100/100)
        30:    LTDRK (light/dark threshold, default: 3)
        31-32: Image height = vertical resolution (big-endian)
        33:    Servo mode (4 = FULL calibration)
    """
    buf = bytearray(43)
    buf[3] = 39                            # descriptor length
    buf[4] = film                          # film table slot
    buf[6] = (hres >> 8) & 0xFF            # HRES MSB
    buf[7] = hres & 0xFF                   # HRES LSB
    buf[10] = (hres >> 8) & 0xFF           # LINE_LENGTH = HRES
    buf[11] = hres & 0xFF
    buf[13] = (vres >> 8) & 0xFF           # VRES MSB
    buf[14] = vres & 0xFF                  # VRES LSB
    buf[18] = 100                          # LUM_RED
    buf[19] = 100                          # LUM_GREEN
    buf[20] = 100                          # LUM_BLUE
    buf[22] = 3                            # CBAL_RED
    buf[23] = 3                            # CBAL_GREEN
    buf[24] = 3                            # CBAL_BLUE
    buf[26] = 100                          # ETIME_RED
    buf[27] = 100                          # ETIME_GREEN
    buf[28] = 100                          # ETIME_BLUE
    buf[30] = 3                            # LTDRK
    buf[31] = (vres >> 8) & 0xFF           # IMAGE_HEIGHT MSB
    buf[32] = vres & 0xFF                  # IMAGE_HEIGHT LSB
    buf[33] = servo                        # Servo mode
    t.execute( bytes([OP_MODE_SELECT, 0, 0, 0, 43, 0]), data_out=bytes(buf))


def set_color_tab(t, channel, data):
    """SET_COLOR_TAB (DFRCMD sub 1) -- load a 256-byte gamma LUT.

    The CDB encodes the channel in byte 5: channel << 6.
    Byte 3 is always 0x01 (unknown purpose, required by firmware).

    Args:
        t: SCSI Transport.
        channel: Color channel (0=RED, 1=GREEN, 2=BLUE).
        data: 256 bytes of LUT data.
    """
    assert len(data) == 256, f"Color table must be 256 bytes, got {len(data)}"
    cdb = bytes([OP_DFRCMD, 0, SUB_SET_COLOR_TAB, 0x01, 0x00, channel << 6])
    t.execute( cdb, data_out=data)


def get_color_tab(t, channel):
    """GET_COLOR_TAB (DFRCMD sub 2) -- read back a per-exposure gamma LUT.

    Returns the 256-byte LUT most recently set via set_color_tab() for
    the given channel.  Useful for verifying what the device currently
    holds for a channel (e.g. after a SET_COLOR_TAB sequence).

    Args:
        t: SCSI Transport.
        channel: Color channel (0=RED, 1=GREEN, 2=BLUE).

    Returns:
        256 bytes of LUT data.
    """
    cdb = bytes([OP_DFRCMD, 0, SUB_GET_COLOR_TAB, 0x01, 0x00, channel << 6])
    return t.execute( cdb, data_in_len=256)


def start_exposure(t):
    """START_EXPOSURE (DFRCMD sub 0) -- begin CRT calibration and exposure.

    After this command, the device runs an automatic CRT calibration
    cycle that takes 15-25 seconds.  The caller must wait for calibration
    to complete before sending scanlines.  Use current_status() to poll
    until exposure_state becomes non-zero.

    Uses a 60-second timeout because calibration can take a while.
    """
    t.execute( bytes([OP_DFRCMD, 0, SUB_START_EXPOSURE, 0, 0, 0]), timeout=60000)


def print_line(t, line_no, color, pixels):
    """PRINT (0x0A) -- send one scanline of image data.

    The payload is: [line_number (2 bytes big-endian)] + [pixel_data].
    The CDB encodes the color channel in byte 5 (channel << 6) and the
    total payload size in bytes 3-4 (big-endian).

    Args:
        t: SCSI Transport.
        line_no: Vertical line number (0-based).
        color: Color channel (0=RED, 1=GREEN, 2=BLUE).
        pixels: Raw pixel data (one byte per pixel, length = hres).
    """
    payload = struct.pack(">H", line_no) + pixels
    size = len(payload)
    cdb = bytes([OP_PRINT, 0, 0, (size >> 8) & 0xFF, size & 0xFF, color << 6])
    t.execute( cdb, data_out=payload)


def terminate_exposure(t):
    """TERMINATE_EXPOSURE (DFRCMD sub 3) -- finalize the exposure.

    Signals that all scanlines have been sent.  The device writes any
    remaining data from its buffer, advances the film frame, and returns
    to idle state.
    """
    t.execute( bytes([OP_DFRCMD, 0, SUB_TERMINATE_EXPOSURE, 0, 0, 0]))


def stop_print(t):
    """STOP PRINT (0x1B) -- emergency abort of an exposure.

    Best-effort: silently catches SCSI errors since the device may
    already be in an error state when this is called.
    """
    try:
        t.execute( bytes([OP_STOP_PRINT, 0, 0, 0, 0, 0]))
    except SCSIError:
        pass


def current_status(t):
    """CURRENT_STATUS (DFRCMD sub 6) -- real-time device state.

    Returns 7 bytes:
        Bytes 0-1: Buffer free space in KB (big-endian)
        Byte 2:    Exposure state (0=calibrating/idle, non-zero=active)
        Bytes 3-4: Current line being processed (big-endian)
        Byte 5:    Active film slot
        Byte 6:    Status byte
    """
    data = t.execute(
        bytes([OP_DFRCMD, 0, SUB_CURRENT_STATUS, 0, 7, 0]), data_in_len=7
    )
    return {
        "buffer_free_kb": struct.unpack_from(">H", data, 0)[0],
        "exposure_state": data[2],
        "current_line": struct.unpack_from(">H", data, 3)[0],
        "film_slot": data[5],
        "status": data[6],
    }


def film_name(t, slot):
    """FILM_NAME (DFRCMD sub 4) -- read film table name from a device slot.

    The CDB encodes the slot number in byte 3 and requests 24 bytes.
    The response has 3 header bytes followed by the ASCII name.

    Returns None for empty slots (indicated by Illegal Request sense key).
    """
    cdb = bytes([OP_DFRCMD, 0, SUB_FILM_NAME, slot, 24, 0])
    try:
        data = t.execute( cdb, data_in_len=24)
        return data[3:].decode("ascii", errors="replace").strip("\x00").strip()
    except SCSIError as e:
        if e.sense_key == 0x05:  # Illegal Request = empty slot
            return None
        raise


def film_aspect(t, slot):
    """ASPECT_RATIO (DFRCMD sub 5) -- read film aspect ratio from a slot.

    Like FILM_NAME, this places the slot number in CDB byte 3 (not a
    data-size MSB).  Returns the (width, height) aspect components
    stored in the film table header (same values as FLM bytes 26-27).

    Returns None for empty slots (indicated by Illegal Request sense key).
    """
    cdb = bytes([OP_DFRCMD, 0, SUB_ASPECT_RATIO, slot, 2, 0])
    try:
        data = t.execute( cdb, data_in_len=2)
        return (data[0], data[1])
    except SCSIError as e:
        if e.sense_key == 0x05:  # Illegal Request = empty slot
            return None
        raise


def reset_to_default(t):
    """RESET_TO_DFLT (DFRCMD sub 7) -- reset the device to machine defaults.

    Clears any existing errors and puts the Digital Palette in a
    machine-default state (per the original Polaroid SDK).  The original
    toolkit calls this at the start of DP_Initialize before re-uploading
    film tables from disk.

    Returns:
        None.
    """
    t.execute( bytes([OP_DFRCMD, 0, SUB_RESET_TO_DFLT, 0, 0, 0]))


def upload_film_table(t, slot, encrypted_data):
    """Upload an encrypted .FLM film table to a device slot.

    The payload is: [slot_byte] + [encrypted_FLM_data (15,639 bytes)].
    Total payload: 15,640 bytes.  The device firmware decrypts the data
    internally and stores it in the specified slot in flash memory.

    Uses a 30-second timeout because the flash write can be slow.

    Args:
        t: SCSI Transport.
        slot: Target slot number (0-19).
        encrypted_data: Raw FLM file contents (must be exactly 15,639 bytes).
    """
    if len(encrypted_data) != 15639:
        raise ValueError(
            f"FLM data must be 15,639 bytes, got {len(encrypted_data)}"
        )
    # Payload: slot byte prepended to the encrypted FLM data
    payload = bytes([slot]) + encrypted_data
    size = len(payload)  # 15,640
    cdb = bytes([
        OP_DFRCMD, 0, SUB_UPLOAD_FILM_TABLE,
        (size >> 8) & 0xFF, size & 0xFF, 0
    ])
    t.execute( cdb, data_out=payload, timeout=30000)
