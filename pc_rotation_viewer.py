#!/usr/bin/env python3
import argparse
import math
import queue
import re
import threading
import time
import tkinter as tk

import serial

PATTERN_LABEL = re.compile(
    r"Roll:\s*([-+]?\d+(?:\.\d+)?)\s*deg\s*\|\s*Pitch:\s*([-+]?\d+(?:\.\d+)?)\s*deg\s*\|\s*Yaw:\s*([-+]?\d+(?:\.\d+)?)\s*deg"
)
PATTERN_CSV = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?),\s*([-+]?\d+(?:\.\d+)?),\s*([-+]?\d+(?:\.\d+)?)\s*$")
PATTERN_ACTION = re.compile(r"Action:\s*([A-Z_]+)")

ACTION_STYLES = {
    "NEUTRAL": {
        "accent": "#79a8ff",
        "canvas": "#10141f",
        "panel": "#252b3a",
        "hint": "Level hand: LED off",
    },
    "ROLL_POSITIVE": {
        "accent": "#ff9f45",
        "canvas": "#18120d",
        "panel": "#392718",
        "hint": "One-side roll: fast blink",
    },
    "ROLL_NEGATIVE": {
        "accent": "#5ed39b",
        "canvas": "#0d1714",
        "panel": "#193229",
        "hint": "Other-side roll: slow blink",
    },
    "PITCH_UP": {
        "accent": "#ffd84d",
        "canvas": "#19160a",
        "panel": "#3b3412",
        "hint": "Pitch up: LED stays on",
    },
    "PITCH_DOWN": {
        "accent": "#ff6f91",
        "canvas": "#1a0f14",
        "panel": "#3e1a25",
        "hint": "Pitch down: double pulse",
    },
    "TWIST_POSITIVE": {
        "accent": "#8e7dff",
        "canvas": "#131122",
        "panel": "#2a2447",
        "hint": "Positive twist: quick flash",
    },
    "TWIST_NEGATIVE": {
        "accent": "#4de1d2",
        "canvas": "#0c1719",
        "panel": "#173438",
        "hint": "Negative twist: triple flash",
    },
}

VERTICES = [
    (-1.0, -1.0, -1.0),
    (1.0, -1.0, -1.0),
    (1.0, 1.0, -1.0),
    (-1.0, 1.0, -1.0),
    (-1.0, -1.0, 1.0),
    (1.0, -1.0, 1.0),
    (1.0, 1.0, 1.0),
    (-1.0, 1.0, 1.0),
]

EDGES = [
    (0, 1), (1, 2), (2, 3), (3, 0),
    (4, 5), (5, 6), (6, 7), (7, 4),
    (0, 4), (1, 5), (2, 6), (3, 7),
]


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def mix_hex(a: str, b: str, t: float) -> str:
    t = max(0.0, min(1.0, t))
    ar, ag, ab = hex_to_rgb(a)
    br, bg, bb = hex_to_rgb(b)
    rr = int(ar + ((br - ar) * t))
    rg = int(ag + ((bg - ag) * t))
    rb = int(ab + ((bb - ab) * t))
    return f"#{rr:02x}{rg:02x}{rb:02x}"


class RotationWindow:
    def __init__(self, serial_port: str, baud: int, show_raw: bool) -> None:
        self.serial_port = serial_port
        self.baud = baud
        self.show_raw = show_raw

        self.data_queue: queue.Queue[tuple[float, float, float]] = queue.Queue(maxsize=100)
        self.action_queue: queue.Queue[str] = queue.Queue(maxsize=50)
        self.stop_event = threading.Event()

        self.roll_deg = 0.0
        self.pitch_deg = 0.0
        self.yaw_deg = 0.0
        self.action_name = "NEUTRAL"

        self.root = tk.Tk()
        self.root.title("ESP32 MPU6050 Motion Control")
        self.root.geometry("980x700")
        self.root.configure(bg="#111318")

        self.roll_var = tk.StringVar(value="0.00")
        self.pitch_var = tk.StringVar(value="0.00")
        self.yaw_var = tk.StringVar(value="0.00")
        self.action_var = tk.StringVar(value=self.action_name)
        self.hint_var = tk.StringVar(value=ACTION_STYLES["NEUTRAL"]["hint"])

        self._build_ui()
        self._apply_action_style()

        self.reader = threading.Thread(target=self._serial_reader, daemon=True)
        self.reader.start()

        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self._tick()

    def _build_ui(self) -> None:
        self.card = tk.Frame(self.root, bg="#1b1f2a", bd=0, highlightthickness=0)
        self.card.pack(fill="both", expand=True, padx=16, pady=16)

        content = tk.Frame(self.card, bg="#1b1f2a")
        content.pack(fill="both", expand=True)

        left = tk.Frame(content, bg="#1b1f2a")
        left.pack(side="left", fill="y", padx=(4, 12), pady=(6, 6))

        self.action_frame = tk.Frame(left, bg="#252b3a", padx=18, pady=14)
        self.action_frame.pack(pady=10, fill="x")
        tk.Label(
            self.action_frame,
            text="Action",
            fg="#a8b6cf",
            bg="#252b3a",
            font=("Helvetica", 12, "bold"),
        ).pack()
        self.action_label = tk.Label(
            self.action_frame,
            textvariable=self.action_var,
            fg="#ffffff",
            bg="#252b3a",
            font=("Consolas", 18, "bold"),
        )
        self.action_label.pack(pady=(4, 2))
        self.hint_label = tk.Label(
            self.action_frame,
            textvariable=self.hint_var,
            fg="#d8dfef",
            bg="#252b3a",
            wraplength=210,
            justify="center",
            font=("Helvetica", 10),
        )
        self.hint_label.pack()

        self._value_box(left, "Pitch", self.pitch_var).pack(pady=10)
        self._value_box(left, "Roll", self.roll_var).pack(pady=10)
        self._value_box(left, "Yaw", self.yaw_var).pack(pady=10)

        self.canvas = tk.Canvas(content, width=760, height=620, bg="#10141f", highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)

    def _value_box(self, parent: tk.Widget, label: str, var: tk.StringVar) -> tk.Frame:
        frame = tk.Frame(parent, bg="#252b3a", padx=18, pady=12)
        tk.Label(frame, text=label, fg="#a8b6cf", bg="#252b3a", font=("Helvetica", 12, "bold")).pack()
        tk.Label(frame, textvariable=var, fg="#ffffff", bg="#252b3a", font=("Consolas", 20, "bold")).pack()
        return frame

    def _parse_angles(self, line: str) -> tuple[float, float, float] | None:
        m = PATTERN_LABEL.search(line)
        if m:
            return float(m.group(1)), float(m.group(2)), float(m.group(3))

        m = PATTERN_CSV.search(line)
        if m:
            return float(m.group(1)), float(m.group(2)), float(m.group(3))

        return None

    def _parse_action(self, line: str) -> str | None:
        m = PATTERN_ACTION.search(line)
        if not m:
            return None
        return m.group(1)

    def _serial_reader(self) -> None:
        while not self.stop_event.is_set():
            try:
                with serial.Serial(self.serial_port, self.baud, timeout=1) as ser:
                    time.sleep(2.0)

                    while not self.stop_event.is_set():
                        line = ser.readline().decode("utf-8", errors="ignore").strip()
                        if not line:
                            continue

                        if self.show_raw:
                            print(line)

                        parsed_angles = self._parse_angles(line)
                        if parsed_angles is not None:
                            try:
                                self.data_queue.put_nowait(parsed_angles)
                            except queue.Full:
                                pass

                        parsed_action = self._parse_action(line)
                        if parsed_action is not None:
                            try:
                                self.action_queue.put_nowait(parsed_action)
                            except queue.Full:
                                pass
            except Exception:
                time.sleep(1.0)

    def _rotate_vertex(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        roll = math.radians(self.roll_deg)
        pitch = math.radians(self.pitch_deg)
        yaw = math.radians(self.yaw_deg)

        cx = math.cos(roll)
        sx = math.sin(roll)
        y1 = y * cx - z * sx
        z1 = y * sx + z * cx
        x1 = x

        cy = math.cos(pitch)
        sy = math.sin(pitch)
        x2 = x1 * cy + z1 * sy
        z2 = -x1 * sy + z1 * cy
        y2 = y1

        cz = math.cos(yaw)
        sz = math.sin(yaw)
        x3 = x2 * cz - y2 * sz
        y3 = x2 * sz + y2 * cz
        z3 = z2

        return x3, y3, z3

    def _project(self, x: float, y: float, z: float, width: int, height: int) -> tuple[float, float]:
        distance = 5.0
        scale = 290.0
        depth = z + distance
        if depth < 0.1:
            depth = 0.1
        px = (x * scale / depth) + (width / 2.0)
        py = (-y * scale / depth) + (height / 2.0)
        return px, py

    def _current_style(self) -> dict[str, str]:
        return ACTION_STYLES.get(self.action_name, ACTION_STYLES["NEUTRAL"])

    def _apply_action_style(self) -> None:
        style = self._current_style()
        panel = style["panel"]
        canvas_bg = style["canvas"]

        self.card.configure(bg=mix_hex("#1b1f2a", panel, 0.28))
        self.action_frame.configure(bg=panel)
        self.action_label.configure(bg=panel)
        self.hint_label.configure(bg=panel)
        self.hint_var.set(style["hint"])
        self.canvas.configure(bg=canvas_bg)

    def _draw_cube(self) -> None:
        self.canvas.delete("all")
        w = int(self.canvas.winfo_width())
        h = int(self.canvas.winfo_height())

        rotated = [self._rotate_vertex(x, y, z) for (x, y, z) in VERTICES]
        projected = [self._project(x, y, z, w, h) for (x, y, z) in rotated]

        style = self._current_style()
        accent = style["accent"]
        base = mix_hex(style["canvas"], "#ffffff", 0.12)

        self.canvas.create_oval(
            w * 0.15,
            h * 0.14,
            w * 0.85,
            h * 0.86,
            fill=mix_hex(style["canvas"], accent, 0.08),
            outline="",
        )

        for i, j in EDGES:
            x1, y1 = projected[i]
            x2, y2 = projected[j]
            z_avg = (rotated[i][2] + rotated[j][2]) * 0.5
            t = (z_avg + 2.0) / 4.0
            t = max(0.0, min(1.0, t))
            color = mix_hex(base, accent, 0.35 + (0.65 * t))
            width = 3.0 + (1.8 * t)
            self.canvas.create_line(x1, y1, x2, y2, fill=color, width=width)

        self.canvas.create_text(
            26,
            h - 34,
            anchor="w",
            text=f"Action: {self.action_name}",
            fill=mix_hex("#dbe4ff", accent, 0.35),
            font=("Helvetica", 16, "bold"),
        )

    def _tick(self) -> None:
        while True:
            try:
                roll, pitch, yaw = self.data_queue.get_nowait()
            except queue.Empty:
                break

            self.roll_deg = roll
            self.pitch_deg = pitch
            self.yaw_deg = yaw
            self.roll_var.set(f"{roll:.2f}")
            self.pitch_var.set(f"{pitch:.2f}")
            self.yaw_var.set(f"{yaw:.2f}")

        action_changed = False
        while True:
            try:
                action = self.action_queue.get_nowait()
            except queue.Empty:
                break

            self.action_name = action
            self.action_var.set(action)
            action_changed = True

        if action_changed:
            self._apply_action_style()

        self._draw_cube()
        self.root.after(33, self._tick)

    def _close(self) -> None:
        self.stop_event.set()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Display live ESP32 MPU6050 orientation as a rotating cube")
    parser.add_argument("--port", default="/dev/ttyUSB0", help="Serial port (default: /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--show-raw", action="store_true", help="Print raw serial lines to terminal")
    args = parser.parse_args()

    app = RotationWindow(args.port, args.baud, args.show_raw)
    app.run()


if __name__ == "__main__":
    main()
