"""Mock device for development and testing without hardware.

Simulates realistic timing, buffer drain behavior, and film slot state.
Used for UI development, integration testing, and any situation where
a real PP8K is not available.

The mock tracks buffer fill/drain realistically: each scanline consumes
~4 KB of buffer, and the buffer drains at ~2 MB/s (simulating the CRT
scan rate).  This means exposure pacing logic can be tested faithfully.

Usage:
    dev = MockDevice()
    dev.open()
    info = dev.inquiry()   # returns realistic device info
"""

import time

from .constants import SERVO_FULL
from .models import BufferStatus, DeviceInfo, ModeState


class MockDevice:
    """Simulated PP8K for development without hardware.

    Implements the PP8KDevice protocol with realistic but fast timing
    (0.5ms per scanline vs ~12ms on real hardware).
    """

    def __init__(self):
        self._open = False
        self._film_slots = {
            4: "EKTA100",
            5: "PLUSXPAN",
        }
        self._film_aspects = {
            4: (3, 2),
            5: (3, 2),
        }
        self._color_tabs = {0: bytes(range(256)), 1: bytes(range(256)), 2: bytes(range(256))}
        self._mode = {
            "buffer_kb": 4096,
            "film_number": 4,
            "hres": 4096,
            "vres": 2730,
            "lum_rgb": (100, 100, 100),
            "cbal_rgb": (3, 3, 3),
            "etime_rgb": (100, 100, 100),
            "camera_back": "35mm",
        }
        self._exposure_active = False
        self._buffer_free_kb = 4096
        self._current_line = 0
        self._exposure_state = 0
        self._lifetime_exposures = 0
        self._last_drain = time.monotonic()

    def open(self):
        self._open = True

    def close(self):
        self._open = False

    def inquiry(self):
        return DeviceInfo(
            identification="DP2SCSI",
            product="ProPalette 8K",
            firmware=568,
            revision=" 568",
            buffer_kb=4096,
            hres_max=8192,
            vres_max=6710,
        )

    def test_unit_ready(self):
        return self._open

    def request_sense(self):
        return {"sense_key": 0, "eom": False, "asc": 0, "raw": b"\x00" * 10}

    def mode_sense(self):
        return ModeState(
            buffer_kb=self._mode["buffer_kb"],
            film_number=self._mode["film_number"],
            hres=self._mode["hres"],
            vres=self._mode["vres"],
            lum_rgb=self._mode["lum_rgb"],
            cbal_rgb=self._mode["cbal_rgb"],
            etime_rgb=self._mode["etime_rgb"],
            camera_back=self._mode["camera_back"],
            lifetime_exposures=self._lifetime_exposures,
        )

    def mode_select(self, film, hres, vres, servo=SERVO_FULL):
        self._mode["film_number"] = film
        self._mode["hres"] = hres
        self._mode["vres"] = vres

    def set_color_tab(self, channel, data):
        self._color_tabs[channel] = bytes(data)

    def get_color_tab(self, channel):
        return self._color_tabs.get(channel, bytes(range(256)))

    def start_exposure(self):
        self._exposure_active = True
        self._exposure_state = 1
        self._buffer_free_kb = 4096
        self._current_line = 0
        self._last_drain = time.monotonic()

    def print_line(self, line_no, color, pixels):
        # Simulate ~0.5ms per line (faster than real hardware for dev)
        time.sleep(0.0005)
        self._current_line = line_no
        # Each scanline consumes ~4 KB of buffer
        self._buffer_free_kb = max(0, self._buffer_free_kb - 4)

    def terminate_exposure(self):
        self._exposure_active = False
        self._exposure_state = 0
        self._lifetime_exposures += 1

    def stop_print(self):
        self._exposure_active = False
        self._exposure_state = 0

    def current_status(self):
        # Simulate buffer draining at ~2 MB/s
        now = time.monotonic()
        elapsed = now - self._last_drain
        self._last_drain = now
        drained = int(elapsed * 2000)
        self._buffer_free_kb = min(4096, self._buffer_free_kb + drained)

        return BufferStatus(
            buffer_free_kb=self._buffer_free_kb,
            exposure_state=self._exposure_state,
            current_line=self._current_line,
            film_slot=self._mode["film_number"],
            status=0,
        )

    def film_name(self, slot):
        return self._film_slots.get(slot)

    def film_aspect(self, slot):
        return self._film_aspects.get(slot)

    def reset_to_default(self):
        self._exposure_active = False
        self._exposure_state = 0
        self._buffer_free_kb = 4096
        self._current_line = 0

    def upload_film_table(self, slot, encrypted_data):
        self._film_slots[slot] = f"SLOT{slot}"
        self._film_aspects[slot] = (3, 2)
