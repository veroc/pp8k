"""SG_IO transport layer for Linux SCSI passthrough.

This is the lowest layer of the driver -- it sends raw SCSI Command
Descriptor Blocks (CDBs) to the device via the Linux SG_IO ioctl
interface and returns response data.

The SG_IO interface is provided by the Linux SCSI Generic (sg) driver.
A device at /dev/sgN is opened as a regular file descriptor, and SCSI
commands are sent via ioctl(fd, SG_IO, &hdr) where hdr is an
sg_io_hdr_t structure describing the command, data buffers, and timeout.

No business logic lives here -- just the ioctl plumbing.  Higher layers
(commands.py) build the CDBs and interpret responses.

References:
    - Linux SCSI Generic HOWTO: https://sg.danny.cz/sg/
    - sg_io_hdr_t: /usr/include/scsi/sg.h
"""

import ctypes
import fcntl

from .errors import SCSIError
from .constants import DEVICE_ASC_MESSAGES, SENSE_KEYS


# ---------------------------------------------------------------------------
# SG_IO ioctl constants
# ---------------------------------------------------------------------------

# The ioctl request code for SG_IO (SCSI Generic I/O).
# This is architecture-independent on Linux.
SG_IO = 0x2285

# Data transfer directions for the sg_io_hdr_t.dxfer_direction field.
SG_DXFER_NONE = -1       # No data transfer (e.g. TEST UNIT READY)
SG_DXFER_TO_DEV = -2     # Write data to device (e.g. MODE SELECT, PRINT)
SG_DXFER_FROM_DEV = -3   # Read data from device (e.g. INQUIRY, MODE SENSE)


# ---------------------------------------------------------------------------
# SCSI opcodes used by the ProPalette 8000
#
# The PP8K implements a mix of standard SCSI-2 commands and vendor-specific
# commands.  DFRCMD (0x0C) is the vendor command that handles most
# device-specific operations (film table management, exposure control,
# status queries).
# ---------------------------------------------------------------------------

OP_TEST_UNIT_READY = 0x00   # Standard: check if device is ready
OP_REQUEST_SENSE = 0x03     # Standard: read sense data after error
OP_PRINT = 0x0A             # Vendor: send one scanline of image data
OP_DFRCMD = 0x0C            # Vendor: Digital Film Recorder CoMmanD (multi-purpose)
OP_INQUIRY = 0x12           # Standard: device identification
OP_MODE_SELECT = 0x15       # Standard: set device parameters
OP_MODE_SENSE = 0x1A        # Standard: read device parameters
OP_STOP_PRINT = 0x1B        # Vendor: abort exposure in progress


# ---------------------------------------------------------------------------
# DFRCMD subcommands
#
# The DFRCMD opcode (0x0C) uses byte 2 of the CDB as a subcommand selector.
# Each subcommand accesses a different device function.
# ---------------------------------------------------------------------------

SUB_START_EXPOSURE = 0       # Begin exposure (triggers CRT calibration)
SUB_SET_COLOR_TAB = 1        # Load 256-byte gamma LUT for one channel
SUB_GET_COLOR_TAB = 2        # Read back gamma LUT for one channel
SUB_TERMINATE_EXPOSURE = 3   # End exposure (advance film, close shutter)
SUB_FILM_NAME = 4            # Read film table name from a slot
SUB_ASPECT_RATIO = 5         # Read film aspect ratio from a slot (2 bytes)
SUB_CURRENT_STATUS = 6       # Read buffer/exposure state (7 bytes)
SUB_RESET_TO_DFLT = 7        # Reset device to machine-default state
SUB_UPLOAD_FILM_TABLE = 10   # Upload encrypted FLM data to a slot
SUB_INQUIRY_BLOCK = 21       # Query block transfer mode (fw >= 564)


# ---------------------------------------------------------------------------
# sg_io_hdr_t structure
#
# This ctypes Structure mirrors the Linux sg_io_hdr_t from <scsi/sg.h>.
# It describes a single SCSI command to be executed via ioctl.
#
# Key fields:
#   interface_id:     Must be ord('S') for SG_IO.
#   dxfer_direction:  One of SG_DXFER_NONE/TO_DEV/FROM_DEV.
#   cmd_len:          Length of the CDB in bytes (6 for the PP8K).
#   dxfer_len:        Size of the data buffer.
#   dxferp:           Pointer to the data buffer.
#   cmdp:             Pointer to the CDB.
#   sbp:              Pointer to sense buffer (for error reporting).
#   timeout:          Command timeout in milliseconds.
#   status:           SCSI status byte (0x00=GOOD, 0x02=CHECK CONDITION).
#   host_status:      Host adapter status (0=OK).
#   driver_status:    Kernel driver status (0x08=DRIVER_SENSE is normal).
#   resid:            Residual byte count (dxfer_len - actual transferred).
# ---------------------------------------------------------------------------

class SgIoHdr(ctypes.Structure):
    """Linux sg_io_hdr_t structure for SCSI passthrough."""

    _fields_ = [
        ("interface_id", ctypes.c_int),
        ("dxfer_direction", ctypes.c_int),
        ("cmd_len", ctypes.c_ubyte),
        ("mx_sb_len", ctypes.c_ubyte),
        ("iovec_count", ctypes.c_ushort),
        ("dxfer_len", ctypes.c_uint),
        ("dxferp", ctypes.c_void_p),
        ("cmdp", ctypes.c_void_p),
        ("sbp", ctypes.c_void_p),
        ("timeout", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("pack_id", ctypes.c_int),
        ("usr_ptr", ctypes.c_void_p),
        ("status", ctypes.c_ubyte),
        ("masked_status", ctypes.c_ubyte),
        ("msg_status", ctypes.c_ubyte),
        ("sb_len_wr", ctypes.c_ubyte),
        ("host_status", ctypes.c_ushort),
        ("driver_status", ctypes.c_ushort),
        ("resid", ctypes.c_int),
        ("duration", ctypes.c_uint),
        ("info", ctypes.c_uint),
    ]


# ---------------------------------------------------------------------------
# Transport function
# ---------------------------------------------------------------------------

def sg_io(
    fd,
    cdb,
    data_out=None,
    data_in_len=0,
    timeout=20000,
):
    """Send a SCSI command via the Linux SG_IO ioctl interface.

    This is the single point of contact between the driver and the kernel's
    SCSI layer.  Every SCSI command the PP8K understands flows through here.

    Args:
        fd: Open file descriptor for /dev/sgN.
        cdb: Command Descriptor Block -- the raw SCSI command bytes.
             All PP8K commands use 6-byte CDBs.
        data_out: Payload bytes to send TO the device (for write commands
                  like MODE SELECT, PRINT, DFRCMD uploads).  None for
                  commands that don't send data.
        data_in_len: Number of bytes to read FROM the device (for read
                     commands like INQUIRY, MODE SENSE, CURRENT_STATUS).
                     0 for commands that don't return data.
        timeout: Command timeout in milliseconds.  Default 20s is enough
                 for most commands; START_EXPOSURE needs 60s because the
                 device runs a CRT calibration cycle.

    Returns:
        Response bytes from the device (length = data_in_len - residual).
        Empty bytes for write commands and no-data commands.

    Raises:
        SCSIError: On CHECK CONDITION (with decoded sense data),
                   host adapter errors, or driver-level failures.
    """
    # Prepare CDB buffer
    cdb_buf = (ctypes.c_ubyte * len(cdb)).from_buffer_copy(cdb)

    # Sense buffer: 32 bytes is enough for the PP8K's sense format
    sense_buf = (ctypes.c_ubyte * 32)()

    # Set up data transfer direction and buffer
    if data_out is not None:
        # Write to device (MODE SELECT, PRINT, upload, etc.)
        data_arr = (ctypes.c_ubyte * len(data_out)).from_buffer_copy(data_out)
        direction = SG_DXFER_TO_DEV
        dxfer_len = len(data_out)
    elif data_in_len > 0:
        # Read from device (INQUIRY, MODE SENSE, etc.)
        data_arr = (ctypes.c_ubyte * data_in_len)()
        direction = SG_DXFER_FROM_DEV
        dxfer_len = data_in_len
    else:
        # No data transfer (TEST UNIT READY, START_EXPOSURE, etc.)
        data_arr = None
        direction = SG_DXFER_NONE
        dxfer_len = 0

    # Build the sg_io_hdr_t structure
    hdr = SgIoHdr()
    hdr.interface_id = ord("S")    # Required magic value
    hdr.dxfer_direction = direction
    hdr.cmd_len = len(cdb)
    hdr.mx_sb_len = ctypes.sizeof(sense_buf)
    hdr.dxfer_len = dxfer_len
    if data_arr is not None:
        hdr.dxferp = ctypes.addressof(data_arr)
    hdr.cmdp = ctypes.addressof(cdb_buf)
    hdr.sbp = ctypes.addressof(sense_buf)
    hdr.timeout = timeout

    # Execute the SCSI command
    fcntl.ioctl(fd, SG_IO, hdr)

    # Check for host adapter errors (bus reset, timeout, etc.)
    if hdr.host_status != 0:
        raise SCSIError(f"Host adapter error: 0x{hdr.host_status:04x}")

    # Check for driver-level errors.  0x08 (DRIVER_SENSE) is expected
    # alongside CHECK CONDITION -- it just means sense data is available.
    if hdr.driver_status != 0 and hdr.driver_status != 0x08:
        raise SCSIError(f"Driver error: 0x{hdr.driver_status:04x}")

    # Check SCSI status byte
    if hdr.status != 0:
        # Decode sense data from the CHECK CONDITION response
        sense = bytes(sense_buf[: hdr.sb_len_wr]) if hdr.sb_len_wr > 0 else b""
        sense_key = sense[2] & 0x0F if len(sense) > 2 else 0xFF
        asc = (sense[8] << 8 | sense[9]) if len(sense) > 9 else 0
        key_name = SENSE_KEYS.get(sense_key, f"Unknown(0x{sense_key:02x})")
        asc_msg = DEVICE_ASC_MESSAGES.get(asc)
        detail = f": {asc_msg}" if asc_msg else ""
        raise SCSIError(
            f"CHECK CONDITION: {key_name}{detail} "
            f"(ASC=0x{asc:04x}, raw={sense.hex()})",
            sense_key=sense_key,
            asc=asc,
        )

    # Return response data for read commands
    if data_in_len > 0:
        actual = dxfer_len - hdr.resid
        return bytes(data_arr[:actual])
    return b""
