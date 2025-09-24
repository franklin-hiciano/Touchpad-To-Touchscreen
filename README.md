# Touchpad → Touchscreen

Turn a laptop touchpad into a “pseudo-touchscreen” you can point with your index finger. The app watches a stable three-finger reference pose (thumb, middle, pinky), predicts where your pointer finger would be, and moves the mouse there. It can also overlay a sector/indicator for the predicted pointer position and record drag paths as images for later analysis.

![20250924_154754(5)](https://github.com/user-attachments/assets/33969678-afaf-4863-978a-7c70951208f1)

<img width="1409" height="924" alt="Screenshot_20250924_153321" src="https://github.com/user-attachments/assets/c434553e-4324-4a3c-92a2-10784e04f805" />

## Features

- **4-finger pointing**: thumb + middle + pinky form a reference; you “aim” with your pointer finger.
- **Sector indicator**: shows a sector (centerline perpendicular to the thumb-pinky line) with a mark near the 2/3 arc, aligned to the predicted pointer position.
- **Grid & action dot**: on-screen grid and tiny action dot so you can debug at a glance.
- **Fail-safe trigger**: “gesture hold” (press-and-hold for a few hundred ms) to arm actions; optional key trigger if you prefer.
- **Trace snapshots**: on trigger release, saves a PNG heat/trace of finger paths to `--shots-dir` for modeling and calibration.
- **Sudo-friendly**: runs against `/dev/input/event*` with `--grab` to ensure exclusive reads (or configure udev to run without sudo).

## Requirements

- Linux with an evdev touchpad (`/dev/input/event*`).
- Python 3.10+ and typical EV input deps (install via `requirements.txt` in this repo).
- Permission to read your touchpad device and write to `/dev/uinput` (root or udev rules).

## Quick start

1) Put **thumb → middle → pinky** on the touchpad to establish the reference pose.  
2) Use your **pointer finger** to “touch” the screen by moving on the pad.  
3) Hold the gesture trigger, move, then release to save a trace image (optional).

Run (adjust your event node and options as needed):

```bash
sudo python ./touchpad2touch_patched4.py /dev/input/event13   --grab   --ref-count 3   --indicator   --show-action-dot   --grid 6   --trigger gesture   --gesture-hold-ms 350   --outer-ellipse-scale 1.000   --pointer-ellipse-ratio 0.950   --pointer-center-shift-gamma 1.2   --pointer-mark-deg -25   --pred-min-ellipse-a-px 700   --pred-min-ellipse-b-px 500   --pred-minM-ellipse-a-px 200   --pred-minM-ellipse-b-px 180   --pointer-mark-slope 1.2   --shots-dir ~/Pictures
```

> To stop the program, focus the terminal that launched it and press **Ctrl+C**.

## Finding the right input device

Use one of these to discover your touchpad:

```bash
ls -l /dev/input/by-id/
grep -iE "touchpad|synaptics|elan|alps" /proc/bus/input/devices -nA3
```

Prefer `/dev/input/by-id/...` over a raw `event13` so it doesn’t change across reboots.

## Permissions (optional: run without sudo)

Most distros make `/dev/input/event*` readable by the `input` group and `/dev/uinput` writable by `uinput` or `input`.

1) Add yourself to the groups and re-login:
```bash
sudo usermod -aG input,uinput $USER
```

2) If needed, add udev rules `/etc/udev/rules.d/99-touchscreen.rules`:
```udev
KERNEL=="event*", GROUP="input", MODE="660"
KERNEL=="uinput", GROUP="input", MODE="660", OPTIONS+="static_node=uinput"
```
Then:
```bash
sudo udevadm control --reload
sudo udevadm trigger
```

## Options

- `--grab` Exclusively grabs the touchpad so other apps don’t “see” raw fingers while armed.
- `--ref-count 3` Expects thumb + middle + pinky as the reference pose (in that order).
- `--trigger gesture` Uses a **press-and-hold** as the fail-safe trigger.  
  - Tune with `--gesture-hold-ms 350`.
- `--indicator` Draws the **sector** overlay anchored on the reference geometry.
- `--grid 6` Shows a 6×6 overlay grid for calibration; set `0` to disable.
- `--show-action-dot` Tiny dot at predicted pointer.
- `--shots-dir ~/Pictures` Where PNG traces are saved.

## Notes on the sector indicator

The sector’s centerline is **perpendicular** to the thumb-pinky baseline; its sharp center lies on that baseline. The pointer mark sits near the **2/3 arc**, which empirically matches index-finger reach. Use `--pointer-mark-deg` and `--pointer-mark-slope` to fine-tune.

## Troubleshooting

- **Nothing happens**: check you’re reading the right device, or drop `--grab`.
- **Wrong predictions**: adjust `--gesture-hold-ms` and ellipse params.
- **Cursor doesn’t move**: check `/dev/uinput` permissions.
- **Overlay missing**: some Wayland/WM setups hide overlays.
- **Key trigger flaky**: gesture mode is more reliable.
