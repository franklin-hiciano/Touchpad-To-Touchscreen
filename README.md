# Touchpad → Touchscreen

Turn a laptop touchpad into a “pseudo-touchscreen”. The app watches three fingers (thumb, middle, pinky) to predict where your pointer finger would be on the screen.

![20250924_154754(5)](https://github.com/user-attachments/assets/33969678-afaf-4863-978a-7c70951208f1)

<img width="1409" height="924" alt="Screenshot_20250924_153321" src="https://github.com/user-attachments/assets/c434553e-4324-4a3c-92a2-10784e04f805" />

## Installation

Requires Linux.

Open up a terminal and run:
1. `pip install evdev PyQt6`
2. `sudo libinput list-devices | sed -n 's/^Device: //p; s/^Kernel: //p'`.Copy the text under 'Touchpad'. For me, it's `/dev/input/event13`

## Usage

Run the following command:
```
sudo python ./touchpad2touch_patched4.py /dev/input/event13   --grab   --ref-count 3   --indicator   --show-action-dot   --grid 6   --trigger gesture   --gesture-hold-ms 350   --outer-ellipse-scale 1.000   --pointer-ellipse-ratio 0.950   --pointer-center-shift-gamma 1.2   --pointer-mark-deg -25   --pred-min-ellipse-a-px 700   --pred-min-ellipse-b-px 500   --pred-minM-ellipse-a-px 200   --pred-minM-ellipse-b-px 180   --pointer-mark-slope 1.2   --shots-dir ~/Pictures
```

Place your thumb, middle finger, and pinky on the touchpad, in order.
Use your pointer finger to tap and swipe.

Enjoy!

To stop the program, press Ctrl+C in the terminal.

## Why I created this

For fun! Email me at fhiciano5@gmail.com if you have suggestions.

## Requirements

- Unix with root user
- Python 3.10+ and typical EV input deps (install via `requirements.txt` in this repo).
- Permission to read your touchpad device and write to `/dev/uinput` (root or udev rules).

## Options

- `--grab` Exclusively grabs the touchpad so other apps don’t “see” raw fingers while armed.
- `--ref-count 3` Expects thumb + middle + pinky as the reference pose (in that order).
- `--trigger gesture` Uses a **press-and-hold** as the fail-safe trigger.  
  - Tune with `--gesture-hold-ms 350`.
- `--indicator` Draws the **sector** overlay anchored on the reference geometry.
- `--grid 6` Shows a 6×6 overlay grid for calibration; set `0` to disable.
- `--show-action-dot` Tiny dot at predicted pointer.
- `--shots-dir ~/Pictures` Where PNG traces are saved.

