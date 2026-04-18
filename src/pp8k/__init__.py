"""pp8k -- Python driver for the Polaroid ProPalette 8000 film recorder.

Quick start:
    import pp8k

    flm = pp8k.load_flm("PLUSXPAN.FLM")
    device = pp8k.open("/dev/sg2")
    device.expose("photo.tiff", flm=flm)
    device.close()

The public API is intentionally small:
    pp8k.open(path)       -- connect to a real device
    pp8k.mock()           -- get a mock device for testing
    pp8k.load_flm(path)   -- parse a .FLM film table file
    Device.expose(...)    -- run a full exposure
    Device.info           -- device identification
    Device.mode           -- current device configuration
    Device.close()        -- disconnect
"""

import threading
from pathlib import Path

from .constants import (
    BW_FILTER_TO_CHANNEL,
    FRAME_DIMENSIONS,
    RED,
    GREEN,
    BLUE,
    SCRATCH_SLOT,
)
from .errors import (
    DeviceBusyError,
    DeviceError,
    DeviceNotFoundError,
    DeviceNotReadyError,
    ExposureAbortedError,
    SCSIError,
)
from .exposure import run_exposure
from .flm import FilmTable, LutChannel, LutSet, load_flm, save_flm, serialize_flm
from .imaging import get_frame_dimensions, image_to_scanlines
from .mock import MockDevice
from .models import DeviceInfo, ExposureProgress, ModeState
from .scsi import ScsiDevice


__all__ = [
    # Top-level functions
    "open",
    "mock",
    "load_flm",
    "save_flm",
    "serialize_flm",
    # Classes
    "Device",
    "FilmTable",
    "LutSet",
    "LutChannel",
    # Models
    "DeviceInfo",
    "ModeState",
    "ExposureProgress",
    # Errors
    "DeviceError",
    "DeviceNotFoundError",
    "DeviceNotReadyError",
    "DeviceBusyError",
    "ExposureAbortedError",
    "SCSIError",
    # Constants
    "RED",
    "GREEN",
    "BLUE",
]


class Device:
    """High-level interface to a ProPalette 8000 film recorder.

    This is the main object users interact with.  It wraps a low-level
    backend (ScsiDevice or MockDevice) and provides the high-level
    expose() method that handles the full workflow: image conversion,
    film table upload, and SCSI exposure protocol.

    Don't instantiate directly -- use pp8k.open() or pp8k.mock().
    """

    def __init__(self, backend, device_info):
        self._dev = backend
        self._info = device_info

    @property
    def info(self):
        """Device identification (product, firmware, buffer, max resolution)."""
        return self._info

    @property
    def mode(self):
        """Current device configuration (film slot, resolution, exposure params)."""
        return self._dev.mode_sense()

    @property
    def ready(self):
        """True if the device is ready to accept commands."""
        return self._dev.test_unit_ready()

    def film_name(self, slot):
        """Read the film table name from a device slot (0-19)."""
        return self._dev.film_name(slot)

    def film_aspect(self, slot):
        """Read the (width, height) aspect ratio for a device slot.

        Returns None if the slot is empty.
        """
        return self._dev.film_aspect(slot)

    def film_slots(self):
        """Read all 20 film slot names.  None = empty slot."""
        return {i: self._dev.film_name(i) for i in range(20)}

    def film_slots_info(self):
        """Read name and aspect for all 20 slots.

        Returns a list of dicts: [{slot, name, aspect}, ...].  `name` and
        `aspect` are None for empty slots.
        """
        out = []
        for i in range(20):
            name = self._dev.film_name(i)
            aspect = self._dev.film_aspect(i) if name is not None else None
            out.append({"slot": i, "name": name, "aspect": aspect})
        return out

    def reset(self):
        """Reset the device to machine defaults (clears errors, idle state)."""
        self._dev.reset_to_default()

    def install(self, slot, flm):
        """Persist an FLM film table to a device slot (0-19).

        Writes the encrypted FLM bytes to the device's flash memory at
        the specified slot.  The table survives power cycles and can be
        selected for future exposures by slot number without re-uploading.

        Slot 19 is used by expose() as a scratch slot; installing there
        will be overwritten on the next exposure.

        Args:
            slot: Target slot number (0-19).
            flm: Parsed film table (from pp8k.load_flm()).
        """
        if not 0 <= slot <= 19:
            raise ValueError(f"slot must be 0-19, got {slot}")
        self._dev.upload_film_table(slot, flm.encrypted_data)

    def expose(
        self,
        image_path,
        flm,
        resolution="4k",
        transform="fit",
        background="black",
        on_progress=None,
        abort=None,
    ):
        """Run a complete exposure: image conversion, upload, and print.

        This is a blocking call that runs the full exposure workflow.
        For async operation, run it in a background thread and use the
        abort event to cancel.

        The B&W/color mode and filter channel are determined automatically
        from the FLM film table header -- no need to specify them.

        Args:
            image_path: Path to source image (JPEG, PNG, TIFF, etc.).
            flm: Parsed film table (from pp8k.load_flm()).
            resolution: "4k" or "8k" (default "4k").
            transform: "fit" (letterbox, no crop) or "fill" (crop to fill).
            background: "black" or "white" (letterbox bar color for fit mode).
            on_progress: Optional callback receiving ExposureProgress updates.
            abort: Optional threading.Event to request a clean abort.

        Raises:
            ValueError: If resolution/camera type combination is unsupported.
            DeviceError: On SCSI communication failure.
            ExposureAbortedError: If abort was requested.
        """
        # Look up frame dimensions from the FLM's camera type + resolution
        width, height = get_frame_dimensions(flm.camera_type, resolution)

        # Determine B&W mode from the FLM header
        if flm.is_bw:
            bw_channel = BW_FILTER_TO_CHANNEL.get(flm.bw_filter)
        else:
            bw_channel = None

        # Convert the image to scanlines at the target frame dimensions
        scanlines = image_to_scanlines(
            image_path, width, height, transform, background, flm.is_bw,
        )

        # Upload the film table to our scratch slot on the device
        self._dev.upload_film_table(SCRATCH_SLOT, flm.encrypted_data)

        # Run the SCSI exposure workflow
        run_exposure(
            device=self._dev,
            scanlines=scanlines,
            film_slot=SCRATCH_SLOT,
            bw_channel=bw_channel,
            on_progress=on_progress,
            abort=abort,
        )

    def close(self):
        """Close the device connection and release resources."""
        self._dev.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __repr__(self):
        return (
            f"Device({self._info.product}, fw={self._info.firmware}, "
            f"buf={self._info.buffer_kb}KB)"
        )


def open(device_path="/dev/sg2"):
    """Connect to a ProPalette 8000 via SCSI.

    Opens the device, sends INQUIRY to verify it's a Digital Palette,
    and checks TEST UNIT READY.

    Args:
        device_path: Path to the SCSI Generic device node (e.g. "/dev/sg2").
                     Requires root or appropriate udev permissions.

    Returns:
        A connected Device instance.

    Raises:
        DeviceNotFoundError: If the device is not a Digital Palette.
        DeviceNotReadyError: If the device is not ready.
        OSError: If the device cannot be opened.
    """
    dev = ScsiDevice(device_path)
    dev.open()

    try:
        info = dev.inquiry()

        if info.identification != "DP2SCSI":
            raise DeviceNotFoundError(
                f"Not a Digital Palette at {device_path} "
                f"(got identification '{info.identification}')"
            )

        if not dev.test_unit_ready():
            raise DeviceNotReadyError(
                f"Device at {device_path} is not ready (TEST UNIT READY failed)"
            )
    except Exception:
        dev.close()
        raise

    return Device(dev, info)


def mock():
    """Create a mock device for development and testing.

    Returns a Device backed by MockDevice, which simulates realistic
    timing and buffer behavior without requiring real hardware.
    """
    dev = MockDevice()
    dev.open()
    info = dev.inquiry()
    return Device(dev, info)
