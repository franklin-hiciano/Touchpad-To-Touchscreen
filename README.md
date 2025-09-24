# Touchpad To Touchscreen

Touchpad → Touchscreen transforms an ordinary laptop touchpad into a simulated touchscreen. By placing your thumb, middle, and pinky fingers on the pad as a steady reference, the software predicts where your index finger would “touch” on the screen and moves the cursor there. It includes a visual grid and pointer indicator for calibration, gesture-based triggers for reliability, and even the option to save trace images of your finger movements for later analysis.

This project was made in a weekend with ChatGPT. It is for experimenters, tinkerers, and anyone curious about alternative input methods — blending human ergonomics with creative software. **Available for Linux.**

![20250924_154754(5)](https://github.com/user-attachments/assets/33969678-afaf-4863-978a-7c70951208f1)
Me using the software.

<img width="1409" height="924" alt="Screenshot_20250924_153321" src="https://github.com/user-attachments/assets/c434553e-4324-4a3c-92a2-10784e04f805" />
The golden dot is where Touchpad To Touchscreen thinks my pointer finger is. The purple pizza is my pointer finger's range of motion. The three blue dots are my thumb, middle finger and pinky. Each circle is the path my fingers trace when I rotate my wrist; in this image it doesn't think my pointer finger is on the purple circle because my middle finger is in the way. My hand generally moves together, so using the positions of my other fingers I can predict where the pointer will be.

## Installation

1. Download and extract the zip file for this repo.
2. Run the following command: `pip install evdev PyQt6 && sudo libinput list-devices | sed -n 's/^Device: //p; s/^Kernel: //p'`
3. Copy the text under 'Touchpad'. For me, it's `/dev/input/event13`

## Usage

1. Run the following command:
```
sudo python ./touchpad2touch_patched4.py /dev/input/event13   --grab   --ref-count 3   --indicator   --show-action-dot   --grid 6   --trigger gesture   --gesture-hold-ms 350   --outer-ellipse-scale 1.000   --pointer-ellipse-ratio 0.950   --pointer-center-shift-gamma 1.2   --pointer-mark-deg -25   --pred-min-ellipse-a-px 700   --pred-min-ellipse-b-px 500   --pred-minM-ellipse-a-px 200   --pred-minM-ellipse-b-px 180   --pointer-mark-slope 1.2   --shots-dir ~/Pictures
```
2. Place your thumb, middle finger, and pinky on the touchpad, in order. Use your pointer finger to tap and swipe. To stop the program, press Ctrl+C in the terminal.

## Options

- `--grab` Exclusively grabs the touchpad so other apps don’t “see” raw fingers while armed.
- `--ref-count 3` Expects thumb + middle + pinky as the reference pose (in that order).
- `--trigger gesture` Uses a **press-and-hold** as the fail-safe trigger.  
  - Tune with `--gesture-hold-ms 350`.
- `--indicator` Draws the **sector** overlay anchored on the reference geometry.
- `--grid 6` Shows a 6×6 overlay grid for calibration; set `0` to disable.
- `--show-action-dot` Tiny dot at predicted pointer.
- `--shots-dir ~/Pictures` Where PNG traces are saved.

## Contact
fhicano5@gmail.com

