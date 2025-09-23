#!/usr/bin/env python3
# Touchpad -> Virtual Touchscreen (Wayland-safe) with sudo-capable overlay & recorder
# - Root/server: grabs touchpad, emits virtual touchscreen, watches hotkey(s) and/or a gesture trigger,
#                streams overlay+recorder data over a Unix socket.
# - User/helper: connects to socket, draws overlay (grid + dots), and saves PNGs.
#
# New: robust triggers
#   --trigger keyboard|gesture|both   (default both)
#   Keyboard: auto-listen to ALL keyboard-like event devices if --hotkey-dev not provided
#   Gesture: start when >= (ref_count+1) fingers held for --gesture-hold-ms, stop when below threshold
#
# Arch deps:  sudo pacman -S python-evdev python-pyqt6
# Run as root (sudo). The helper runs as your normal user so the overlay works on Wayland.

import sys, os, time, argparse, threading, queue, datetime, pathlib, json, socket, subprocess, glob, select, math

from evdev import InputDevice, ecodes, AbsInfo, UInput

try:
    from PyQt6 import QtCore, QtGui, QtWidgets
    QT_OK = True
except Exception:
    QT_OK = False

def clamp01(x): return 0.0 if x < 0 else 1.0 if x > 1 else x

# ----------------- Overlay (helper UI) -----------------

class IndicatorOverlay(QtWidgets.QWidget):
    def __init__(self, size_px=18, fade_ms=120, grid_n=0, show_action_dot=False):
        super().__init__(None)
        self.grid_n = grid_n
        self.show_action_dot = show_action_dot
        self.arc_visible = False
        self.arc_center = QtCore.QPointF(-1000, -1000)
        self.arc_radius = 0.0
        self.ptr_visible = False
        self.ptr_center = QtCore.QPointF(-1000, -1000)
        self.ptr_radius = 0.0
        self.ell_outer = {"visible": False, "cx": 0.0, "cy": 0.0, "a": 0.0, "b": 0.0, "deg": 0.0}
        self.ell_inner = {"visible": False, "cx": 0.0, "cy": 0.0, "a": 0.0, "b": 0.0, "deg": 0.0}
        self.ptr_mark_visible = False
        self.ptr_mark = QtCore.QPointF(-1000, -1000)




        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint |
            QtCore.Qt.WindowType.Tool |
            QtCore.Qt.WindowType.WindowStaysOnTopHint |
            QtCore.Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        screen = QtGui.QGuiApplication.primaryScreen()
        self.setGeometry(screen.geometry())
        wh = self.windowHandle()
        if wh:
            wh.setScreen(screen)
            wh.setFlag(QtCore.Qt.WindowType.WindowTransparentForInput, True)

        r = max(2, size_px // 2)
        self.ref_radius = r
        self.act_radius = int(r * 0.9)

        self.ref_pts = []          # [QPointF, ...]
        self.ref_opacity = 0.0
        self.act_pt = QtCore.QPointF(-1000, -1000)
        self.act_opacity = 0.0

        self.ref_anim = QtCore.QPropertyAnimation(self, b"refOpacity", self)
        self.ref_anim.setDuration(fade_ms)
        self.ref_anim.setStartValue(1.0); self.ref_anim.setEndValue(0.0)

        self.act_anim = QtCore.QPropertyAnimation(self, b"actOpacity", self)
        self.act_anim.setDuration(fade_ms)
        self.act_anim.setStartValue(1.0); self.act_anim.setEndValue(0.0)

    @QtCore.pyqtProperty(float)
    def refOpacity(self): return self.ref_opacity
    @refOpacity.setter
    def refOpacity(self, v): self.ref_opacity = v; self.update()

    @QtCore.pyqtProperty(float)
    def actOpacity(self): return self.act_opacity
    @actOpacity.setter
    def actOpacity(self, v): self.act_opacity = v; self.update()

    def paintEvent(self, e):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        if self.grid_n > 0:
            geo = self.geometry(); w, h = geo.width(), geo.height()
            pen = QtGui.QPen(QtGui.QColor(200, 200, 200, 120)); pen.setWidth(1)
            p.setPen(pen)
            for i in range(1, self.grid_n):
                x = int(i * w / self.grid_n); p.drawLine(x, 0, x, h)
            for j in range(1, self.grid_n):
                y = int(j * h / self.grid_n); p.drawLine(0, y, w, y)

        if self.ref_pts:
            color = QtGui.QColor(25, 132, 255); color.setAlphaF(self.ref_opacity)
            p.setPen(QtCore.Qt.PenStyle.NoPen); p.setBrush(color)
            r = self.ref_radius
            for pt in self.ref_pts:
                p.drawEllipse(QtCore.QRectF(pt.x()-r, pt.y()-r, 2*r, 2*r))

        if self.show_action_dot and self.act_opacity > 0.001:
            color = QtGui.QColor(46, 204, 113); color.setAlphaF(self.act_opacity)
            p.setPen(QtCore.Qt.PenStyle.NoPen); p.setBrush(color)
            r = self.act_radius
            p.drawEllipse(QtCore.QRectF(self.act_pt.x()-r, self.act_pt.y()-r, 2*r, 2*r))

        # full circle for Rule #1 (orange, dashed)
        if self.arc_visible and self.arc_radius > 2.0:
            pen = QtGui.QPen(QtGui.QColor(255, 160, 0, 220))  # orange
            pen.setWidth(2)
            pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            p.setPen(pen); p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            cx, cy, r = self.arc_center.x(), self.arc_center.y(), self.arc_radius
            p.drawEllipse(QtCore.QRectF(cx - r, cy - r, 2*r, 2*r))

        # inner "pointer" circle (magenta, solid)
        if self.ptr_visible and self.ptr_radius > 2.0:
            pen = QtGui.QPen(QtGui.QColor(155, 89, 182, 230))  # amethyst
            pen.setWidth(2)
            p.setPen(pen); p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            cx, cy, r = self.ptr_center.x(), self.ptr_center.y(), self.ptr_radius
            p.drawEllipse(QtCore.QRectF(cx - r, cy - r, 2*r, 2*r))

        # Outer ellipse (orange, dashed)
        eo = self.ell_outer
        if eo["visible"] and eo["a"] > 2 and eo["b"] > 2:
            p.save()
            p.translate(eo["cx"], eo["cy"])
            p.rotate(eo["deg"])
            pen = QtGui.QPen(QtGui.QColor(255,160,0,220))
            pen.setWidth(2); pen.setStyle(QtCore.Qt.PenStyle.DashLine)
            p.setPen(pen); p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            p.drawEllipse(QtCore.QRectF(-eo["a"], -eo["b"], 2*eo["a"], 2*eo["b"]))
            p.restore()

        # Inner ellipse (magenta, solid)
        ei = self.ell_inner
        if ei["visible"] and ei["a"] > 2 and ei["b"] > 2:
            p.save()
            p.translate(ei["cx"], ei["cy"])
            p.rotate(ei["deg"])
            pen = QtGui.QPen(QtGui.QColor(155,89,182,230))
            pen.setWidth(2)
            p.setPen(pen); p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
            p.drawEllipse(QtCore.QRectF(-ei["a"], -ei["b"], 2*ei["a"], 2*ei["b"]))
            p.restore()

        # inner-circle reference point (gold filled dot)
        if self.ptr_mark_visible:
            pen = QtGui.QPen(QtCore.Qt.PenStyle.NoPen)
            brush = QtGui.QBrush(QtGui.QColor(241, 196, 15, 230))  # gold
            p.setPen(pen); p.setBrush(brush)
            r = 5  # marker radius px
            p.drawEllipse(QtCore.QRectF(self.ptr_mark.x()-r, self.ptr_mark.y()-r, 2*r, 2*r))

    def set_pointer_mark(self, x_px: float, y_px: float, visible: bool):
        self.ptr_mark = QtCore.QPointF(float(x_px), float(y_px))
        self.ptr_mark_visible = bool(visible)
        self.update()


    def set_thumb_ellipse(self, cx, cy, a, b, deg, visible=True):
        self.ell_outer.update(cx=float(cx), cy=float(cy), a=float(a), b=float(b), deg=float(deg), visible=bool(visible))
        self.update()

    def set_pointer_ellipse(self, cx, cy, a, b, deg, visible=True):
        self.ell_inner.update(cx=float(cx), cy=float(cy), a=float(a), b=float(b), deg=float(deg), visible=bool(visible))
        self.update()


    def set_pointer_circle(self, cx_px: float, cy_px: float, r_px: float, visible: bool):
        self.ptr_center = QtCore.QPointF(cx_px, cy_px)
        self.ptr_radius = float(r_px)
        self.ptr_visible = bool(visible)
        self.update()


    def set_thumb_arc(self, cx_px: float, cy_px: float, r_px: float, visible: bool):
        self.arc_center = QtCore.QPointF(cx_px, cy_px)
        self.arc_radius = float(r_px)
        self.arc_visible = bool(visible)
        self.update()

    def set_ref_points(self, qpoints, visible):
        self.ref_pts = qpoints
        if visible:
            self.ref_anim.stop(); self.ref_opacity = 1.0; self.update()
        else:
            self.ref_anim.stop(); self.ref_anim.setStartValue(self.ref_opacity or 1.0); self.ref_anim.start()

    def show_act(self, x, y, touching):
        self.act_pt = QtCore.QPointF(x, y)
        if touching:
            self.act_anim.stop(); self.act_opacity = 1.0; self.update()
        else:
            self.act_anim.stop(); self.act_anim.setStartValue(self.act_opacity or 1.0); self.act_anim.start()

# ----------------- Helpers: hotkey + device open -----------------

def parse_keycode(name: str) -> int:
    if hasattr(ecodes, name):
        return getattr(ecodes, name)
    if name in ecodes.ecodes:
        return ecodes.ecodes[name]
    raise SystemExit(f"[error] Unknown key name: {name}")

def list_keyboard_like_event_nodes():
    """Return a list of /dev/input/event* paths that look keyboardish (EV_KEY present)."""
    paths = sorted(glob.glob("/dev/input/event*"))
    kbd = []
    for p in paths:
        try:
            dev = InputDevice(p)
            caps = dev.capabilities()
            if ecodes.EV_KEY in caps:
                kbd.append(p)
            dev.close()
        except Exception:
            pass
    return kbd

def hotkey_multi_loop(dev_paths, key_code, ctrl_q):
    """Listen to multiple keyboard devices at once. Emits ('hk','start'|'stop')."""
    devs = []
    for p in dev_paths:
        try:
            d = InputDevice(p)
            d.set_nonblocking(True)
            devs.append(d)
        except Exception:
            pass
    if not devs:
        ctrl_q.put(("hk_error", "No keyboard-like devices available")); return

    pressed_by_fd = {d.fd: False for d in devs}
    was_any = False

    try:
        while True:
            r, _, _ = select.select([d.fd for d in devs], [], [], 0.1)
            for d in devs:
                if d.fd not in r:
                    continue
                try:
                    for e in d.read():
                        if e.type == ecodes.EV_KEY and e.code == key_code:
                            if e.value == 1:   # press
                                pressed_by_fd[d.fd] = True
                            elif e.value == 0: # release
                                pressed_by_fd[d.fd] = False
                            any_now = any(pressed_by_fd.values())
                            if any_now and not was_any:
                                ctrl_q.put(("hk", "start")); was_any = True
                            elif not any_now and was_any:
                                ctrl_q.put(("hk", "stop")); was_any = False
                except BlockingIOError:
                    pass
                except OSError:
                    pass
    except Exception as e:
        ctrl_q.put(("hk_error", f"Hotkey loop failure: {e}"))

def open_touch_device(path, require_grab=False):
    try:
        dev = InputDevice(path)
    except PermissionError:
        sys.exit(f"[error] No access to {path}.")
    except OSError as e:
        sys.exit(f"[error] Failed to open {path}: {e}")
    try:
        dev.grab()
    except Exception as e:
        if require_grab:
            sys.exit(f"[error] Exclusive grab failed on {path}: {e}")
        else:
            print(f"[warn] grab failed on {path}: {e}", file=sys.stderr)
    return dev

def build_ui_device():
    return UInput({
        ecodes.EV_KEY: [ecodes.BTN_TOUCH],
        ecodes.EV_ABS: [
            (ecodes.ABS_X, AbsInfo(0, 0, 65535, 0, 0, 0)),
            (ecodes.ABS_Y, AbsInfo(0, 0, 65535, 0, 0, 0)),
        ]
    }, name="Virtual Touchscreen (touchpad2touch)")

# ----------------- Socket bridge -----------------

def default_socket_path():
    uid = os.getenv("SUDO_UID")
    uid = int(uid) if uid is not None else os.getuid()
    return f"/tmp/touchpad2touch.{uid}.sock"

class BridgeServer:
    def __init__(self, path):
        self.path = path
        try: os.unlink(self.path)
        except FileNotFoundError: pass
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        os.chmod(self.path, 0o666)
        self.srv.listen(1)
        self.conn = None
        self.lock = threading.Lock()
        threading.Thread(target=self._accept, daemon=True).start()
    def _accept(self):
        try:
            conn, _ = self.srv.accept()
            with self.lock: self.conn = conn
        except Exception:
            pass
    def send(self, obj):
        data = (json.dumps(obj) + "\n").encode("utf-8")
        with self.lock:
            if not self.conn: return
            try: self.conn.sendall(data)
            except Exception: self.conn = None

class BridgeClientReader(QtCore.QObject):
    got_message = QtCore.pyqtSignal(dict)
    def __init__(self, path):
        super().__init__()
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)
        self.sock.setblocking(False)
        self.buf = b""
        self.timer = QtCore.QTimer()
        self.timer.timeout.connect(self._poll)
        self.timer.start(8)
    def _poll(self):
        try:
            chunk = self.sock.recv(65536)
            if not chunk: return
            self.buf += chunk
            while b"\n" in self.buf:
                line, self.buf = self.buf.split(b"\n", 1)
                if line.strip():
                    try: self.got_message.emit(json.loads(line.decode("utf-8")))
                    except Exception: pass
        except BlockingIOError:
            pass
        except Exception:
            pass

# ----------------- Root/server side -----------------

def run_root_server(args):
    out_q = queue.Queue()
    ctrl_q = queue.Queue()

    touch = threading.Thread(target=root_touch_loop, args=(args, out_q, ctrl_q), daemon=True)
    touch.start()

    # Start keyboard trigger (if enabled)
    if args.trigger in ("keyboard", "both"):
        key_code = parse_keycode(args.hotkey)
        if args.hotkey_dev:
            dev_paths = [args.hotkey_dev]
        else:
            dev_paths = list_keyboard_like_event_nodes()
        hk = threading.Thread(target=hotkey_multi_loop, args=(dev_paths, key_code, ctrl_q), daemon=True)
        hk.start()

    bridge = BridgeServer(args.socket_path)

    helper_user = os.getenv("SUDO_USER")
    if helper_user:
        script = os.path.abspath(sys.argv[0])
        helper_cmd = [
            "sudo", "-u", helper_user, "--",
            sys.executable, script,
            "--display-helper",
            "--socket-path", args.socket_path,
            "--shots-dir", args.shots_dir,
            "--grid", str(args.grid),
            "--indicator-size", str(args.indicator_size),
            "--indicator-fade-ms", str(args.indicator_fade_ms),
        ]
        helper_cmd += [
            "--stroke-px", str(args.stroke_px),
            "--pointer-ellipse-ratio", str(args.pointer_ellipse_ratio),
            "--outer-ellipse-scale", str(args.outer_ellipse_scale),
            "--pointer-min-gap-px", str(args.pointer_min_gap_px),
            "--pointer-inner-margin-px", str(args.pointer_inner_margin_px),
            "--pointer-mark-deg", str(args.pointer_mark_deg),
            "--pointer-center-shift-gamma", str(args.pointer_center_shift_gamma),
        ]
        helper_cmd += [
            "--pred-min-ellipse-a-px", str(args.pred_min_ellipse_a_px),
            "--pred-min-ellipse-b-px", str(args.pred_min_ellipse_b_px),
        ]
        helper_cmd += [
            "--pred-minM-ellipse-a-px", str(args.pred_minM_ellipse_a_px),
            "--pred-minM-ellipse-b-px", str(args.pred_minM_ellipse_b_px),
            "--pointer-mark-slope", str(args.pointer_mark_slope),
        ]








        if args.indicator: helper_cmd.append("--indicator")
        if args.show_action_dot: helper_cmd.append("--show-action-dot")
        helper_cmd += ["--stroke-px", str(args.stroke_px)]
        try: subprocess.Popen(helper_cmd)
        except Exception as e: print(f"[warn] failed to spawn helper: {e}", file=sys.stderr)
    else:
        print("[warn] SUDO_USER not set; helper UI won't start.", file=sys.stderr)

    try:
        while True:
            msg = out_q.get()
            bridge.send(msg)
    except KeyboardInterrupt:
        pass

def root_touch_loop(args, out_q, ctrl_q):
    src = open_touch_device(args.device, require_grab=args.grab)

    caps = src.capabilities(absinfo=True)
    abs_map = dict(caps.get(ecodes.EV_ABS, []))
    has_mt = (ecodes.ABS_MT_POSITION_X in abs_map) and (ecodes.ABS_MT_POSITION_Y in abs_map)
    if not abs_map.get(ecodes.ABS_X) and not abs_map.get(ecodes.ABS_MT_POSITION_X):
        print("Couldn't find absolute X on this device.", file=sys.stderr); sys.exit(2)
    if not abs_map.get(ecodes.ABS_Y) and not abs_map.get(ecodes.ABS_MT_POSITION_Y):
        print("Couldn't find absolute Y on this device.", file=sys.stderr); sys.exit(2)

    ax = abs_map.get(ecodes.ABS_MT_POSITION_X) if has_mt else abs_map.get(ecodes.ABS_X)
    ay = abs_map.get(ecodes.ABS_MT_POSITION_Y) if has_mt else abs_map.get(ecodes.ABS_Y)
    raw_min_x, raw_max_x = ax.min, ax.max
    raw_min_y, raw_max_y = ay.min, ay.max

    use_min_x, use_max_x = raw_min_x, raw_max_x
    use_min_y, use_max_y = raw_min_y, raw_max_y

    def scale(v, lo, hi):
        span = max(1, hi - lo)
        return int(65535 * (v - lo) / span)

    ui = build_ui_device()

    # MT & recording state
    current_slot = 0
    slots = {}   # slot -> {'active','x','y','tid','tstart'}
    action_down = False

    recording = False
    rec_refs = {}   # slot -> [(sx,sy)]
    rec_act = []
    rec_started_at = None

    # Gesture trigger state
    g_min_fingers = max(1, args.ref_count + 1)  # refs + action
    g_hold_s = max(0.0, args.gesture_hold_ms / 1000.0)
    g_candidate_since = None

    calib_until = time.monotonic() + args.calib_seconds if args.calib_seconds > 0 else 0

    def emit_touch(x655, y655, down):
        nonlocal action_down
        if down and not action_down:
            ui.write(ecodes.EV_KEY, ecodes.BTN_TOUCH, 1); action_down = True
        elif not down and action_down:
            ui.write(ecodes.EV_KEY, ecodes.BTN_TOUCH, 0); action_down = False
        if down:
            ui.write(ecodes.EV_ABS, ecodes.ABS_X, x655)
            ui.write(ecodes.EV_ABS, ecodes.ABS_Y, y655)
        ui.syn()
        out_q.put({"type":"act_frame", "x":x655, "y":y655, "down":down})

    def start_recording():
        nonlocal recording, rec_refs, rec_act, rec_started_at
        if recording: return
        recording = True
        rec_refs.clear(); rec_act.clear()
        rec_started_at = datetime.datetime.now().isoformat()
        print("[record] START")

    def stop_and_save():
        nonlocal recording, rec_refs, rec_act, rec_started_at
        if not recording: return
        out_q.put({"type":"save_paths",
                   "refs":[pts for _,pts in sorted(rec_refs.items())],
                   "act":rec_act,
                   "started_at":rec_started_at})
        print("[record] SAVE")
        recording = False
        rec_refs.clear(); rec_act.clear(); rec_started_at = None

    try:
        for e in src.read_loop():
            # hotkey control from keyboard threads
            try:
                while True:
                    msg = ctrl_q.get_nowait()
                    if msg[0] == "hk":
                        if args.trigger in ("keyboard", "both"):
                            if msg[1] == "start": start_recording()
                            elif msg[1] == "stop":  stop_and_save()
                    elif msg[0] == "hk_error":
                        print(f"[hotkey] {msg[1]}", file=sys.stderr)
            except queue.Empty:
                pass

            if e.type == ecodes.EV_ABS:
                if has_mt:
                    if e.code == ecodes.ABS_MT_SLOT:
                        current_slot = e.value
                        slots.setdefault(current_slot, {'active':False,'x':0,'y':0,'tid':-1,'tstart':0.0})
                    elif e.code == ecodes.ABS_MT_TRACKING_ID:
                        slots.setdefault(current_slot, {'active':False,'x':0,'y':0,'tid':-1,'tstart':0.0})
                        if e.value == -1:
                            slots[current_slot]['active'] = False
                        else:
                            slots[current_slot].update(active=True, tid=e.value, tstart=time.monotonic())
                    elif e.code == ecodes.ABS_MT_POSITION_X:
                        slots.setdefault(current_slot, {'active':False,'x':0,'y':0,'tid':-1,'tstart':0.0})
                        slots[current_slot]['x'] = e.value
                        if calib_until and time.monotonic() < calib_until:
                            use_min_x = min(use_min_x, e.value); use_max_x = max(use_max_x, e.value)
                    elif e.code == ecodes.ABS_MT_POSITION_Y:
                        slots.setdefault(current_slot, {'active':False,'x':0,'y':0,'tid':-1,'tstart':0.0})
                        slots[current_slot]['y'] = e.value
                        if calib_until and time.monotonic() < calib_until:
                            use_min_y = min(use_min_y, e.value); use_max_y = max(use_max_y, e.value)
                else:
                    if e.code == ecodes.ABS_X:
                        x = e.value
                        if calib_until and time.monotonic() < calib_until:
                            use_min_x = min(use_min_x, x); use_max_x = max(use_max_x, x)
                        slots[0] = slots.get(0, {'active':True,'x':x,'y':0,'tid':0,'tstart':time.monotonic()})
                        slots[0]['x'] = x
                    elif e.code == ecodes.ABS_Y:
                        y = e.value
                        if calib_until and time.monotonic() < calib_until:
                            use_min_y = min(use_min_y, y); use_max_y = max(use_max_y, y)
                        slots[0] = slots.get(0, {'active':True,'x':0,'y':y,'tid':0,'tstart':time.monotonic()})
                        slots[0]['y'] = y

            elif e.type == ecodes.EV_SYN and e.code == ecodes.SYN_REPORT:
                now = time.monotonic()

                if calib_until and now >= calib_until:
                    calib_until = 0
                    def apply_margin(lo, hi, frac):
                        frac = clamp01(frac); w = hi - lo
                        lo2 = int(lo + w * frac); hi2 = int(hi - w * frac)
                        return (lo, hi) if hi2 <= lo2 else (lo2, hi2)
                    use_min_x, use_max_x = apply_margin(use_min_x, use_max_x, args.margin)
                    use_min_y, use_max_y = apply_margin(use_min_y, use_max_y, args.margin)

                # determine refs/action
                active_slots = [k for k,v in slots.items() if v.get('active')]
                active_slots.sort(key=lambda k: slots[k]['tstart'])
                ref_slots = active_slots[:args.ref_count]
                candidates = [k for k in active_slots if k not in ref_slots]
                act_slot = candidates[-1] if candidates else None

                # --- Rule #1: thumb arc from 3 earliest fingers (T, M, P) ---
                if len(active_slots) >= 3:
                    t_slot, m_slot, p_slot = active_slots[0], active_slots[1], active_slots[2]
                    def to655(s):
                        sx = max(use_min_x, min(slots[s]['x'], use_max_x))
                        sy = max(use_min_y, min(slots[s]['y'], use_max_y))
                        sx655 = int(65535 * (sx - use_min_x) / max(1, (use_max_x - use_min_x)))
                        sy655 = int(65535 * (sy - use_min_y) / max(1, (use_max_y - use_min_y)))
                        return sx655, sy655
                    t655 = to655(t_slot)
                    m655 = to655(m_slot)
                    p655 = to655(p_slot)
                    out_q.put({"type":"arc_rule1", "thumb":t655, "mid":m655, "pink":p655})
                else:
                    # clear the arc if fewer than 3 fingers are down
                    out_q.put({"type":"arc_rule1", "clear": True})


                # send refs to UI
                pts = []
                for k in ref_slots:
                    s = slots[k]
                    sx = max(use_min_x, min(s['x'], use_max_x))
                    sy = max(use_min_y, min(s['y'], use_max_y))
                    pts.append((
                        int(65535 * (sx - use_min_x) / max(1, (use_max_x - use_min_x))),
                        int(65535 * (sy - use_min_y) / max(1, (use_max_y - use_min_y)))
                    ))
                out_q.put({"type":"ref_multi", "pts":pts})

                # gesture trigger
                if args.trigger in ("gesture","both"):
                    if len(active_slots) >= g_min_fingers:
                        if g_candidate_since is None:
                            g_candidate_since = now
                        elif (not recording) and (now - g_candidate_since >= g_hold_s):
                            start_recording()
                    else:
                        g_candidate_since = None
                        if recording:
                            stop_and_save()

                # record refs while recording
                if recording and pts:
                    for k, (sx655, sy655) in zip(ref_slots, pts):
                        rec_refs.setdefault(k, []).append((sx655, sy655))

                # drive action + record
                if act_slot is not None:
                    s = slots[act_slot]
                    sx = max(use_min_x, min(s['x'], use_max_x))
                    sy = max(use_min_y, min(s['y'], use_max_y))
                    sx655 = int(65535 * (sx - use_min_x) / max(1, (use_max_x - use_min_x)))
                    sy655 = int(65535 * (sy - use_min_y) / max(1, (use_max_y - use_min_y)))
                    emit_touch(sx655, sy655, True)
                    if recording:
                        rec_act.append((sx655, sy655))
                else:
                    if action_down:
                        emit_touch(0, 0, False)

    except KeyboardInterrupt:
        pass
    finally:
        try: ui.close()
        except: pass
        try: src.ungrab()
        except: pass

# ----------------- Helper UI (user side) -----------------

def run_user_helper(args):
    if not QT_OK:
        sys.exit("[error] PyQt6 not available; install python-pyqt6")
    app = QtWidgets.QApplication(sys.argv)
    screen = QtGui.QGuiApplication.primaryScreen()
    geom = screen.geometry()
    sw, sh = geom.width(), geom.height()

    overlay = IndicatorOverlay(size_px=args.indicator_size,
                               fade_ms=args.indicator_fade_ms,
                               grid_n=args.grid,
                               show_action_dot=args.show_action_dot)
    if args.indicator:
        overlay.showFullScreen()

    shots_dir = pathlib.Path(args.shots_dir).expanduser()
    shots_dir.mkdir(parents=True, exist_ok=True)

    def map_to_px(x655, y655):
        x = int((x655/65535.0) * (sw-1))
        y = int((y655/65535.0) * (sh-1))
        return x, y

    def save_paths_image(payload):
        """payload: {'refs': [...], 'act': [...], 'started_at': str}"""
        refs = payload.get("refs", [])
        act  = payload.get("act", [])
        started_at = payload.get("started_at", "")
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        # 1) Primary target dir
        shots_dir = pathlib.Path(args.shots_dir).expanduser()
        try:
            shots_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"[shot] WARN: mkdir({shots_dir}) failed: {e}")

        fname = shots_dir / f"shot_{ts}.png"

        # Render
        img = QtGui.QImage(sw, sh, QtGui.QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QtGui.QColor(255,255,255,255))
        painter = QtGui.QPainter(img)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        if args.grid > 0:
            pen = QtGui.QPen(QtGui.QColor(200, 200, 200, 120)); pen.setWidth(1)
            painter.setPen(pen)
            for i in range(1, args.grid):
                x = int(i * sw / args.grid); painter.drawLine(x, 0, x, sh)
            for j in range(1, args.grid):
                y = int(j * sh / args.grid); painter.drawLine(0, y, sw, y)

        pen_ref = QtGui.QPen(QtGui.QColor(25,132,255,220)); pen_ref.setWidth(max(1,args.stroke_px))
        painter.setPen(pen_ref)
        for poly in refs:
            pts = [QtCore.QPoint(int((x/65535.0)*(sw-1)), int((y/65535.0)*(sh-1))) for (x,y) in poly]
            for a, b in zip(pts, pts[1:]):
                painter.drawLine(a, b)

        pen_act = QtGui.QPen(QtGui.QColor(46,204,113,240)); pen_act.setWidth(max(2, args.stroke_px+1))
        painter.setPen(pen_act)
        pts = [QtCore.QPoint(int((x/65535.0)*(sw-1)), int((y/65535.0)*(sh-1))) for (x,y) in act]
        for a, b in zip(pts, pts[1:]):
            painter.drawLine(a, b)

        painter.setPen(QtGui.QPen(QtGui.QColor(0,0,0,200)))
        font = painter.font(); font.setPointSize(10); painter.setFont(font)
        started_str = f"start: {started_at}" if started_at else ""
        painter.drawText(12, sh-24, f"{ts}  {started_str}  refs:{len(refs)}  act_pts:{len(act)}")
        painter.end()

        # 2) Save with explicit format and verify; if it fails, try Pictures and /tmp
        def try_save(path: pathlib.Path) -> bool:
            writer = QtGui.QImageWriter(str(path), b'png')
            ok = writer.write(img)
            if not ok:
                print(f"[shot] WARN: save failed at {path} :: {writer.errorString()}")
                return False
            # double-check presence
            try:
                return path.exists() and path.stat().st_size > 0
            except Exception:
                return False

        # Attempt primary
        if try_save(fname):
            print(f"[shot] saved {fname}")
            return

        # Attempt XDG Pictures
        pics = QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.StandardLocation.PicturesLocation)
        if pics:
            alt = pathlib.Path(pics) / f"shot_{ts}.png"
            alt.parent.mkdir(parents=True, exist_ok=True)
            if try_save(alt):
                print(f"[shot] saved (fallback: Pictures) {alt}")
                return

        # Final fallback: /tmp
        tmp = pathlib.Path("/tmp") / f"shot_{ts}.png"
        if try_save(tmp):
            print(f"[shot] saved (fallback: /tmp) {tmp}")
            return

        print("[shot] ERROR: All save attempts failed.")

    reader = BridgeClientReader(args.socket_path)

    def on_msg(obj):
        t = obj.get("type")
        if t == "ref_multi":
            pts = obj.get("pts", [])
            qpts = [QtCore.QPointF(
                int((x/65535.0)*(sw-1)),
                int((y/65535.0)*(sh-1))
            ) for (x,y) in pts]
            if args.indicator:
                overlay.set_ref_points(qpts, visible=bool(qpts))
        elif t == "act_frame" and args.indicator:
            x = int((obj.get("x",0)/65535.0)*(sw-1))
            y = int((obj.get("y",0)/65535.0)*(sh-1))
            overlay.show_act(x, y, bool(obj.get("down", False)))
        elif t == "save_paths":
            save_paths_image(obj)
        elif t == "arc_rule1":
            if obj.get("clear"):
                if args.indicator:
                    overlay.set_thumb_ellipse(0, 0, 0, 0, 0, False)
                    overlay.set_pointer_ellipse(0, 0, 0, 0, 0, False)
                    overlay.set_pointer_mark(0, 0, False)
            else:
                import math

                (tx, ty) = obj.get("thumb", (0,0))
                (mx, my) = obj.get("mid",   (0,0))
                (px, py) = obj.get("pink",  (0,0))

                def to_px(x655, y655):
                    return (
                        int((x655/65535.0) * (sw - 1)),
                        int((y655/65535.0) * (sh - 1))
                    )
                Tx, Ty = to_px(tx, ty)
                Mx, My = to_px(mx, my)
                Px, Py = to_px(px, py)

                # ----- Outer circle centered at M -----
                Cx, Cy = Mx, My
                base_R = math.hypot(Tx - Cx, Ty - Cy)
                scale = max(0.05, float(getattr(args, "outer_circle_scale", getattr(args, "outer_ellipse_scale", 1.0))))
                R = max(2.0, base_R * scale)
                if args.indicator:
                    overlay.set_thumb_ellipse(Cx, Cy, R, R, 0.0, True)

                # ----- Pointer radius (ratio + bounds) -----
                # prefer --pointer-ellipse-ratio; fall back to --pointer-radius-ratio; default 0.62
                ratio = args.pointer_ellipse_ratio
                if ratio is None and args.pointer_radius_ratio is not None:
                    ratio = args.pointer_radius_ratio
                if ratio is None:
                    ratio = 0.62
                ratio = max(0.05, min(0.95, float(ratio)))

                r_in = max(1.0, ratio * R)  # keep > 0 for drawing; no min-gap/outer-margin clipping

                # ----- Dynamic center shift from M toward mid(M,P) based on requested ratio -----
                # ----- Dynamic center shift from M toward mid(M,P) based on requested ratio -----
                midx, midy = 0.5*(Mx + Px), 0.5*(My + Py)

                # shift is driven by the requested ratio (not the clamped r_in)
                gamma = max(0.1, float(getattr(args, "pointer_center_shift_gamma", 1.0)))
                shift = pow(max(0.0, min(1.0, float(ratio))), gamma)

                # blend center from M (small) toward mid(M,P) (large)
                Cix = (1.0 - shift)*Mx + shift*midx
                Ciy = (1.0 - shift)*My + shift*midy

                # no clipping: allow circles to intersect
                if args.indicator:
                    overlay.set_pointer_ellipse(Cix, Ciy, r_in, r_in, 0.0, True)

                # ===== Marker: intersection of ray from M (rotated by --pointer-mark-deg) with the inner circle =====
                # Unit direction along M->T
                vx0, vy0 = (Tx - Mx), (Ty - My)
                norm0 = math.hypot(vx0, vy0) or 1.0
                ux, uy = vx0 / norm0, vy0 / norm0

                # Clockwise rotation on screen by theta
                theta = math.radians(float(getattr(args, "pointer_mark_deg", -20.0)))  # + = CW
                c, s = math.cos(theta), math.sin(theta)
                uxr = c*ux + s*uy
                uyr = -s*ux + c*uy

                # ===== Marker: theta_indicator = k * theta_thumb + intercept =====
                # Thumb angle at M (math CCW; +x is 0)
                theta_thumb = math.atan2(Ty - My, Tx - Mx)

                # k = slope; intercept from --pointer-mark-deg (CW on screen => negative in math)
                k = float(getattr(args, "pointer_mark_slope", 1.0))
                b = -math.radians(float(getattr(args, "pointer_mark_deg", -20.0)))  # CW positive -> negative math

                theta_ind = k * theta_thumb + b

                # Unit direction for the ray from M at theta_ind (math CCW)
                uxr = math.cos(theta_ind)
                uyr = math.sin(theta_ind)

                # ----- Intersection of ray from M with the inner circle (center Ci, radius r_in) -----
                # Ray: P(s) = M + s * u', s >= 0
                # Circle equation: |(M - Ci) + s*u'|^2 = r_in^2
                wx, wy = (Mx - Cix), (My - Ciy)          # vector from Ci to M
                bq = wx*uxr + wy*uyr
                cquad = wx*wx + wy*wy - r_in*r_in
                D = bq*bq - cquad

                have_intersection = D >= 0.0
                if have_intersection:
                    root = math.sqrt(D)
                    s1 = -bq - root
                    s2 = -bq + root
                    s_candidates = [s for s in (s1, s2) if s >= 0.0]
                    if s_candidates:
                        s_base = min(s_candidates)       # first hit along the ray
                    else:
                        s_base = max(s1, s2)
                        if s_base < 0.0:
                            s_base = 0.0
                else:
                    # Ray misses the circle: project to closest point, then snap to circle along direction from Ci
                    s_proj = max(0.0, -bq)
                    s_base = s_proj

                # Base point ON the inner circle (exact if intersected; else directional snap)
                if have_intersection:
                    mx_base = Mx + s_base * uxr
                    my_base = My + s_base * uyr
                else:
                    qx = Mx + s_base * uxr
                    qy = My + s_base * uyr
                    dqx, dqy = (qx - Cix), (qy - Ciy)
                    dn = math.hypot(dqx, dqy) or 1.0
                    mx_base = Cix + (r_in * dqx / dn)
                    my_base = Ciy + (r_in * dqy / dn)

                # ===== Guard ellipses: push ONLY ALONG THE SAME RAY if inside =====
                s_mark = s_base

                # Guard 1: pointer-centered ellipse (axis-aligned) around Ci
                a_ptr = max(1.0, float(getattr(args, "pred_min_ellipse_a_px", 160.0)))
                b_ptr = max(1.0, float(getattr(args, "pred_min_ellipse_b_px", 120.0)))
                # Solve ((wx + s*uxr)^2)/a^2 + ((wy + s*uyr)^2)/b^2 = 1
                Aq = (uxr*uxr)/(a_ptr*a_ptr) + (uyr*uyr)/(b_ptr*b_ptr)
                Bq = 2.0 * ((wx*uxr)/(a_ptr*a_ptr) + (wy*uyr)/(b_ptr*b_ptr))
                Cq = (wx*wx)/(a_ptr*a_ptr) + (wy*wy)/(b_ptr*b_ptr) - 1.0
                f_base = Aq*(s_mark*s_mark) + Bq*s_mark + Cq
                if f_base < 0.0 and Aq > 1e-12:
                    disc = Bq*Bq - 4.0*Aq*Cq
                    if disc >= 0.0:
                        rdisc = math.sqrt(disc)
                        r1 = (-Bq - rdisc) / (2.0*Aq)
                        r2 = (-Bq + rdisc) / (2.0*Aq)
                        ahead = [r for r in (r1, r2) if r >= s_mark]
                        if ahead:
                            s_mark = min(ahead)
                        else:
                            s_mark = max(r1, r2)

                # Guard 2: middle-centered ellipse (axis-aligned) around M
                a_mid = max(1.0, float(getattr(args, "pred_minM_ellipse_a_px", 200.0)))
                b_mid = max(1.0, float(getattr(args, "pred_minM_ellipse_b_px", 140.0)))
                # Distance from M to boundary along u' is: s_mid = 1 / sqrt((uxr^2)/a^2 + (uyr^2)/b^2)
                den_mid = (uxr*uxr)/(a_mid*a_mid) + (uyr*uyr)/(b_mid*b_mid)
                if den_mid > 1e-12:
                    s_mid = 1.0 / math.sqrt(den_mid)
                    if s_mark < s_mid:
                        s_mark = s_mid

                # ----- Final prediction point (gold marker)
                mx_pt = Mx + s_mark * uxr
                my_pt = My + s_mark * uyr
                overlay.set_pointer_mark(mx_pt, my_pt, True)



    reader.got_message.connect(on_msg)

    try:
        app.exec()
    except KeyboardInterrupt:
        pass

# ----------------- CLI -----------------

def main():
    ap = argparse.ArgumentParser(description="Touchpad -> Virtual Touchscreen with robust triggers (sudo-capable)")
    ap.add_argument("device", nargs="?", help="/dev/input/eventN (your touchpad)")
    ap.add_argument("--calib-seconds", type=float, default=1.5)
    ap.add_argument("--margin", type=float, default=0.02)
    ap.add_argument("--grab", action="store_true", help="Exclusive EVIOCGRAB on touchpad (recommended).")
    ap.add_argument("--outer-ellipse-scale", type=float, default=1.0,
                    help="Scale for outer ellipse size (1.0 = fits through thumb point)")
    ap.add_argument("--pointer-ellipse-ratio", type=float, default=0.62,
                    help="Inner ellipse size as a fraction of the outer ellipse 'a' (0.05..0.95)")
    ap.add_argument("--pointer-ellipse-min", type=float, default=40.0,
                    help="Minimum semi-major axis length (pixels) for the pointer ellipse")
    ap.add_argument("--pointer-ellipse-min-a-px", type=float, default=32.0,
                    help="Minimum semi-major axis (a) in pixels for the pointer (inner) ellipse")
    ap.add_argument("--pointer-mark-deg", type=float, default=-20.0,
                    help="Angle offset (degrees) from the M→T line for the inner-circle marker (default -20°).")
    ap.add_argument("--pointer-center-shift-gamma", type=float, default=1.0,
                    help="Response exponent for shifting pointer center from M toward mid(M,P). 1.0=linear, >1=slower near M, <1=faster.")
    ap.add_argument("--pred-minM-ellipse-a-px", type=float, default=200.0,
                    help="Lower-bound ellipse semi-axis a (X) in px centered at Middle finger.")
    ap.add_argument("--pred-minM-ellipse-b-px", type=float, default=140.0,
                    help="Lower-bound ellipse semi-axis b (Y) in px centered at Middle finger.")

    ap.add_argument("--pred-min-ellipse-a-px", type=float, default=160.0,
                    help="Lower-bound ellipse semi-axis a (X) in px for the prediction point; ellipse is centered at the pointer center.")
    ap.add_argument("--pred-min-ellipse-b-px", type=float, default=120.0,
                    help="Lower-bound ellipse semi-axis b (Y) in px for the prediction point; ellipse is centered at the pointer center.")

    ap.add_argument("--pointer-mark-slope", type=float, default=1.0,
                    help="k in theta_indicator = k * theta_thumb + intercept (intercept set by --pointer-mark-deg, CW on screen).")

    # overlay (helper)
    ap.add_argument("--indicator", action="store_true", help="Show on-screen dots")
    ap.add_argument("--indicator-size", type=int, default=18)
    ap.add_argument("--indicator-fade-ms", type=int, default=120)
    ap.add_argument("--grid", type=int, default=0, help="Draw an n×n grid (0=off)")
    ap.add_argument("--show-action-dot", action="store_true")

    ap.add_argument("--pointer-radius-ratio", type=float, default=None,
                    help="Inner pointer circle radius as a fraction of outer circle radius (default 0.62)")
    ap.add_argument("--pointer-roll-deg", type=float, default=10.0,
                    help="Rotate tangency point by this angle (degrees, CCW) from the pinky-x reference")
    ap.add_argument("--pointer-min-extra", type=float, default=40.0,
                    help="Minimum extra semi-major length beyond c (foci distance/2). Prevents tiny pointer ellipses.")
    ap.add_argument("--pointer-min-gap-px", type=float, default=120.0,
                    help="Minimum pixel distance from each focus (middle/pinky) to the nearest point on the inner ellipse (a_in - c).")
    ap.add_argument("--pointer-inner-margin-px", type=float, default=6.0,
                    help="Keep the inner ellipse at least this many pixels smaller in semi-major than the outer ellipse (a - a_in).")


    # reference/action config
    ap.add_argument("--ref-count", type=int, default=1, help="Number of reference fingers (default 1)")

    # triggers
    ap.add_argument("--trigger", choices=["keyboard","gesture","both"], default="both",
                    help="Start/stop recorder via keyboard hotkey, gesture hold, or both")
    ap.add_argument("--hotkey-dev", type=str, help="Specific keyboard /dev/input/eventN (optional; otherwise auto)")
    ap.add_argument("--hotkey", type=str, default="KEY_SPACE")
    ap.add_argument("--gesture-hold-ms", type=int, default=400, help="Hold time before gesture starts recording")
    ap.add_argument("--shots-dir", type=str, default=str(pathlib.Path.home() / "touchshots"))
    ap.add_argument("--stroke-px", type=int, default=3, help="Saved path stroke width")

    # bridge / helper
    ap.add_argument("--socket-path", type=str, default=None, help="Unix socket path (auto per-UID)")
    ap.add_argument("--display-helper", action="store_true", help="(internal) run as user-side helper UI")
    args = ap.parse_args()

    if args.display_helper:
        if not args.socket_path:
            sys.exit("[error] --socket-path is required in --display-helper mode")
        run_user_helper(args); return

    if not args.device:
        sys.exit("Usage: sudo ./touchpad2touch.py /dev/input/eventN [options]")

    if not args.socket_path:
        args.socket_path = default_socket_path()

    # Root server (spawns helper)
    run_root_server(args)

if __name__ == "__main__":
    main()
