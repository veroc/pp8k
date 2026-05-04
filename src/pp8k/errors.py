"""Exception hierarchy for the pp8k driver.

All exceptions raised by this package inherit from DeviceError, so callers
can catch the base class for blanket handling or individual subclasses for
specific recovery.

Hierarchy:
    DeviceError
    +-- DeviceNotFoundError   (no PP8K at the given /dev/sgN path)
    +-- DeviceNotReadyError   (device exists but TEST UNIT READY failed)
    +-- DeviceBusyError       (an exposure is already running)
    +-- ExposureAbortedError  (user or system requested abort)
    +-- SCSIError             (low-level SCSI transport or CHECK CONDITION)
        +-- ParameterError    (firmware rejected a command parameter)
        +-- HardwareError     (filter wheel jam, fuse, door open, etc.)
        +-- CalibrationError  (CRT auto-luma cycle failed)
        +-- FilmTableError    (FLM upload rejected: bad data, size, structure)

The four SCSIError subclasses are dispatched by transport._raise_check_condition
based on ASC ranges -- catching them lets callers distinguish "your input was
bad" from "the device has a physical problem" without parsing ASC integers
manually.  The asc and sense_key attributes are still populated for cases
where finer-grained handling is needed (e.g. distinguishing 0x254E green-CBAL
from 0x254A red-luminance, both ParameterError).
"""


class DeviceError(Exception):
    """Base for all pp8k device errors."""


class DeviceNotFoundError(DeviceError):
    """No ProPalette 8000 was found at the specified SCSI device path.

    Raised when INQUIRY returns an identification string other than
    "DP2SCSI", which is the signature all Digital Palette devices use.
    """


class DeviceNotReadyError(DeviceError):
    """Device exists on the SCSI bus but is not ready.

    Typically means the device is still powering up, calibrating,
    or has a mechanical problem (film door open, no film loaded).
    """


class DeviceBusyError(DeviceError):
    """An exposure is already in progress on this device.

    The PP8K can only process one exposure at a time. Wait for the
    current exposure to complete or abort it before starting a new one.
    """


class ExposureAbortedError(DeviceError):
    """The exposure was cleanly aborted.

    Raised when the abort event is set during an exposure. The driver
    sends STOP PRINT and TERMINATE EXPOSURE to the device before raising.
    """


class SCSIError(DeviceError):
    """Low-level SCSI transport or protocol error.

    Wraps CHECK CONDITION responses from the device, host adapter errors,
    and driver-level transport failures. The sense_key and asc fields
    carry the SCSI sense data when available.

    Attributes:
        sense_key: SCSI sense key (e.g. 0x02=Not Ready, 0x05=Illegal Request).
                   None if the error is not a CHECK CONDITION.
        asc: Additional Sense Code. Device-specific error detail.
             None if not available.
    """

    def __init__(self, msg, sense_key=None, asc=None):
        super().__init__(msg)
        self.sense_key = sense_key
        self.asc = asc


class ParameterError(SCSIError):
    """Firmware rejected a command parameter as out of range or invalid.

    Covers ASC 0x2500-0x250D (command protocol errors) and 0x2540-0x255F
    (MODE SELECT parameter errors).  Most well-formed cases are caught
    client-side before the SCSI roundtrip; reaching this exception
    typically means a bound that pp8k doesn't yet validate, a
    firmware-quirk rejection, or a parameter mismatch (e.g. film number
    incompatible with the attached camera back).
    """


class HardwareError(SCSIError):
    """Device reported a physical or operational hardware fault.

    Covers ASC 0x2400-0x241F (diagnostics, memory, video, filter wheel,
    fuse, door, shutter, daughter board) and 0x2560-0x2572 (frame buffer
    system errors).  Recovery typically requires user intervention --
    closing the film door, replacing a fuse, or power-cycling the unit.
    """


class CalibrationError(SCSIError):
    """CRT auto-luma calibration cycle failed.

    Covers ASC 0x2420-0x2428.  Often transient -- retrying a fresh
    START_EXPOSURE may succeed.  Persistent calibration failures
    indicate CRT or video subsystem degradation.
    """


class FilmTableError(SCSIError):
    """Device rejected an uploaded FLM film table.

    Covers ASC 0x255A-0x255C, 0x2575-0x2576, and 0x2580-0x2588.
    Typical causes: structurally invalid FLM (missing pixel tables,
    wrong table count, ordering errors), bad file size, or a film-type
    lock mismatch with the camera back currently attached.
    """
