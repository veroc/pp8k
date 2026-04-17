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

# ---------------------------------------------------------------------------
# Device-specific ASC (Additional Sense Code) error messages
#
# The PP8K firmware returns vendor-specific ASC values in the sense data
# when a command fails.  The ASC is a 16-bit value composed of bytes 8-9
# of the sense response (ASC << 8 | ASCQ in standard SCSI terms).
#
# These codes were identified through protocol analysis and real hardware
# testing.
# ---------------------------------------------------------------------------

DEVICE_ASC_MESSAGES = {
    # General
    0x2000: "No additional information available",

    # Calibration errors (0x2420-0x2428)
    0x2420: "Calibration error",
    0x2421: "Calibration error",
    0x2422: "Calibration error",
    0x2423: "Calibration error",
    0x2424: "Calibration error",
    0x2425: "Calibration error",
    0x2426: "Calibration error",
    0x2427: "Calibration error",
    0x2428: "Calibration error",

    # Diagnostics and hardware (0x2400-0x24FF)
    0x2400: "Diagnostic failure",
    0x2401: "Memory failure",
    0x2402: "Video buffer failure",
    0x2403: "Video data failure",
    0x2404: "General I/O failure",
    0x2405: "Checksum error",
    0x2406: "Power on failure",
    0x2407: "Filter wheel jam",
    0x2408: "Bad filter wheel position",
    0x2409: "No memory for data queue",
    0x240B: "Film previously exposed",
    0x240C: "Bad daughter board configuration",
    0x240D: "Frame buffer memory failure",
    0x240E: "Generic firmware error",
    0x240F: "Unexpected interrupt",
    0x2410: "Camera fuse blown",
    0x2411: "Unknown camera back",
    0x2412: "Camera film door open",
    0x2413: "Shutter failure",
    0x2414: "Camera failure",

    # Command protocol errors (0x2500-0x250D)
    0x2500: "LUN cannot be accessed",
    0x2501: "LUN cannot be accessed",
    0x2502: "LUN cannot be accessed",
    0x2503: "Unsupported function",
    0x2504: "Requested length not in valid range",
    0x2505: "Invalid RGB color",
    0x2506: "Invalid combination of FLAG and LINK bit",
    0x2507: "Print command issued without Start Exposure",
    0x2508: "Print command invalid transfer length",
    0x2509: "Terminate Exposure issued without Start Exposure",
    0x250A: "Invalid requested length for Set Color Table",
    0x250B: "Invalid LUN",
    0x250C: "Statement issued with no termination",
    0x250D: "Command aborted",

    # MODE SELECT parameter errors (0x2540-0x2559)
    0x2540: "Invalid field in parameter list",
    0x2541: "Unsupported function",
    0x2542: "Requested length not in range",
    0x2543: "Requested film number incorrect for attached camera back",
    0x2544: "Requested film number not in range",
    0x2545: "Requested horizontal resolution not in range",
    0x2546: "Requested horizontal offset not in range",
    0x2547: "Requested line length not in range",
    0x2548: "Requested vertical resolution not in range",
    0x254A: "Requested luminant red not in range",
    0x254B: "Requested luminant green not in range",
    0x254C: "Requested luminant blue not in range",
    0x254D: "Requested color balance red not in range",
    0x254E: "Requested color balance green not in range",
    0x254F: "Requested color balance blue not in range",
    0x2551: "Illegal parameter in command descriptor",
    0x2552: "Illegal parameter in command descriptor",
    0x2553: "Illegal parameter in command descriptor",
    0x2554: "Illegal parameter in command descriptor",
    0x2555: "Illegal parameter in command descriptor",
    0x2556: "Requested image enhancement not in range",
    0x2557: "Camera adjust command invalid parameters",
    0x2558: "Bad print command line number",
    0x2559: "Calibration control byte not in range",

    # Film table errors (0x255A-0x255C, 0x2580-0x2585)
    0x255A: "Image queue byte not in range",
    0x255B: "Downloaded film table has bad data",
    0x255C: "Downloaded film table bad size",
    0x255D: "Background color command bad parameter",
    0x255E: "Image brightness out of range",
    0x255F: "Invalid servo mode",
    0x2575: "Film type does not match locked film",
    0x2576: "Flash writer error",
    0x2580: "Film table has bad camera data",
    0x2581: "Wrong number of pixel tables in film table",
    0x2582: "First pixel table is missing in film table",
    0x2583: "Pixel tables are out of order in film table",
    0x2584: "Vertical doubles error in film table",
    0x2585: "Scans error in film table",
    0x2586: "4096 pixel table is missing in film table",
    0x2587: "4097 pixel table is missing in film table",
    0x2588: "8192 pixel table is missing in film table",

    # Frame buffer / exposure errors (0x2560-0x2574)
    0x2560: "Frame buffer system error",
    0x2561: "Frame buffer system error",
    0x2562: "Frame buffer system error",
    0x2563: "Frame buffer system error",
    0x2564: "Frame buffer system error",
    0x2565: "Frame buffer system error",
    0x2566: "Frame buffer system error",
    0x2567: "Frame buffer system error",
    0x2568: "Frame buffer system error",
    0x2569: "Frame buffer system error",
    0x256A: "Frame buffer system error",
    0x256B: "Frame buffer system error",
    0x256C: "Requested min exposure resolution not in range",
    0x256D: "Start Exposure issued with single mode exposure in process",
    0x256E: "Frame buffer system error",
    0x256F: "Frame buffer system error",
    0x2570: "Frame buffer system error",
    0x2571: "Frame buffer system error",
    0x2572: "Frame buffer system error",
    0x2573: "Invalid data in set exposure fix parameters command",
    0x2574: "Invalid data in get failures command",
}
