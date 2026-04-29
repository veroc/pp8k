"""Real SCSI device implementation.

Holds a Transport and delegates every SCSI operation to the pure
command functions in commands.py.  This class also converts raw dict
responses from commands into typed NamedTuple models.

The transport is opaque -- ScsiDevice doesn't care whether commands
are dispatched via SG_IO ioctls, an s2pexec subprocess, or anything
else.  See pp8k.open() for how a transport gets paired with this class.

Usage:
    from pp8k.transport import SGIOTransport
    dev = ScsiDevice(SGIOTransport("/dev/sg2"))
    dev.open()
    info = dev.inquiry()
    dev.close()

The SG_IO path requires root (or appropriate udev rules) for /dev/sgN
access.  Other transports have their own privilege requirements.
"""

from . import commands
from .models import BufferStatus, DeviceInfo, ModeState


class ScsiDevice:
    """PP8K device connected via a SCSI initiator transport.

    Implements the PP8KDevice protocol for use with real hardware.
    Each method maps to one SCSI command -- see commands.py and
    docs/scsi_protocol.md for the wire-level details.
    """

    def __init__(self, transport):
        """Initialize with a Transport instance.

        Args:
            transport: A Transport (e.g. SGIOTransport, S2pexecTransport).
                       The transport is not opened until open() is called.
        """
        self._t = transport

    def open(self):
        """Open the underlying transport.

        Raises:
            OSError: If the transport cannot be opened (permission
                     denied, device not found, etc.).
        """
        self._t.open()

    def close(self):
        """Close the underlying transport."""
        self._t.close()

    def inquiry(self):
        """INQUIRY -- identify the device.  See commands.inquiry()."""
        raw = commands.inquiry(self._t)
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
        return commands.test_unit_ready(self._t)

    def request_sense(self):
        """REQUEST SENSE -- read error details."""
        return commands.request_sense(self._t)

    def mode_sense(self):
        """MODE SENSE -- read current device configuration."""
        raw = commands.mode_sense(self._t)
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
        commands.mode_select(self._t, film=film, hres=hres, vres=vres, servo=servo)

    def set_color_tab(self, channel, data):
        """SET_COLOR_TAB -- load a 256-byte gamma LUT for one channel."""
        commands.set_color_tab(self._t, channel, data)

    def get_color_tab(self, channel):
        """GET_COLOR_TAB -- read back the 256-byte gamma LUT for one channel."""
        return commands.get_color_tab(self._t, channel)

    def start_exposure(self):
        """START_EXPOSURE -- begin CRT calibration and exposure."""
        commands.start_exposure(self._t)

    def print_line(self, line_no, color, pixels):
        """PRINT -- send one scanline of image data."""
        commands.print_line(self._t, line_no, color, pixels)

    def terminate_exposure(self):
        """TERMINATE_EXPOSURE -- finalize the exposure."""
        commands.terminate_exposure(self._t)

    def stop_print(self):
        """STOP PRINT -- emergency abort."""
        commands.stop_print(self._t)

    def current_status(self):
        """CURRENT_STATUS -- real-time buffer and exposure state."""
        raw = commands.current_status(self._t)
        return BufferStatus(
            buffer_free_kb=raw["buffer_free_kb"],
            exposure_state=raw["exposure_state"],
            current_line=raw["current_line"],
            film_slot=raw["film_slot"],
            status=raw["status"],
        )

    def film_name(self, slot):
        """FILM_NAME -- read film table name from a device slot."""
        return commands.film_name(self._t, slot)

    def film_aspect(self, slot):
        """ASPECT_RATIO -- read (width, height) aspect for a device slot."""
        return commands.film_aspect(self._t, slot)

    def reset_to_default(self):
        """RESET_TO_DFLT -- reset the device to machine-default state."""
        commands.reset_to_default(self._t)

    def upload_film_table(self, slot, encrypted_data):
        """Upload an encrypted .FLM film table to a device slot."""
        commands.upload_film_table(self._t, slot, encrypted_data)
