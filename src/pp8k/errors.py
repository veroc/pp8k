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
