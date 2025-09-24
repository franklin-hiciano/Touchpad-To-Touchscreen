# Touchpad to Touchscreen

<img width="1409" height="924" alt="Screenshot_20250924_153321" src="https://github.com/user-attachments/assets/c434553e-4324-4a3c-92a2-10784e04f805" />

Touchpad to Touchscreen is a computer program that allows use of the laptop touchpad like a phone touchscreen.



# Installation

1. Place the thumb, middle finger, and pinky on the laptop touchpad.




1. Paste the command below into the terminal.
2. Place your thumb, then middle finger, then pinky on the touchpad, in order.
5. Use your pointer finger to navigate the computer like an iPhone. The mouse cursor will disappear.

```bash
sudo python ./touchpad2touch_patched4.py /dev/input/event13   --grab --ref-count 3 --indicator --show-action-dot --grid 6   --trigger gesture --gesture-hold-ms 350   --outer-ellipse-scale 1.000   --pointer-ellipse-ratio 0.950 --pointer-center-shift-gamma 1.2   --pointer-mark-deg -25 --pred-min-ellipse-a-px 700  --pred-min-ellipse-b-px 500   --pred-minM-ellipse-a-px 200 --pred-minM-ellipse-b-px 180   --pointer-mark-slope 1.2 --indicator --shots-dir ~/Pictures
```

> To stop the program, go back to the terminal where you launched Touchpad to Touchscreen and press Ctrl+C.


## Stop



