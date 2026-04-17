"""Exposure orchestration for the ProPalette 8000.

Runs the full exposure workflow from MODE SELECT through TERMINATE_EXPOSURE.
Designed to be called in a blocking fashion -- the caller can run it in a
background thread if needed.

Exposure workflow:
    1. MODE SELECT     -- configure film slot, resolution, servo mode
    2. SET_COLOR_TAB   -- load identity gamma LUTs (3 channels)
    3. START_EXPOSURE   -- trigger CRT calibration cycle
    4. Wait 15-25s     -- poll CURRENT_STATUS until calibration completes
    5. Send scanlines  -- 50-line bursts per channel, paced by buffer status
    6. TERMINATE_EXPOSURE -- finalize, advance film

For B&W film tables, only one color channel is sent (determined by the
FLM header's filter field).  For color, all three channels are sent
sequentially (RED, GREEN, BLUE) with a pause between passes for the
filter wheel to rotate.

Buffer management:
    The PP8K has a ~4 MB internal buffer.  Scanlines are sent in bursts
    of 50 lines.  Between bursts, the driver polls CURRENT_STATUS to
    check free buffer space.  If free space drops below 500 KB, the
    driver waits for the buffer to drain before sending more.  This
    prevents buffer overflow while maintaining throughput.

    Measured throughput: ~340 KB/s (~85 lines/s) over PCMCIA PIO SCSI.
    Expected exposure times: ~65s for 4K B&W, ~145s for 4K color.
"""

import threading
import time

from .constants import BLUE, COLOR_NAMES, GREEN, RED
from .errors import ExposureAbortedError, SCSIError
from .models import ExposureProgress


# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

# Number of scanlines to send in each burst before checking buffer state.
# 50 lines * ~4 KB/line = ~200 KB per burst -- well within the buffer.
BURST_SIZE = 50

# Minimum free buffer space (KB) before sending the next burst.
# Below this threshold, the driver waits for the buffer to drain.
MIN_FREE_KB = 500

# Maximum seconds to wait for CRT calibration after START_EXPOSURE.
CALIBRATION_WAIT_S = 30

# Minimum seconds to wait before checking if calibration is complete.
# The CRT needs at least this long to stabilize.
CALIBRATION_MIN_S = 15


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _check_abort(abort, device):
    """Check if the caller requested an abort, and clean up if so.

    Sends STOP PRINT and TERMINATE EXPOSURE to the device before raising,
    ensuring the device returns to a clean state.
    """
    if abort is not None and abort.is_set():
        try:
            device.stop_print()
            time.sleep(0.5)
            device.terminate_exposure()
        except (SCSIError, OSError):
            pass  # Best effort -- device may already be stopped
        raise ExposureAbortedError("Exposure aborted by user")


# ---------------------------------------------------------------------------
# Main exposure function
# ---------------------------------------------------------------------------

def run_exposure(
    device,
    scanlines,
    film_slot,
    bw_channel,
    on_progress=None,
    abort=None,
):
    """Run a complete exposure workflow (blocking).

    This function blocks until the exposure is complete, aborted, or
    fails with an error.  Progress updates are delivered via the
    on_progress callback.

    Args:
        device: Connected PP8K device (real or mock).
        scanlines: (red_lines, green_lines, blue_lines) -- each a list
                   of bytes objects, one per scanline.
        film_slot: Device film slot to use (set via MODE SELECT).
        bw_channel: None for color (3 passes), or RED/GREEN/BLUE for
                    B&W (single pass on the specified channel).
        on_progress: Optional callback, called with ExposureProgress
                     after each significant state change.
        abort: Optional threading.Event.  Set it from another thread
               to request a clean abort.
    """
    red_lines, green_lines, blue_lines = scanlines
    height = len(red_lines)
    width = len(red_lines[0])

    # Determine which channels to send
    if bw_channel is not None:
        all_lines = {RED: red_lines, GREEN: green_lines, BLUE: blue_lines}
        channels = [(bw_channel, all_lines[bw_channel])]
    else:
        channels = [(RED, red_lines), (GREEN, green_lines), (BLUE, blue_lines)]

    total_lines = height * len(channels)
    lines_sent = 0
    exposure_start = time.monotonic()

    def _emit(
        phase,
        channel="",
        error=None,
        buffer_free_kb=0,
    ):
        """Build and emit a progress update."""
        if on_progress is None:
            return
        elapsed = time.monotonic() - exposure_start
        rate = lines_sent / elapsed if elapsed > 0 else 0
        remaining = total_lines - lines_sent
        eta = remaining / rate if rate > 0 else 0
        on_progress(ExposureProgress(
            phase=phase,
            channel=channel,
            lines_sent=lines_sent,
            lines_total=total_lines,
            buffer_free_kb=buffer_free_kb,
            elapsed_seconds=round(elapsed, 1),
            eta_seconds=round(eta, 1),
            error=error,
        ))

    try:
        # --- Step 1: MODE SELECT ---
        _emit("setup")
        device.mode_select(film=film_slot, hres=width, vres=height)
        _check_abort(abort, device)

        # --- Step 2: SET_COLOR_TAB (identity LUT for all channels) ---
        # The film table's built-in LUT curves handle the tone mapping.
        # We send identity (0-255) gamma tables so the per-exposure
        # gamma correction is a no-op.
        identity_lut = bytes(range(256))
        for ch in [RED, GREEN, BLUE]:
            device.set_color_tab(ch, identity_lut)
        _check_abort(abort, device)

        # --- Step 3: START_EXPOSURE ---
        _emit("calibrating")
        device.start_exposure()

        # --- Step 4: Wait for CRT calibration ---
        # The device runs an automatic calibration cycle after
        # START_EXPOSURE.  We poll CURRENT_STATUS until the
        # exposure_state field transitions from 0 to non-zero,
        # indicating the CRT is calibrated and ready for scanlines.
        for i in range(CALIBRATION_WAIT_S):
            time.sleep(1)
            _check_abort(abort, device)

            try:
                st = device.current_status()
                _emit("calibrating", buffer_free_kb=st.buffer_free_kb)

                # Only check for completion after minimum wait
                if i >= CALIBRATION_MIN_S and st.exposure_state != 0:
                    time.sleep(3)  # Extra settle time
                    break
            except SCSIError:
                pass  # Device may be busy during calibration

        _check_abort(abort, device)

        # --- Step 5: Send scanlines ---
        for color, lines in channels:
            color_name = COLOR_NAMES[color]
            y = 0

            while y < height:
                _check_abort(abort, device)

                # Check buffer before each burst
                st = device.current_status()

                if st.buffer_free_kb < MIN_FREE_KB:
                    # Buffer is getting full -- wait for it to drain
                    _emit("sending", channel=color_name,
                          buffer_free_kb=st.buffer_free_kb)
                    while st.buffer_free_kb < MIN_FREE_KB + 200:
                        time.sleep(1)
                        _check_abort(abort, device)
                        st = device.current_status()
                    continue  # Re-check after wait

                # Send a burst of scanlines
                burst_end = min(y + BURST_SIZE, height)
                for line_y in range(y, burst_end):
                    device.print_line(line_y, color, lines[line_y])
                    lines_sent += 1
                y = burst_end

                _emit("sending", channel=color_name,
                      buffer_free_kb=st.buffer_free_kb)

            # Wait between color passes for the filter wheel to rotate.
            # The device needs time to switch from one color filter to
            # the next and drain the scanline buffer.
            if len(channels) > 1 and color < BLUE:
                _emit("sending", channel=f"{color_name}->next")
                time.sleep(5)
                for _ in range(30):
                    _check_abort(abort, device)
                    try:
                        st = device.current_status()
                        if st.buffer_free_kb > 1000:
                            break
                    except SCSIError:
                        pass
                    time.sleep(1)

        # --- Step 6: TERMINATE_EXPOSURE ---
        _emit("finishing")
        device.terminate_exposure()

        # Brief pause for the device to finish writing and advance film
        time.sleep(2)
        _emit("complete")

    except ExposureAbortedError:
        _emit("aborted")

    except (SCSIError, OSError) as e:
        # Try to abort cleanly on error
        try:
            device.stop_print()
            time.sleep(0.5)
            device.terminate_exposure()
        except (SCSIError, OSError):
            pass
        _emit("error", error=str(e))

    except Exception as e:
        try:
            device.stop_print()
        except (SCSIError, OSError):
            pass
        _emit("error", error=str(e))
        raise
