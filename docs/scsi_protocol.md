# ProPalette 8000 SCSI Protocol Reference

This document describes the SCSI command protocol used by the Polaroid
ProPalette 8000 (PP8K) film recorder, as implemented by the pp8k driver.

All information was derived from the Polaroid Digital Palette SDK analysis,
real hardware testing, and protocol reverse engineering.  This is an
independent documentation effort, not a copy of any proprietary material.


## Device Overview

The ProPalette 8000 is a SCSI-2 target device that connects to a host
computer via a standard 50-pin SCSI bus.  It exposes photographic film
(35mm, 4x5, 6x7, or 6x8) by drawing images on a high-resolution CRT
through color filters onto the film plane.

Key specifications:
- SCSI identification: `DP2SCSI` (all Digital Palette models)
- Maximum resolution: 8192 x 7020 pixels (6x7 back)
- Internal buffer: 4096 KB
- CRT DAC: 18-bit (max display value 262,144)
- Film table storage: 20 slots in flash memory
- Color: 3-pass exposure through R/G/B filter wheel
- B&W: single-pass exposure through selectable color filter


## SCSI Bus Configuration

- SCSI ID: configurable via device menu, stored in flash, persists across
  power cycles.  Requires device reset after changing.
- The device appears as a SCSI target on the bus.  On Linux, it shows up
  as `/dev/sgN` via the SCSI Generic driver.
- All commands use 6-byte CDBs (Command Descriptor Blocks).


## Command Reference

### Standard SCSI Commands

#### TEST UNIT READY (0x00)

Check if the device is powered on and operational.

```
CDB: [0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
Data: none
Response: GOOD status if ready, CHECK CONDITION if not
```

A CHECK CONDITION response typically means the device is still powering
up, running CRT calibration, or has a mechanical problem.


#### REQUEST SENSE (0x03)

Read error details after a CHECK CONDITION.

```
CDB: [0x03, 0x00, 0x00, 0x00, 0x0A, 0x00]
Data: 10 bytes from device
```

Response format:
- Byte 2 bits 0-3: Sense key (0x02=Not Ready, 0x05=Illegal Request, etc.)
- Byte 2 bit 6: EOM (End of Medium) flag
- Bytes 8-9: Additional Sense Code (ASC), device-specific


#### INQUIRY (0x12)

Identify the device.  Always the first command sent.

```
CDB: [0x12, 0x00, 0x00, 0x00, 0x3F, 0x00]
Data: 63 bytes from device
```

Response format:
| Offset | Length | Description |
|--------|--------|-------------|
| 0      | 1      | Device type |
| 8      | 7      | Identification string (`DP2SCSI`) |
| 16     | 16     | Product name (`ProPalette 8K`) |
| 32     | 4      | Firmware revision (e.g. ` 568`) |
| 40     | 2      | Buffer size in KB (big-endian) |
| 46     | 2      | Max horizontal resolution (big-endian) |
| 50     | 2      | Max vertical resolution (big-endian) |


#### MODE SENSE (0x1A)

Read current device configuration.

```
CDB: [0x1A, 0x00, 0x00, 0x00, 0x3D, 0x00]
Data: 61 bytes from device
```

Response format:
| Offset | Length | Description |
|--------|--------|-------------|
| 4      | 2      | Buffer size in KB (big-endian) |
| 6      | 1      | Active film table slot (0-19) |
| 10     | 2      | Horizontal resolution (big-endian) |
| 17     | 2      | Vertical resolution (big-endian) |
| 22     | 3      | Luminance R, G, B (0-200 each) |
| 26     | 3      | Color balance R, G, B |
| 30     | 3      | Exposure time R, G, B |
| 46     | 4      | Camera back identifier (ASCII) |
| 58     | 2      | Frame counter (big-endian) |

Note: frame counter and camera back state are displayed on the device's
LCD panel.  They may not update in real time during SCSI operation.


#### MODE SELECT (0x15)

Configure the device for an exposure.  Must be sent before START_EXPOSURE.

```
CDB: [0x15, 0x00, 0x00, 0x00, 0x2B, 0x00]
Data: 43 bytes to device
```

Parameter block:
| Offset | Description | Typical value |
|--------|-------------|---------------|
| 3      | Descriptor length | 39 |
| 4      | Film table slot | 0-19 |
| 6-7    | Horizontal resolution (big-endian) | 4096 |
| 10-11  | Line length = HRES (big-endian) | 4096 |
| 13-14  | Vertical resolution (big-endian) | 2730 |
| 18-20  | Luminance R/G/B | 100/100/100 |
| 22-24  | Color balance R/G/B | 3/3/3 |
| 26-28  | Exposure time R/G/B | 100/100/100 |
| 30     | LTDRK (light/dark threshold) | 3 |
| 31-32  | Image height = VRES (big-endian) | 2730 |
| 33     | Servo mode (4 = FULL) | 4 |

The luminance, color balance, and exposure time values can be adjusted
for calibration purposes but should normally be left at defaults for
production exposures where the film table handles tone mapping.


### Vendor Commands

#### DFRCMD (0x0C) -- Digital Film Recorder CoMmanD

Multi-purpose vendor command.  Byte 2 of the CDB selects the subcommand.

##### START_EXPOSURE (sub 0)

Begin an exposure.  Triggers the CRT auto-calibration cycle.

```
CDB: [0x0C, 0x00, 0x00, 0x00, 0x00, 0x00]
Data: none
Timeout: 60 seconds (calibration can take 15-25s)
```

After this command returns successfully, the device runs an automatic
CRT calibration.  Poll CURRENT_STATUS until exposure_state becomes
non-zero before sending scanlines.


##### SET_COLOR_TAB (sub 1)

Load a 256-byte per-exposure gamma lookup table for one channel.

```
CDB: [0x0C, 0x00, 0x01, 0x01, 0x00, channel<<6]
Data: 256 bytes to device
```

The channel is encoded in CDB byte 5: 0x00=RED, 0x40=GREEN, 0x80=BLUE.

This LUT is applied per-exposure on top of the film table's built-in
LUT curves.  An identity table (bytes 0-255) means no additional
correction.  This is separate from the film table LUT stored in the
device's flash memory.


##### TERMINATE_EXPOSURE (sub 3)

Signal that all scanlines have been sent.

```
CDB: [0x0C, 0x00, 0x03, 0x00, 0x00, 0x00]
Data: none
```

The device finishes writing any buffered scanlines, advances the film
frame, and returns to idle state.


##### FILM_NAME (sub 4)

Read the name of a film table loaded in a device slot.

```
CDB: [0x0C, 0x00, 0x04, slot, 0x18, 0x00]
Data: 24 bytes from device
```

Response bytes 3-23 contain the ASCII film name.  Returns Illegal
Request (sense key 0x05) for empty slots.


##### CURRENT_STATUS (sub 6)

Read real-time buffer and exposure status.

```
CDB: [0x0C, 0x00, 0x06, 0x00, 0x07, 0x00]
Data: 7 bytes from device
```

Response format:
| Offset | Length | Description |
|--------|--------|-------------|
| 0      | 2      | Buffer free space in KB (big-endian) |
| 2      | 1      | Exposure state (0=calibrating, non-zero=active) |
| 3      | 2      | Current line being processed (big-endian) |
| 5      | 1      | Active film slot |
| 6      | 1      | Status byte |

Polled continuously during exposure for buffer management.


##### UPLOAD_FILM_TABLE (sub 10)

Upload an encrypted .FLM film table to a device slot.

```
CDB: [0x0C, 0x00, 0x0A, size_MSB, size_LSB, 0x00]
Data: (1 + 15639) bytes to device
Timeout: 30 seconds (flash write)
```

The payload is: one slot byte (0-19) followed by 15,639 bytes of
encrypted FLM data.  The device firmware decrypts the data and stores
it in flash memory at the specified slot.


##### INQUIRY_BLOCK (sub 21)

Query block transfer mode capabilities.  Available on firmware >= 564.

```
CDB: [0x0C, 0x00, 0x15, 0x00, 0x08, 0x00]
Data: 8 bytes from device
```

Returns 4 big-endian uint16 values describing block mode parameters.
Block mode is an alternative transfer method that has not been
implemented in this driver.


#### PRINT (0x0A)

Send one scanline of image data.

```
CDB: [0x0A, 0x00, 0x00, size_MSB, size_LSB, channel<<6]
Data: (2 + HRES) bytes to device
```

Payload format: [line_number (2 bytes big-endian)] + [pixel_data].
Each pixel is one byte (0-255).  The line_number is 0-based.

The channel is encoded in CDB byte 5: 0x00=RED, 0x40=GREEN, 0x80=BLUE.

Scanlines should be sent in bursts of ~50 lines.  Between bursts,
poll CURRENT_STATUS to ensure sufficient buffer space (>= 500 KB free).


#### STOP PRINT (0x1B)

Emergency abort of an exposure in progress.

```
CDB: [0x1B, 0x00, 0x00, 0x00, 0x00, 0x00]
Data: none
```

Best-effort command -- errors are ignored since the device may already
be in an error state.  Follow with TERMINATE_EXPOSURE to fully clean up.


## Exposure Workflow

A complete exposure follows this sequence:

```
1. MODE SELECT        -- set film slot, resolution, servo mode
2. SET_COLOR_TAB x3   -- load identity gamma LUTs (R, G, B)
3. START_EXPOSURE      -- trigger CRT calibration
4. Wait 15-25s        -- poll CURRENT_STATUS until ready
5. Send scanlines     -- PRINT commands in 50-line bursts
   (for color: 3 passes, one per channel, with filter wheel pauses)
   (for B&W: 1 pass on the filter channel specified by the film table)
6. TERMINATE_EXPOSURE  -- finalize, advance film
```

### Buffer Management

The device has a 4096 KB internal buffer.  Scanlines are sent in bursts:

1. Check CURRENT_STATUS for buffer_free_kb
2. If < 500 KB free: wait, re-poll
3. If >= 500 KB free: send up to 50 scanlines
4. Repeat until all scanlines sent

Each scanline at 4K resolution is ~4 KB (4096 pixels + 2-byte header).

### Color Pass Transitions

For color exposures, after completing all lines for one channel, wait
5 seconds for the filter wheel to rotate, then poll until buffer
drains (>1000 KB free) before starting the next channel.

### Timing

Measured on PCMCIA PIO SCSI (Lenovo T60):
- Throughput: ~340 KB/s (~85 lines/s)
- 4K color exposure: ~145 seconds
- 4K B&W exposure: ~65 seconds
- CRT calibration: 15-25 seconds


## Film Table Format

Film tables are stored as encrypted .FLM files (15,639 bytes).  See
`pp8k/flm.py` for the decryption algorithm and detailed format
documentation.

Key facts:
- 10 LUT sets per file (one per resolution tier)
- 3 channels per set (R, G, B), 256 entries per channel, uint16 LE
- Per-channel scale factors convert stored values to display values
- B&W flag and filter channel encoded in the header flags byte
- Camera type determines frame aspect ratio and dimensions
