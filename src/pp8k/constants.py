"""Constants for the ProPalette 8000 driver.

Gathers hardware constants, frame dimensions, camera types, and color
channel mappings in one place.  All values are derived from the PP8K SDK,
SCSI protocol analysis, and verified against real hardware.
"""

# ---------------------------------------------------------------------------
# Color channel indices
#
# The PP8K uses a spinning color filter wheel with three positions.
# During a color exposure, three passes are made (one per channel).
# B&W film tables specify which single channel to use.
# ---------------------------------------------------------------------------

RED = 0
GREEN = 1
BLUE = 2

COLOR_NAMES = {RED: "RED", GREEN: "GREEN", BLUE: "BLUE"}

# ---------------------------------------------------------------------------
# B&W filter mapping
#
# The FLM header encodes which color filter to use for B&W exposures.
# Filter 0 (Clear) is ambiguous -- it may require all three passes.
# Most B&W film tables use filter 1 (Green), which gives the best
# spectral response for silver halide films.
# ---------------------------------------------------------------------------

BW_FILTER_TO_CHANNEL = {
    0: None,   # Clear -- unclear mapping, treat as color (3 passes)
    1: GREEN,  # Green filter -- standard for B&W
    2: RED,    # Red filter
    3: BLUE,   # Blue filter
}

BW_FILTER_NAMES = {
    0: "Clear",
    1: "Green",
    2: "Red",
    3: "Blue",
}

# ---------------------------------------------------------------------------
# Camera types
#
# The camera type determines the film format and therefore the frame
# dimensions.  Stored as a single byte in the FLM header (offset 24).
# ---------------------------------------------------------------------------

CAMERA_TYPES = {
    0: "Pack Film",
    1: "35mm",
    2: "Auto Film",
    3: "4x5",
    4: "6x7",
    5: "6x8",
}

# ---------------------------------------------------------------------------
# Frame dimensions
#
# Maps (camera_type, resolution) to (width, height) in pixels.
# The PP8K CRT has a maximum addressable area of 8192 x 7020 pixels.
# Actual frame dimensions depend on the camera back's film gate size.
#
# These values come from the device's MODE SENSE response and the FLM
# set headers (which encode resolution breakpoints at 1024, 2048, 4032,
# 4096, 4097, and 8192 horizontal pixels).
# ---------------------------------------------------------------------------

FRAME_DIMENSIONS = {
    # 35mm (camera_type=1) -- 3:2 aspect ratio
    (1, "4k"): (4096, 2730),
    (1, "8k"): (8192, 5462),
    # 4x5 (camera_type=3) -- 5:4 aspect ratio
    (3, "4k"): (4096, 3184),
    (3, "8k"): (8192, 6371),
    # 6x7 (camera_type=4) -- 7:6 aspect ratio
    (4, "4k"): (4096, 3510),
    (4, "8k"): (8192, 7020),
    # 6x8 (camera_type=5) -- 4:3 aspect ratio
    (5, "4k"): (4096, 3072),
    (5, "8k"): (8192, 6144),
}

# ---------------------------------------------------------------------------
# Device constants
# ---------------------------------------------------------------------------

# MODE SELECT servo mode: FULL (4) = full CRT deflection calibration
SERVO_FULL = 4

# Internal slot used for film table uploads. Slot 19 is the last of 20
# available slots (0-19) and least likely to conflict with user tables
# loaded via the device's front panel.
SCRATCH_SLOT = 19

# Maximum values from hardware specifications
HARDWARE_MAX_DISPLAY = 262144   # 2^18, CRT DAC limit
HARDWARE_MAX_STORED = 65535     # uint16 max in FLM LUT entries
HARDWARE_MAX_HRES = 8192        # maximum horizontal resolution
HARDWARE_MAX_VRES = 7020        # maximum vertical resolution (6x7 back)
HARDWARE_BUFFER_KB = 4096       # standard device buffer size

# SCSI sense keys relevant to PP8K operation
SENSE_KEYS = {
    0x00: "No Sense",
    0x02: "Not Ready",
    0x04: "Hardware Error",
    0x05: "Illegal Request",
    0x06: "Unit Attention",
    0x0B: "Aborted Command",
}
