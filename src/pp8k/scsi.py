"""Real SCSI device implementation.

Wraps a Linux /dev/sgN file descriptor and delegates every operation to
the pure-function command wrappers in commands.py.  This class manages
the file descriptor lifecycle (open/close) and converts raw dict responses
from commands into typed NamedTuple models.

Usage:
    dev = ScsiDevice("/dev/sg2")
    dev.open()
    info = dev.inquiry()
    dev.close()

Requires root (or appropriate udev rules) for /dev/sgN access.
"""

import os

from . import commands
from .models import BufferStatus, DeviceInfo, ModeState


class ScsiDevice:
    """PP8K device connected via Linux SCSI Generic (SG) passthrough.

    Implements the PP8KDevice protocol for use with real hardware.
    Each method maps to one SCSI command -- see commands.py and
    docs/scsi_protocol.md for the wire-level details.
    """

    def __init__(self, device_path="/dev/sg2"):
        """Initialize with a path to a SCSI Generic device node.

        Args:
            device_path: Path to the sg device (e.g. "/dev/sg2").
                         The device is not opened until open() is called.
        """
        self.device_path = device_path
        self.fd = -1

    def open(self):
        """Open the SCSI device for reading and writing.

        Raises:
            OSError: If the device cannot be opened (permission denied,
                     device not found, etc.).
        """
        self.fd = os.open(self.device_path, os.O_RDWR)

    def close(self):
        """Close the SCSI device file descriptor."""
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def inquiry(self):
        """INQUIRY -- identify the device.  See commands.inquiry()."""
        raw = commands.inquiry(self.fd)
        return DeviceInfo(
            identification=raw["identification"],
            product=raw["product"],
            firmware=raw["firmware"],
            revision=raw["revision"],
            buffer_kb=raw["buffer_kb"],
            hres_max=raw["hres_max"],
            vres_max=raw["vres_max"],
        )

    def test_unit_ready(self):
        """TEST UNIT READY -- check if device is operational."""
        return commands.test_unit_ready(self.fd)

    def request_sense(self):
        """REQUEST SENSE -- read error details."""
        return commands.request_sense(self.fd)

    def mode_sense(self):
        """MODE SENSE -- read current device configuration."""
        raw = commands.mode_sense(self.fd)
        return ModeState(
            buffer_kb=raw["buffer_kb"],
            film_number=raw["film_number"],
            hres=raw["hres"],
            vres=raw["vres"],
            lum_rgb=raw["lum_rgb"],
            cbal_rgb=raw["cbal_rgb"],
            etime_rgb=raw["etime_rgb"],
            camera_back=raw["camera_back"],
            frame_counter=raw["frame_counter"],
        )

    def mode_select(self, film, hres, vres, servo=4):
        """MODE SELECT -- configure device for exposure."""
        commands.mode_select(self.fd, film=film, hres=hres, vres=vres, servo=servo)

    def set_color_tab(self, channel, data):
        """SET_COLOR_TAB -- load a 256-byte gamma LUT for one channel."""
        commands.set_color_tab(self.fd, channel, data)

    def start_exposure(self):
        """START_EXPOSURE -- begin CRT calibration and exposure."""
        commands.start_exposure(self.fd)

    def print_line(self, line_no, color, pixels):
        """PRINT -- send one scanline of image data."""
        commands.print_line(self.fd, line_no, color, pixels)

    def terminate_exposure(self):
        """TERMINATE_EXPOSURE -- finalize the exposure."""
        commands.terminate_exposure(self.fd)

    def stop_print(self):
        """STOP PRINT -- emergency abort."""
        commands.stop_print(self.fd)

    def current_status(self):
        """CURRENT_STATUS -- real-time buffer and exposure state."""
        raw = commands.current_status(self.fd)
        return BufferStatus(
            buffer_free_kb=raw["buffer_free_kb"],
            exposure_state=raw["exposure_state"],
            current_line=raw["current_line"],
            film_slot=raw["film_slot"],
            status=raw["status"],
        )

    def film_name(self, slot):
        """FILM_NAME -- read film table name from a device slot."""
        return commands.film_name(self.fd, slot)

    def upload_film_table(self, slot, encrypted_data):
        """Upload an encrypted .FLM film table to a device slot."""
        commands.upload_film_table(self.fd, slot, encrypted_data)
