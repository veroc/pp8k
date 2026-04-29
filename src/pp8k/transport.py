"""SCSI transport layer.

The driver talks to the device through a `Transport` object that
exposes a single method, `execute(cdb, data_out=None, data_in_len=0,
timeout=20000)`, plus `open()` and `close()` for lifecycle.  Any
implementation of that contract can drive the PP8K -- this file ships
with one: `SGIOTransport`, which uses the Linux SCSI Generic (sg)
driver and the SG_IO ioctl.

The transport is the only place that touches the kernel.  Higher layers
(commands.py) build CDBs, call transport.execute(), and interpret the
returned bytes.

A second transport (subprocess-based, talking to scsi2pi's `s2pexec`
for the PiSCSI HAT path) lives in its own module and is loaded only
when needed; this keeps the SG_IO path free of any scsi2pi dependency
on regular Linux machines.

References:
    - Linux SCSI Generic HOWTO: https://sg.danny.cz/sg/
    - sg_io_hdr_t: /usr/include/scsi/sg.h
"""

import ctypes
import fcntl
import os

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
# Shared sense-data parsing
#
# The PP8K returns a 10-byte sense response with a non-standard layout:
# byte 2's low nibble is the sense key, bytes 8-9 are a 16-bit ASC.
# Both transports use this helper so the error mapping stays in one place.
# ---------------------------------------------------------------------------

def _raise_check_condition(sense):
    """Decode PP8K-format sense bytes and raise SCSIError.

    Args:
        sense: Raw REQUEST SENSE response (typically 10 bytes; shorter
               accepted, padded with zeros conceptually).
    """
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


# ---------------------------------------------------------------------------
# Transport contract
# ---------------------------------------------------------------------------

class Transport:
    """Abstract SCSI initiator transport.

    Subclasses send one CDB at a time and return the response bytes,
    or raise SCSIError on CHECK CONDITION / host error.  open() and
    close() bracket the lifecycle; execute() is reentrant within that
    bracket.

    Implementations must support the full execute() signature even if
    a particular SCSI command doesn't need every parameter -- callers
    in commands.py rely on it.
    """

    def open(self):
        """Acquire whatever resources the transport needs to issue commands."""
        raise NotImplementedError

    def close(self):
        """Release transport-owned resources.  Idempotent."""
        raise NotImplementedError

    def execute(self, cdb, data_out=None, data_in_len=0, timeout=20000):
        """Send one SCSI CDB.

        Args:
            cdb: Raw command bytes (6-byte CDBs throughout the PP8K).
            data_out: Bytes to send TO the device, or None for read/no-data
                      commands.
            data_in_len: Bytes to read FROM the device, or 0 for write/no-data
                        commands.
            timeout: Command timeout in milliseconds.

        Returns:
            Response bytes (length up to data_in_len; b'' for non-read commands).

        Raises:
            SCSIError: on CHECK CONDITION, host error, or transport failure.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# SG_IO implementation
# ---------------------------------------------------------------------------

class SGIOTransport(Transport):
    """Transport that drives a Linux SCSI Generic (sg) device.

    Works on any host with a Linux SCSI HBA -- Ubuntu/Debian, Pi OS with
    a USB-SCSI bridge, the T60 + PCMCIA test rig, etc.  No external
    binaries are invoked; everything goes through ioctl().

    Args:
        device_path: Path to the sg device node (e.g. "/dev/sg2").
                     The device is not opened until open() is called.
    """

    def __init__(self, device_path="/dev/sg2"):
        self.device_path = device_path
        self.fd = -1

    def open(self):
        self.fd = os.open(self.device_path, os.O_RDWR)

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def execute(self, cdb, data_out=None, data_in_len=0, timeout=20000):
        return _sg_io_ioctl(self.fd, cdb, data_out, data_in_len, timeout)


def _sg_io_ioctl(fd, cdb, data_out, data_in_len, timeout):
    """Issue one SG_IO ioctl on an open sg file descriptor.

    Module-private; call SGIOTransport.execute() instead.

    Returns:
        Response bytes (length = data_in_len - residual).  Empty bytes
        for write commands and no-data commands.

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
        sense = bytes(sense_buf[: hdr.sb_len_wr]) if hdr.sb_len_wr > 0 else b""
        _raise_check_condition(sense)

    # Return response data for read commands
    if data_in_len > 0:
        actual = dxfer_len - hdr.resid
        return bytes(data_arr[:actual])
    return b""


# ---------------------------------------------------------------------------
# s2pexec subprocess transport
#
# Used on the Raspberry Pi when the PP8K is reached through a PiSCSI HAT
# (or compatible board) driven by scsi2pi.  There is no /dev/sg* in this
# setup -- scsi2pi bit-bangs the SCSI bus via GPIO, and we shell out to
# its `s2pexec` binary to issue one CDB per call.
#
# The subprocess is spawned fresh for every CDB; per-call overhead on a
# Pi 3 is a few milliseconds, fine for the cold-path commands we care
# about first (INQUIRY, MODE SENSE, FILM_NAME, GET_COLOR_TAB, etc.).
# The hot-path PRINT scanline burst loop will need a follow-up
# optimisation (binary-input streaming or libscsi2pi via ctypes).
# ---------------------------------------------------------------------------

import shutil
import subprocess
import tempfile


# REQUEST SENSE CDB used after CHECK CONDITION to drain the device's
# contingent-allegiance state.  Alloc length = 10 bytes (PP8K's full
# sense response).
_REQUEST_SENSE_CDB = bytes([0x03, 0, 0, 0, 10, 0])

# s2pexec's --buffer-size minimum is 65536; we always pass at least that.
# All PP8K read commands return well under 64 KB (largest is GET_COLOR_TAB
# at 256 bytes), so this never bites.
_S2PEXEC_BUFSZ = 65536

# data_out larger than this gets streamed via -f tempfile instead of
# packed into -d hex on the command line.  Keeps argv reasonable for
# upload_film_table (15640 B) and PRINT scanlines (~4 KB+).
_S2PEXEC_HEX_INLINE_LIMIT = 4096


class S2pexecTransport(Transport):
    """Transport that drives a SCSI peripheral via scsi2pi's s2pexec.

    Args:
        scsi_id: Target SCSI ID (0-7).  PP8K defaults to 4.
        board_id: Initiator (host) ID (0-7).  Default 7 matches scsi2pi.
        binary: Name or absolute path of the s2pexec executable.
                Default looks it up via PATH.

    The transport carries no per-connection state -- open() only
    verifies the binary is reachable; close() is a no-op.  Each
    execute() spawns a fresh subprocess.
    """

    def __init__(self, scsi_id, board_id=7, binary="s2pexec"):
        self.scsi_id = scsi_id
        self.board_id = board_id
        self.binary = binary

    def open(self):
        self.binary = self._resolve_binary(self.binary)

    @staticmethod
    def _resolve_binary(binary):
        """Find the scsi2pi binary regardless of PATH quirks.

        Order: absolute path as-given, then PATH lookup, then known
        scsi2pi install locations.  This makes pp8k work under sudo
        even when /opt/scsi2pi/bin is not in secure_path.
        """
        if os.path.isabs(binary):
            if not (os.path.isfile(binary) and os.access(binary, os.X_OK)):
                raise FileNotFoundError(f"{binary} not found or not executable")
            return binary
        found = shutil.which(binary)
        if found:
            return found
        for prefix in ("/opt/scsi2pi/bin", "/usr/local/bin", "/usr/bin"):
            candidate = os.path.join(prefix, binary)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        raise FileNotFoundError(
            f"s2pexec binary {binary!r} not found on PATH or in "
            "/opt/scsi2pi/bin.  Install scsi2pi or pass the absolute path."
        )

    def close(self):
        pass

    def execute(self, cdb, data_out=None, data_in_len=0, timeout=20000):
        with tempfile.TemporaryDirectory(prefix="pp8k_s2p_") as tmp:
            return self._run(tmp, cdb, data_out, data_in_len, timeout)

    def _run(self, tmp, cdb, data_out, data_in_len, timeout):
        argv = [
            self.binary,
            "-i", str(self.scsi_id),
            "-B", str(self.board_id),
            "-c", cdb.hex(),
            "-t", str(max(1, (timeout + 999) // 1000)),  # ms -> s, ceil
            "-L", "off",
        ]

        if data_out is not None:
            if len(data_out) <= _S2PEXEC_HEX_INLINE_LIMIT:
                argv += ["-d", data_out.hex()]
            else:
                in_path = os.path.join(tmp, "in.bin")
                with open(in_path, "wb") as f:
                    f.write(data_out)
                argv += ["-f", in_path]

        out_path = None
        if data_in_len > 0:
            out_path = os.path.join(tmp, "out.bin")
            argv += ["-F", out_path,
                     "-b", str(max(_S2PEXEC_BUFSZ, data_in_len))]

        # subprocess.run timeout is the safety net; s2pexec's own -t is
        # the per-CDB SCSI timeout.  Add a few seconds of slack so the
        # SCSI-layer timeout fires first.
        result = subprocess.run(
            argv,
            capture_output=True,
            timeout=(timeout / 1000.0) + 5,
        )

        if result.returncode == 0:
            if out_path is None:
                return b""
            with open(out_path, "rb") as f:
                return f.read(data_in_len)

        # CHECK CONDITION: drain sense in a follow-up subprocess and
        # raise via the shared helper.
        if (result.returncode == 255
                and b"CHECK CONDITION" in result.stderr):
            sense = self._fetch_sense()
            _raise_check_condition(sense)

        raise SCSIError(
            f"s2pexec failed (exit {result.returncode}): "
            f"{result.stderr.decode('utf-8', errors='replace').strip()}"
        )

    def _fetch_sense(self):
        """Issue REQUEST SENSE and return the raw bytes, or b'' on failure."""
        with tempfile.TemporaryDirectory(prefix="pp8k_sense_") as tmp:
            out_path = os.path.join(tmp, "sense.bin")
            argv = [
                self.binary,
                "-i", str(self.scsi_id),
                "-B", str(self.board_id),
                "-c", _REQUEST_SENSE_CDB.hex(),
                "-F", out_path,
                "-b", str(_S2PEXEC_BUFSZ),
                "-t", "5",
                "-L", "off",
            ]
            try:
                r = subprocess.run(argv, capture_output=True, timeout=10)
            except subprocess.TimeoutExpired:
                return b""
            if r.returncode != 0 or not os.path.exists(out_path):
                return b""
            with open(out_path, "rb") as f:
                return f.read(10)
