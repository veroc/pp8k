"""Data models for the pp8k driver.

All models are NamedTuples -- immutable, lightweight, zero-dependency
value objects.  They serialize naturally via _asdict() and unpack as
tuples where convenient.
"""

from typing import NamedTuple


class DeviceInfo(NamedTuple):
    """Device identification returned by SCSI INQUIRY.

    The PP8K responds to INQUIRY with a 63-byte block containing product
    identification, firmware version, buffer size, and maximum resolution.
    The identification field is always "DP2SCSI" for Digital Palette devices.
    """
    identification: str   # "DP2SCSI" for all Digital Palette models
    product: str          # e.g. "ProPalette 8K"
    firmware: int         # firmware version as integer (e.g. 568)
    revision: str         # raw 4-char revision string from INQUIRY
    buffer_kb: int        # device buffer size in KB (typically 4096)
    hres_max: int         # maximum horizontal resolution (typically 8192)
    vres_max: int         # maximum vertical resolution (depends on camera back)


class ModeState(NamedTuple):
    """Current device mode returned by SCSI MODE SENSE.

    Reflects the active configuration: which film slot is selected,
    what resolution is set, and the exposure parameters (luminance,
    color balance, exposure time per channel).

    Note: frame_counter and camera_back are read from the device's LCD
    state and may not update in real time during SCSI operation.
    """
    buffer_kb: int                      # buffer size in KB
    film_number: int                    # active film slot (0-19)
    hres: int                           # horizontal resolution
    vres: int                           # vertical resolution
    lum_rgb: tuple                      # luminance per channel (0-200)
    cbal_rgb: tuple                     # color balance per channel
    etime_rgb: tuple                    # exposure time per channel
    camera_back: str                    # camera back identifier (e.g. "35mm")
    frame_counter: int                  # exposure count (LCD-only, may lag)


class BufferStatus(NamedTuple):
    """Real-time buffer and exposure status from DFRCMD CURRENT_STATUS.

    Polled continuously during exposure to manage scanline burst pacing.
    The buffer_free_kb field determines when to send the next burst of
    scanlines -- sending too fast overflows the buffer; too slow wastes
    time.

    The exposure_state field transitions:
        0 = idle / calibrating
        non-zero = ready to receive scanlines
    """
    buffer_free_kb: int   # free buffer space in KB
    exposure_state: int   # 0 = calibrating/idle, non-zero = active
    current_line: int     # last line processed by the device
    film_slot: int        # currently selected film slot
    status: int           # device status byte


class ExposureProgress(NamedTuple):
    """Progress update emitted during an exposure.

    Passed to the on_progress callback at regular intervals.  The phase
    field tracks the exposure lifecycle:

        "setup"        -- MODE SELECT and color table configuration
        "calibrating"  -- waiting for CRT calibration after START_EXPOSURE
        "sending"      -- transmitting scanlines to the device
        "finishing"    -- TERMINATE_EXPOSURE sent, waiting for completion
        "complete"     -- exposure finished successfully
        "error"        -- exposure failed (see error field)
        "aborted"      -- exposure was aborted by user
    """
    phase: str
    channel: str = ""              # "RED", "GREEN", "BLUE", or ""
    lines_sent: int = 0            # total scanlines sent so far
    lines_total: int = 0           # total scanlines to send
    buffer_free_kb: int = 0        # current device buffer free space
    elapsed_seconds: float = 0.0   # wall clock time since exposure start
    eta_seconds: float = 0.0       # estimated time remaining
    error: str = None              # error message if phase == "error"
