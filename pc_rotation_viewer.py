#!/usr/bin/env python3
import argparse
import base64
import math
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from dataclasses import dataclass

try:
    import serial
except Exception:
    serial = None

try:
    import cv2
except Exception:
    cv2 = None

try:
    import numpy as np
except Exception:
    np = None

try:
    import onnxruntime as ort
except Exception:
    ort = None

try:
    import mediapipe as mp
    from mediapipe.tasks import python as mp_tasks_python
    from mediapipe.tasks.python import vision as mp_tasks_vision
except Exception:
    mp = None
    mp_tasks_python = None
    mp_tasks_vision = None

try:
    import gi

    gi.require_version("Atspi", "2.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Atspi, Gdk
except Exception:
    Atspi = None
    Gdk = None


PATTERN_LABEL = re.compile(
    r"Roll:\s*([-+]?\d+(?:\.\d+)?)\s*deg\s*\|\s*Pitch:\s*([-+]?\d+(?:\.\d+)?)\s*deg\s*\|\s*Yaw:\s*([-+]?\d+(?:\.\d+)?)\s*deg"
)
PATTERN_CSV = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?),\s*([-+]?\d+(?:\.\d+)?),\s*([-+]?\d+(?:\.\d+)?)\s*$")
PATTERN_ACTION = re.compile(r"Action:\s*([A-Z_]+)")

ROLL_ENTER_DEG = 28.0
ROLL_EXIT_DEG = 16.0
PITCH_ENTER_DEG = 24.0
PITCH_EXIT_DEG = 12.0
TWIST_ANGLE_THRESHOLD_DEG = 24.0
TWIST_RATE_THRESHOLD_DPS = 85.0
TWIST_COOLDOWN_MS = 900
TWIST_HOLD_MS = 350
TWIST_MAX_ROLL_PITCH_DEG = 35.0
NEUTRAL_RESET_ROLL_PITCH_DEG = 8.0
NEUTRAL_RESET_HOLD_MS = 350
CAMERA_CLICK_HOLD_SECONDS = 0.34
CAMERA_CLICK_COOLDOWN_SECONDS = 0.95
HAND_POSE_MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "yolo26_hand_pose_fp32.onnx")
MEDIAPIPE_GESTURE_MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "gesture_recognizer.task")
HAND_POSE_CONFIDENCE = 0.35
HAND_KEYPOINT_CONFIDENCE = 0.15
MEDIAPIPE_FIST_CONFIDENCE = 0.55
HISTORY_LIMIT = 6
SAMPLE_LIMIT = 150
ACTION_ORDER = [
    "NEUTRAL",
    "ROLL_POSITIVE",
    "ROLL_NEGATIVE",
    "PITCH_UP",
    "PITCH_DOWN",
    "TWIST_POSITIVE",
    "TWIST_NEGATIVE",
    "FIST",
]
ACTION_DISPLAY_NAMES = {
    "NEUTRAL": "Neutral",
    "ROLL_POSITIVE": "Roll Right",
    "ROLL_NEGATIVE": "Roll Left",
    "PITCH_UP": "Pitch Up",
    "PITCH_DOWN": "Pitch Down",
    "TWIST_POSITIVE": "Yaw Right",
    "TWIST_NEGATIVE": "Yaw Left",
    "FIST": "Fist",
}
COMPUTER_ACTION_OPTIONS = [
    "Do nothing",
    "Move mouse right",
    "Move mouse left",
    "Move mouse up",
    "Move mouse down",
    "Left click",
    "Right click",
    "Double click",
    "Back",
    "Scroll up",
    "Scroll down",
    "Press Space",
    "Press Enter",
    "Play / Pause",
]

CONTINUOUS_ACTIONS = {
    "Move mouse right",
    "Move mouse left",
    "Move mouse up",
    "Move mouse down",
}

DISCRETE_ACTIONS = {
    "Left click",
    "Right click",
    "Double click",
    "Back",
    "Press Space",
    "Press Enter",
    "Play / Pause",
}

REPEATING_ACTIONS = {
    "Scroll up",
    "Scroll down",
}

ACTION_STYLES = {
    "NEUTRAL": {
        "title": "Pause",
        "accent": "#7ad4ff",
        "canvas": "#081722",
        "panel": "#143144",
        "gesture": "Neutral pose",
        "hint": "No directional gesture is active.",
        "computer_action": "Pointer stays still",
        "computer_detail": "The computer holds position while the device is steady.",
    },
    "ROLL_POSITIVE": {
        "title": "Move Right",
        "accent": "#ffae57",
        "canvas": "#1b1209",
        "panel": "#4a2f16",
        "gesture": "Right tilt detected",
        "hint": "The pointer is moving right.",
        "computer_action": "Pointer moves right",
        "computer_detail": "Pointer speed follows the strength of the detected tilt.",
    },
    "ROLL_NEGATIVE": {
        "title": "Move Left",
        "accent": "#66efb4",
        "canvas": "#091710",
        "panel": "#174232",
        "gesture": "Left tilt detected",
        "hint": "The pointer is moving left.",
        "computer_action": "Pointer moves left",
        "computer_detail": "Pointer speed follows the strength of the detected tilt.",
    },
    "PITCH_UP": {
        "title": "Move Up",
        "accent": "#ffe15c",
        "canvas": "#1a1507",
        "panel": "#494015",
        "gesture": "Up tilt detected",
        "hint": "The pointer is moving up.",
        "computer_action": "Pointer moves up",
        "computer_detail": "The computer maps the pitch angle into upward cursor motion.",
    },
    "PITCH_DOWN": {
        "title": "Move Down",
        "accent": "#ff7ea5",
        "canvas": "#1c0d13",
        "panel": "#4c1f2f",
        "gesture": "Down tilt detected",
        "hint": "The pointer is moving down.",
        "computer_action": "Pointer moves down",
        "computer_detail": "The computer maps the pitch angle into downward cursor motion.",
    },
    "TWIST_POSITIVE": {
        "title": "Right Click",
        "accent": "#9f86ff",
        "canvas": "#151125",
        "panel": "#34295e",
        "gesture": "Right twist detected",
        "hint": "The secondary click action is firing.",
        "computer_action": "Right click",
        "computer_detail": "The computer treats the twist burst as a secondary click.",
    },
    "TWIST_NEGATIVE": {
        "title": "Left Click",
        "accent": "#55e6df",
        "canvas": "#08181b",
        "panel": "#184247",
        "gesture": "Left twist detected",
        "hint": "The primary click action is firing.",
        "computer_action": "Left click",
        "computer_detail": "The computer treats the twist burst as a primary click.",
    },
    "FIST": {
        "title": "Fist",
        "accent": "#66efb4",
        "canvas": "#08181b",
        "panel": "#184247",
        "gesture": "Fist detected",
        "hint": "The camera gesture action is firing.",
        "computer_action": "Left click",
        "computer_detail": "The computer treats the camera fist gesture as a configurable action.",
    },
}

AXIS_COLORS = {
    "roll": "#ffae57",
    "pitch": "#66efb4",
    "yaw": "#7ad4ff",
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

DEFAULT_BINDINGS = {
    "NEUTRAL": "Do nothing",
    "ROLL_POSITIVE": "Move mouse right",
    "ROLL_NEGATIVE": "Move mouse left",
    "PITCH_UP": "Move mouse up",
    "PITCH_DOWN": "Move mouse down",
    "TWIST_POSITIVE": "Right click",
    "TWIST_NEGATIVE": "Left click",
    "FIST": "Left click",
}


@dataclass
class MotionPacket:
    roll: float
    pitch: float
    yaw: float
    scene: str = ""
    timestamp: float = 0.0


class DesktopInput:
    def __init__(self) -> None:
        self.ydotool_path = shutil.which("ydotool")
        self.can_use_atspi = Atspi is not None and Gdk is not None
        self.using_ydotool = self._ydotool_ready()
        self.available = self.using_ydotool or self.can_use_atspi
        self.last_error = ""
        if self.using_ydotool:
            self.status = "Ready"
            self.backend_name = "ydotool virtual input"
        elif self.can_use_atspi:
            self.status = "Ready"
            self.backend_name = "GNOME accessibility input"
        else:
            self.status = "Unavailable"
            self.backend_name = "No desktop input backend"

    def _ydotool_ready(self) -> bool:
        return self.ydotool_path is not None and os.path.exists("/tmp/.ydotool_socket")

    def _run_ydotool(self, *args: str) -> bool:
        if not self._ydotool_ready():
            self.using_ydotool = False
            return False
        self.using_ydotool = True
        env = os.environ.copy()
        env["YDOTOOL_SOCKET"] = "/tmp/.ydotool_socket"
        try:
            result = subprocess.run(
                [self.ydotool_path, *args],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                timeout=0.8,
                check=False,
            )
        except Exception as exc:
            self.last_error = f"ydotool failed: {exc}"
            return False
        if result.returncode != 0:
            self.last_error = "ydotool daemon is not responding"
            return False
        return True

    def move_relative(self, dx: int, dy: int) -> bool:
        if not self.available or (dx == 0 and dy == 0):
            return False
        if self._ydotool_ready() and self._run_ydotool("mousemove", "--", str(dx), str(dy)):
            return True
        if not self.can_use_atspi:
            self.last_error = "No desktop input backend"
            return False
        try:
            moved = bool(Atspi.generate_mouse_event(dx, dy, "rel"))
        except Exception as exc:
            self.last_error = f"Move failed: {exc}"
            return False
        if not moved:
            self.last_error = "Move event was rejected"
        return moved

    def click(self, button: int = 1) -> bool:
        if not self.available:
            return False
        if self._ydotool_ready():
            button_map = {
                1: "0xC0",
                2: "0xC2",
                3: "0xC1",
                4: "0xC4",
                5: "0xC5",
            }
            if self._run_ydotool("click", button_map.get(button, "0xC0")):
                return True
        if not self.can_use_atspi:
            self.last_error = "No desktop input backend"
            return False
        try:
            clicked = bool(Atspi.generate_mouse_event(0, 0, f"b{button}c"))
        except Exception as exc:
            self.last_error = f"Click failed: {exc}"
            return False
        if not clicked:
            self.last_error = "Click event was rejected"
        return clicked

    def double_click(self) -> bool:
        first = self.click(1)
        time.sleep(0.05)
        second = self.click(1)
        return first and second

    def scroll(self, direction: str) -> bool:
        if direction == "up":
            return self.click(4)
        if direction == "down":
            return self.click(5)
        return False

    def key_press(self, key_name: str) -> bool:
        if not self.available:
            return False
        if self._ydotool_ready():
            key_codes = {
                "space": "57",
                "Return": "28",
                "XF86AudioPlay": "164",
            }
            key_code = key_codes.get(key_name)
            if key_code is None:
                self.last_error = f"Unknown ydotool key: {key_name}"
                return False
            if self._run_ydotool("key", f"{key_code}:1", f"{key_code}:0"):
                return True
        if not self.can_use_atspi:
            self.last_error = "No desktop input backend"
            return False
        keyval = Gdk.keyval_from_name(key_name)
        if keyval == 0:
            self.last_error = f"Unknown key: {key_name}"
            return False
        try:
            pressed = bool(Atspi.generate_keyboard_event(keyval, None, Atspi.KeySynthType.PRESSRELEASE))
        except Exception as exc:
            self.last_error = f"Key failed: {exc}"
            return False
        if not pressed:
            self.last_error = "Key event was rejected"
        return pressed

    def back(self) -> bool:
        if not self.available:
            return False
        if self._ydotool_ready():
            if self._run_ydotool("key", "56:1", "105:1", "105:0", "56:0"):
                return True
        if not self.can_use_atspi:
            self.last_error = "No desktop input backend"
            return False
        alt = Gdk.keyval_from_name("Alt_L")
        left = Gdk.keyval_from_name("Left")
        if alt == 0 or left == 0:
            self.last_error = "Back keys unavailable"
            return False
        try:
            pressed = Atspi.generate_keyboard_event(alt, None, Atspi.KeySynthType.PRESS)
            tapped = Atspi.generate_keyboard_event(left, None, Atspi.KeySynthType.PRESSRELEASE)
            released = Atspi.generate_keyboard_event(alt, None, Atspi.KeySynthType.RELEASE)
        except Exception as exc:
            self.last_error = f"Back failed: {exc}"
            return False
        if not (pressed and tapped and released):
            self.last_error = "Back event was rejected"
        return bool(pressed and tapped and released)

    def perform(self, action: str) -> bool:
        if action == "Left click":
            return self.click(1)
        if action == "Right click":
            return self.click(3)
        if action == "Double click":
            return self.double_click()
        if action == "Back":
            return self.back()
        if action == "Scroll up":
            return self.scroll("up")
        if action == "Scroll down":
            return self.scroll("down")
        if action == "Press Space":
            return self.key_press("space")
        if action == "Press Enter":
            return self.key_press("Return")
        if action == "Play / Pause":
            return self.key_press("XF86AudioPlay")
        return False


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


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def wrap_angle_deg(angle_deg: float) -> float:
    while angle_deg > 180.0:
        angle_deg -= 360.0

    while angle_deg <= -180.0:
        angle_deg += 360.0

    return angle_deg


def angle_delta_deg(reference_deg: float, value_deg: float) -> float:
    return wrap_angle_deg(value_deg - reference_deg)


def format_uptime(seconds: float) -> str:
    total = int(max(0.0, seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class RotationWindow:
    def __init__(
        self,
        serial_port: str,
        baud: int,
        show_raw: bool,
        auto_close_seconds: float,
        camera_index: int,
    ) -> None:
        self.serial_port = serial_port
        self.baud = baud
        self.show_raw = show_raw
        self.auto_close_seconds = auto_close_seconds
        self.camera_index = camera_index

        self.data_queue: queue.Queue[MotionPacket] = queue.Queue(maxsize=120)
        self.action_queue: queue.Queue[str] = queue.Queue(maxsize=80)
        self.status_queue: queue.Queue[tuple[bool, str]] = queue.Queue(maxsize=20)
        self.camera_status_queue: queue.Queue[str] = queue.Queue(maxsize=20)
        self.camera_frame_queue: queue.Queue[tuple[str, str, float]] = queue.Queue(maxsize=3)
        self.stop_event = threading.Event()
        self.camera_stop_event = threading.Event()
        self.desktop_input = DesktopInput()

        self.start_monotonic = time.monotonic()
        self.last_sample_time = 0.0
        self.external_action_expire_at = 0.0
        self.external_action_name = "NEUTRAL"
        self.input_enabled = False
        self.last_discrete_action_at = 0.0
        self.last_repeating_action_at = 0.0
        self.last_continuous_action_at = 0.0
        self.last_input_detail = "Disabled"
        self.input_test_until = 0.0
        self.camera_enabled = False
        self.camera_thread: threading.Thread | None = None
        self.camera_detail = "Off"
        self.camera_fist_visible = False
        self.camera_hand_visible = False
        self.camera_click_until = 0.0
        self.camera_preview_data = ""
        self.camera_preview_photo = None
        self.camera_detection_label = "Off"
        self.camera_detection_confidence = 0.0
        self.hand_pose_session = None
        self.hand_pose_input_name = ""
        self.hand_pose_output_name = ""
        self.hand_pose_model_error = ""
        self.gesture_recognizer = None
        self.gesture_model_error = ""
        self.camera_gesture_name = ""

        self.roll_deg = 0.0
        self.pitch_deg = 0.0
        self.yaw_deg = 0.0
        self.roll_rate_dps = 0.0
        self.pitch_rate_dps = 0.0
        self.yaw_rate_dps = 0.0

        self.action_name = "NEUTRAL"
        self.persistent_action = "NEUTRAL"
        self.transient_action = "NEUTRAL"
        self.transient_action_until_ms = 0
        self.last_twist_ms = 0
        self.neutral_pose_since_ms = 0
        self.yaw_gesture_anchor_deg = 0.0

        self.connected = False
        self.connection_detail = f"Waiting for {serial_port}"
        self.scene_name = "Live serial telemetry"
        self.motion_energy = 0.0
        self.stability = 100.0
        self.confidence = 40.0
        self.sample_rate_hz = 0.0
        self.led_on = False
        self.action_changed_at = time.monotonic()
        self.virtual_cursor_x = 0.5
        self.virtual_cursor_y = 0.5
        self.click_flash_until = 0.0
        self.clicked_tile = ""

        self.samples: list[MotionPacket] = []

        self.root = tk.Tk()
        self.root.title("ESP32 Motion Control Studio")
        self.root.geometry("1440x920")
        self.root.minsize(1180, 760)
        self.root.configure(bg="#07121b")

        self.action_var = tk.StringVar(value=ACTION_STYLES["NEUTRAL"]["title"])
        self.mapped_action_var = tk.StringVar(value=DEFAULT_BINDINGS["NEUTRAL"])
        self.roll_var = tk.StringVar(value="+0.0°")
        self.pitch_var = tk.StringVar(value="+0.0°")
        self.yaw_var = tk.StringVar(value="+0.0°")
        self.scene_var = tk.StringVar(value=self.scene_name)
        self.connection_var = tk.StringVar(value=self.connection_detail)
        self.sample_rate_var = tk.StringVar(value="0 Hz")
        self.uptime_var = tk.StringVar(value="00:00")
        self.energy_var = tk.StringVar(value="0%")
        self.stability_var = tk.StringVar(value="100%")
        self.confidence_var = tk.StringVar(value="40%")
        self.led_var = tk.StringVar(value="Off")
        self.input_state_var = tk.StringVar(value="Disabled")
        self.input_detail_var = tk.StringVar(value=self.desktop_input.backend_name)
        self.camera_state_var = tk.StringVar(value="Off")
        self.camera_button_var = tk.StringVar(value="Enable Camera")
        self.camera_detection_var = tk.StringVar(value="Off")
        self.enable_button_var = tk.StringVar(value="Enable")
        self.binding_vars = {
            action: tk.StringVar(value=DEFAULT_BINDINGS[action])
            for action in ACTION_ORDER
        }

        self.axis_widgets: dict[str, dict[str, object]] = {}
        self.metric_widgets: dict[str, dict[str, object]] = {}
        self.mapping_rows: dict[str, dict[str, tk.Widget]] = {}
        self.scroll_columns: list[tuple[tk.Widget, tk.Canvas]] = []

        for action in ACTION_ORDER:
            self.binding_vars[action].trace_add(
                "write",
                lambda *_args, action=action: self._on_binding_changed(action),
            )

        self._build_ui()
        self._bind_keys()
        self._apply_action_style()
        self._update_input_controls()

        self.worker = threading.Thread(target=self._data_source_loop, daemon=True)
        self.worker.start()

        self.root.protocol("WM_DELETE_WINDOW", self._close)
        if auto_close_seconds > 0.0:
            self.root.after(int(auto_close_seconds * 1000.0), self._close)
        self._tick()

    def _build_ui(self) -> None:
        shell = tk.Frame(self.root, bg="#07121b")
        shell.pack(fill="both", expand=True, padx=18, pady=14)

        header = tk.Frame(
            shell,
            bg="#0d1c29",
            padx=14,
            pady=10,
            highlightthickness=1,
            highlightbackground="#1b3346",
        )
        header.pack(fill="x", pady=(0, 10))

        header_right = tk.Frame(header, bg="#0d1c29")
        header_right.pack(side="left")

        self.enable_button = tk.Button(
            header_right,
            textvariable=self.enable_button_var,
            command=self._toggle_input_enabled,
            bg="#66efb4",
            fg="#07121b",
            activebackground="#7dffc5",
            activeforeground="#07121b",
            bd=0,
            padx=28,
            pady=12,
            font=("Avenir Next", 14, "bold"),
        )
        self.enable_button.pack(side="left")

        test_button = tk.Button(
            header_right,
            text="Test Move",
            command=self._test_desktop_input,
            bg="#173041",
            fg="#f4f8ff",
            activebackground="#214158",
            activeforeground="#ffffff",
            bd=0,
            padx=20,
            pady=9,
            font=("Avenir Next", 11, "bold"),
        )
        test_button.pack(side="left", padx=(10, 0))

        self.camera_button = tk.Button(
            header_right,
            textvariable=self.camera_button_var,
            command=self._toggle_camera_enabled,
            bg="#173041",
            fg="#f4f8ff",
            activebackground="#214158",
            activeforeground="#ffffff",
            bd=0,
            padx=18,
            pady=9,
            font=("Avenir Next", 11, "bold"),
        )
        self.camera_button.pack(side="left", padx=(10, 0))

        tk.Label(
            header_right,
            textvariable=self.input_state_var,
            fg="#c8d7e8",
            bg="#0d1c29",
            font=("Trebuchet MS", 11, "bold"),
        ).pack(side="left", padx=(14, 0))

        body = tk.Frame(shell, bg="#07121b")
        body.pack(fill="both", expand=True)

        left_col = self._scroll_column(body, 285)

        center_col = tk.Frame(body, bg="#07121b")
        center_col.pack(side="left", fill="both", expand=True, padx=14)

        right_col = self._scroll_column(body, 350)

        self.action_card, action_body = self._card(left_col, None, "#102433")
        self.action_card.pack(fill="x", pady=(0, 12))

        self.action_label = tk.Label(
            action_body,
            textvariable=self.action_var,
            fg="#ffffff",
            bg="#102433",
            font=("Avenir Next", 24, "bold"),
        )
        self.action_label.pack(anchor="w")
        self.mapped_action_label = tk.Label(
            action_body,
            textvariable=self.mapped_action_var,
            fg="#c8d7e8",
            bg="#102433",
            font=("Trebuchet MS", 12, "bold"),
        )
        self.mapped_action_label.pack(anchor="w", pady=(8, 0))

        axis_card, axis_body = self._card(left_col, None, "#0d1c29")
        axis_card.pack(fill="x")
        self._axis_card(axis_body, "Pitch", "pitch", self.pitch_var, 90.0)
        self._axis_card(axis_body, "Roll", "roll", self.roll_var, 90.0)
        self._axis_card(axis_body, "Yaw", "yaw", self.yaw_var, 180.0)

        status_card, status_body = self._card(left_col, "Status", "#0d1c29")
        status_card.pack(fill="x", pady=(12, 0))
        self._status_line(status_body, "Source", self.connection_var)
        self._status_line(status_body, "Scene", self.scene_var)
        self._status_line(status_body, "Rate", self.sample_rate_var)
        self._status_line(status_body, "Uptime", self.uptime_var)
        self._status_line(status_body, "LED", self.led_var)
        self._status_line(status_body, "Input", self.input_detail_var)
        self._status_line(status_body, "Camera", self.camera_state_var)

        self.visual_card, visual_body = self._card(center_col, None, "#0d1c29")
        self.visual_card.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            visual_body,
            bg="#081722",
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        mappings_card, mappings_body = self._card(right_col, "Mappings", "#0d1c29")
        mappings_card.pack(fill="x")
        for action in ACTION_ORDER:
            self._mapping_row(mappings_body, action)

        desktop_card, desktop_body = self._card(right_col, "Preview", "#0d1c29")
        desktop_card.pack(fill="both", expand=True, pady=(12, 0))
        self.desktop_canvas = tk.Canvas(desktop_body, bg="#08131c", height=245, highlightthickness=0, bd=0)
        self.desktop_canvas.pack(fill="both", expand=True)

        camera_card, camera_body = self._card(right_col, "Camera", "#0d1c29")
        camera_card.pack(fill="x", pady=(12, 0))
        self.camera_canvas = tk.Canvas(camera_body, bg="#08131c", height=176, highlightthickness=0, bd=0)
        self.camera_canvas.pack(fill="x")
        camera_indicator = tk.Frame(camera_body, bg="#10202f", padx=10, pady=8)
        camera_indicator.pack(fill="x", pady=(8, 0))
        self.camera_indicator_dot = tk.Canvas(camera_indicator, width=28, height=28, bg="#10202f", highlightthickness=0, bd=0)
        self.camera_indicator_dot.pack(side="left")
        tk.Label(
            camera_indicator,
            textvariable=self.camera_detection_var,
            fg="#edf4ff",
            bg="#10202f",
            font=("Trebuchet MS", 10, "bold"),
            justify="left",
        ).pack(side="left", padx=(8, 0))

    def _scroll_column(self, parent: tk.Widget, width: int) -> tk.Frame:
        outer = tk.Frame(parent, bg="#07121b", width=width)
        outer.pack(side="left", fill="y")
        outer.pack_propagate(False)

        canvas = tk.Canvas(
            outer,
            bg="#07121b",
            width=width,
            highlightthickness=0,
            bd=0,
        )
        scrollbar = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg="#07121b")
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw", width=width - 16)

        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def update_scroll_region(_event: tk.Event) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def update_inner_width(event: tk.Event) -> None:
            canvas.itemconfigure(window_id, width=max(1, event.width - 16))

        inner.bind("<Configure>", update_scroll_region)
        canvas.bind("<Configure>", update_inner_width)
        self.scroll_columns.append((outer, canvas))

        return inner

    def _card(self, parent: tk.Widget, title: str | None, bg: str) -> tuple[tk.Frame, tk.Frame]:
        frame = tk.Frame(
            parent,
            bg=bg,
            padx=18,
            pady=16,
            highlightthickness=1,
            highlightbackground=mix_hex(bg, "#ffffff", 0.08),
        )
        has_header = bool(title)
        if title:
            tk.Label(
                frame,
                text=title,
                fg="#f4f8ff",
                bg=bg,
                font=("Avenir Next", 16, "bold"),
            ).pack(anchor="w")
        body = tk.Frame(frame, bg=bg)
        body.pack(fill="both", expand=True, pady=(14 if has_header else 0, 0))
        return frame, body

    def _mapping_row(self, parent: tk.Widget, action: str) -> None:
        row = tk.Frame(parent, bg="#10202f", padx=10, pady=8)
        row.pack(fill="x", pady=4)

        label = tk.Label(
            row,
            text=ACTION_DISPLAY_NAMES[action],
            fg="#f4f8ff",
            bg="#10202f",
            width=11,
            anchor="w",
            font=("Avenir Next", 10, "bold"),
        )
        label.pack(side="left")

        menu_wrap = tk.Frame(row, bg="#10202f")
        menu_wrap.pack(side="right", fill="x", expand=True, padx=(8, 0))

        option = tk.OptionMenu(menu_wrap, self.binding_vars[action], *COMPUTER_ACTION_OPTIONS)
        option.configure(
            bg="#173041",
            fg="#f4f8ff",
            activebackground="#214158",
            activeforeground="#ffffff",
            highlightthickness=0,
            bd=0,
            indicatoron=0,
            anchor="w",
            padx=8,
            pady=6,
            font=("Trebuchet MS", 9),
        )
        option["menu"].configure(
            bg="#173041",
            fg="#f4f8ff",
            activebackground="#214158",
            activeforeground="#ffffff",
            bd=0,
            font=("Trebuchet MS", 10),
        )
        option.pack(side="right", fill="x", expand=True)

        self.mapping_rows[action] = {
            "row": row,
            "label": label,
            "menu_wrap": menu_wrap,
            "menu": option,
        }

    def _status_line(self, parent: tk.Widget, label: str, var: tk.StringVar) -> None:
        row = tk.Frame(parent, bg="#0d1c29")
        row.pack(fill="x", pady=4)
        tk.Label(
            row,
            text=label,
            fg="#87a0b6",
            bg="#0d1c29",
            font=("Trebuchet MS", 10, "bold"),
        ).pack(side="left")
        tk.Label(
            row,
            textvariable=var,
            fg="#edf4ff",
            bg="#0d1c29",
            anchor="e",
            font=("JetBrains Mono", 10),
        ).pack(side="right")

    def _axis_card(
        self,
        parent: tk.Widget,
        title: str,
        key: str,
        value_var: tk.StringVar,
        max_abs: float,
    ) -> None:
        row = tk.Frame(parent, bg="#10202f", padx=12, pady=12)
        row.pack(fill="x", pady=5)
        header = tk.Frame(row, bg="#10202f")
        header.pack(fill="x")
        tk.Label(
            header,
            text=title,
            fg="#f4f8ff",
            bg="#10202f",
            font=("Avenir Next", 12, "bold"),
        ).pack(side="left")
        tk.Label(
            header,
            textvariable=value_var,
            fg="#d9e4ef",
            bg="#10202f",
            font=("JetBrains Mono", 12),
        ).pack(side="right")
        canvas = tk.Canvas(row, height=20, bg="#10202f", highlightthickness=0)
        canvas.pack(fill="x", pady=(10, 0))
        self.axis_widgets[key] = {
            "canvas": canvas,
            "max_abs": max_abs,
            "color": AXIS_COLORS[key],
        }

    def _metric_bar(
        self,
        parent: tk.Widget,
        title: str,
        key: str,
        value_var: tk.StringVar,
        color: str,
    ) -> None:
        row = tk.Frame(parent, bg="#0d1c29")
        row.pack(fill="x", pady=6)
        top = tk.Frame(row, bg="#0d1c29")
        top.pack(fill="x")
        tk.Label(
            top,
            text=title,
            fg="#d8e3ef",
            bg="#0d1c29",
            font=("Trebuchet MS", 10, "bold"),
        ).pack(side="left")
        tk.Label(
            top,
            textvariable=value_var,
            fg="#ffffff",
            bg="#0d1c29",
            font=("JetBrains Mono", 10),
        ).pack(side="right")
        canvas = tk.Canvas(row, height=18, bg="#0d1c29", highlightthickness=0)
        canvas.pack(fill="x", pady=(8, 0))
        self.metric_widgets[key] = {
            "canvas": canvas,
            "color": color,
        }

    def _bind_keys(self) -> None:
        self.root.bind("<Escape>", lambda _event: self._set_input_enabled(False))
        self.root.bind_all("<MouseWheel>", self._handle_mousewheel)
        self.root.bind_all("<Button-4>", self._handle_mousewheel)
        self.root.bind_all("<Button-5>", self._handle_mousewheel)

    def _handle_mousewheel(self, event: tk.Event) -> str | None:
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        for outer, canvas in self.scroll_columns:
            if self._is_descendant(widget, outer):
                if event.num == 4:
                    canvas.yview_scroll(-3, "units")
                elif event.num == 5:
                    canvas.yview_scroll(3, "units")
                elif event.delta:
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                return "break"
        return None

    def _is_descendant(self, widget: tk.Widget | None, parent: tk.Widget) -> bool:
        while widget is not None:
            if widget == parent:
                return True
            try:
                widget = widget.master
            except AttributeError:
                return False
        return False

    def _toggle_input_enabled(self) -> None:
        self._set_input_enabled(not self.input_enabled)

    def _toggle_camera_enabled(self) -> None:
        if self.camera_enabled:
            self._stop_camera()
            return

        if cv2 is None:
            self.camera_detail = "Install OpenCV"
            self.camera_state_var.set(self.camera_detail)
            return
        has_mediapipe = mp is not None and os.path.exists(MEDIAPIPE_GESTURE_MODEL_PATH)
        has_yolo_fallback = ort is not None and np is not None and os.path.exists(HAND_POSE_MODEL_PATH)
        if not has_mediapipe and not has_yolo_fallback:
            self.camera_detail = "Missing gesture model"
            self.camera_state_var.set(self.camera_detail)
            return

        self.camera_stop_event.clear()
        self.camera_enabled = True
        self.camera_button_var.set("Disable Camera")
        self.camera_detail = "Starting"
        self.camera_state_var.set(self.camera_detail)
        self.camera_thread = threading.Thread(target=self._camera_loop, daemon=True)
        self.camera_thread.start()

    def _stop_camera(self) -> None:
        self.camera_enabled = False
        self.camera_stop_event.set()
        self.camera_button_var.set("Enable Camera")
        self.camera_detail = "Off"
        self.camera_fist_visible = False
        self.camera_hand_visible = False
        self.camera_gesture_name = ""
        self.camera_preview_data = ""
        self.camera_detection_label = "Off"
        self.camera_detection_confidence = 0.0
        self.camera_detection_var.set("Off")
        self.camera_state_var.set(self.camera_detail)
        while True:
            try:
                self.camera_frame_queue.get_nowait()
            except queue.Empty:
                break

    def _set_input_enabled(self, enabled: bool) -> None:
        if enabled and not self.desktop_input.available:
            self.input_enabled = False
            self.last_input_detail = self.desktop_input.backend_name
            self._update_input_controls()
            return

        self.input_enabled = enabled
        self.last_discrete_action_at = time.monotonic()
        self.last_repeating_action_at = time.monotonic()
        self.last_input_detail = "Enabled" if enabled else "Disabled"
        self._update_input_controls()

    def _test_desktop_input(self) -> None:
        if not self.desktop_input.available:
            self.last_input_detail = self.desktop_input.backend_name
            return

        ok = self.desktop_input.move_relative(160, 0)
        self.root.after(180, lambda: self.desktop_input.move_relative(-160, 0))
        self.input_test_until = time.monotonic() + 0.8
        if ok:
            self.last_input_detail = "Test move sent"
        else:
            self.last_input_detail = self.desktop_input.last_error or "Test move failed"

    def _camera_loop(self) -> None:
        if cv2 is None:
            self._queue_camera_status("Install OpenCV")
            return
        model_ready = self._load_mediapipe_gesture_model()
        if not model_ready and not self._load_hand_pose_model():
            self._queue_camera_status(self.hand_pose_model_error or "YOLO unavailable")
            self.camera_enabled = False
            return

        capture, backend_name = self._open_camera_capture()
        if capture is None:
            self._queue_camera_status("No camera")
            self.camera_enabled = False
            return

        if not capture.isOpened():
            self._queue_camera_status("No camera")
            self.camera_enabled = False
            return

        fist_started_at = 0.0
        last_click_at = 0.0
        last_status_at = 0.0
        last_preview_at = 0.0
        read_failures = 0
        self._queue_camera_status("Gesture watching" if self.gesture_recognizer is not None else "YOLO watching")

        try:
            while not self.stop_event.is_set() and not self.camera_stop_event.is_set():
                ok, frame = capture.read()
                if not ok:
                    read_failures += 1
                    if read_failures >= 40:
                        self._queue_camera_status("Camera stalled")
                        read_failures = 0
                    else:
                        self._queue_camera_status("Camera warming")
                    time.sleep(0.12)
                    continue

                read_failures = 0
                fist_visible, confidence = self._detect_fist(frame)
                now = time.monotonic()
                self.camera_fist_visible = fist_visible
                preview_label = "Fist" if fist_visible else ("Hand" if self.camera_hand_visible else "No hand")
                if now - last_preview_at > 0.10:
                    self._queue_camera_frame(frame, preview_label, confidence)
                    last_preview_at = now

                if fist_visible:
                    if fist_started_at == 0.0:
                        fist_started_at = now
                    held_long_enough = (now - fist_started_at) >= CAMERA_CLICK_HOLD_SECONDS
                    cooled_down = (now - last_click_at) >= CAMERA_CLICK_COOLDOWN_SECONDS
                    if self.input_enabled and held_long_enough and cooled_down:
                        if self._perform_fist_input():
                            last_click_at = now
                            self.click_flash_until = now + 0.32
                            self._queue_camera_status("Fist click")
                        else:
                            self.last_input_detail = self.desktop_input.last_error or "Camera click failed"
                            self._queue_camera_status("Click failed")
                else:
                    fist_started_at = 0.0

                if now - last_status_at > 0.20:
                    if fist_visible:
                        self._queue_camera_status(f"Fist {int(confidence * 100):02d}%")
                    elif self.camera_hand_visible:
                        detail = self.camera_gesture_name or "Hand"
                        self._queue_camera_status(f"{detail} {int(confidence * 100):02d}%")
                    elif self.input_enabled:
                        self._queue_camera_status("Gesture watching" if self.gesture_recognizer is not None else "YOLO watching")
                    else:
                        self._queue_camera_status("Gesture standby" if self.gesture_recognizer is not None else "YOLO standby")
                    last_status_at = now

                time.sleep(0.025)
        finally:
            capture.release()
            if not self.stop_event.is_set() and self.camera_stop_event.is_set():
                self._queue_camera_status("Off")

    def _load_mediapipe_gesture_model(self) -> bool:
        if self.gesture_recognizer is not None:
            return True
        if mp is None or mp_tasks_python is None or mp_tasks_vision is None:
            self.gesture_model_error = "Install MediaPipe"
            return False
        if not os.path.exists(MEDIAPIPE_GESTURE_MODEL_PATH):
            self.gesture_model_error = "Missing MediaPipe model"
            return False

        self._queue_camera_status("Loading Gesture")
        try:
            base_options = mp_tasks_python.BaseOptions(model_asset_path=MEDIAPIPE_GESTURE_MODEL_PATH)
            options = mp_tasks_vision.GestureRecognizerOptions(
                base_options=base_options,
                running_mode=mp_tasks_vision.RunningMode.IMAGE,
                num_hands=1,
                min_hand_detection_confidence=0.45,
                min_hand_presence_confidence=0.45,
                min_tracking_confidence=0.45,
            )
            self.gesture_recognizer = mp_tasks_vision.GestureRecognizer.create_from_options(options)
        except Exception as exc:
            self.gesture_model_error = f"Gesture load failed: {exc}"
            return False

        self.gesture_model_error = ""
        return True

    def _load_hand_pose_model(self) -> bool:
        if self.hand_pose_session is not None:
            return True
        if ort is None or np is None:
            self.hand_pose_model_error = "Install ONNX"
            return False
        if not os.path.exists(HAND_POSE_MODEL_PATH):
            self.hand_pose_model_error = "Missing YOLO model"
            return False

        self._queue_camera_status("Loading YOLO")
        try:
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(
                HAND_POSE_MODEL_PATH,
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
        except Exception as exc:
            self.hand_pose_model_error = f"YOLO load failed: {exc}"
            return False

        self.hand_pose_session = session
        self.hand_pose_input_name = session.get_inputs()[0].name
        self.hand_pose_output_name = session.get_outputs()[0].name
        self.hand_pose_model_error = ""
        return True

    def _open_camera_capture(self) -> tuple[object | None, str]:
        if cv2 is None:
            return None, ""

        device = f"/dev/video{self.camera_index}"
        pipelines = []
        if os.path.exists(device):
            pipelines.append(
                (
                    "GStreamer",
                    (
                        f"v4l2src device={device} ! "
                        "video/x-raw,width=320,height=240,framerate=30/1 ! "
                        "videoconvert ! video/x-raw,format=BGR ! "
                        "appsink drop=true max-buffers=1 sync=false"
                    ),
                )
            )
        pipelines.append(
            (
                "GStreamer auto",
                (
                    "autovideosrc ! "
                    "video/x-raw,width=320,height=240,framerate=30/1 ! "
                    "videoconvert ! video/x-raw,format=BGR ! "
                    "appsink drop=true max-buffers=1 sync=false"
                ),
            )
        )

        for backend_name, pipeline in pipelines:
            capture = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
            if capture.isOpened():
                return capture, backend_name
            capture.release()

        capture = cv2.VideoCapture(self.camera_index)
        if capture.isOpened():
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, 320)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 240)
            capture.set(cv2.CAP_PROP_FPS, 24)
            return capture, "OpenCV"
        capture.release()
        return None, ""

    def _detect_fist(self, frame: object) -> tuple[bool, float]:
        if self.gesture_recognizer is not None:
            return self._detect_fist_with_mediapipe(frame)

        if cv2 is None or np is None or self.hand_pose_session is None:
            return False, 0.0

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        resized = cv2.resize(rgb, (640, 640), interpolation=cv2.INTER_LINEAR)
        tensor = resized.astype(np.float32) / 255.0
        tensor = np.transpose(tensor, (2, 0, 1))[np.newaxis, ...]

        try:
            output = self.hand_pose_session.run(
                [self.hand_pose_output_name],
                {self.hand_pose_input_name: tensor},
            )[0]
        except Exception as exc:
            self.hand_pose_model_error = f"YOLO run failed: {exc}"
            return False, 0.0

        detections = output[0] if output.ndim == 3 else output
        if detections.size == 0:
            return False, 0.0

        best = detections[int(np.argmax(detections[:, 4]))]
        hand_confidence = float(best[4])
        self.camera_hand_visible = hand_confidence >= HAND_POSE_CONFIDENCE
        if hand_confidence < HAND_POSE_CONFIDENCE:
            return False, hand_confidence

        keypoints = best[6:].reshape(21, 3)
        fist_visible, curl_confidence = self._classify_fist_from_keypoints(keypoints)
        return fist_visible, clamp(min(hand_confidence, curl_confidence), 0.0, 1.0)

    def _detect_fist_with_mediapipe(self, frame: object) -> tuple[bool, float]:
        if cv2 is None or mp is None or self.gesture_recognizer is None:
            return False, 0.0

        try:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = self.gesture_recognizer.recognize(image)
        except Exception as exc:
            self.gesture_model_error = f"Gesture run failed: {exc}"
            self.camera_hand_visible = False
            self.camera_gesture_name = ""
            return False, 0.0

        if not result.gestures:
            self.camera_hand_visible = False
            self.camera_gesture_name = ""
            return False, 0.0

        category = result.gestures[0][0]
        gesture_name = category.category_name or ""
        confidence = float(category.score)
        self.camera_hand_visible = True
        self.camera_gesture_name = gesture_name.replace("_", " ")
        return gesture_name == "Closed_Fist" and confidence >= MEDIAPIPE_FIST_CONFIDENCE, confidence

    def _classify_fist_from_keypoints(self, keypoints: object) -> tuple[bool, float]:
        if np is None:
            return False, 0.0

        wrist = keypoints[0, :2]
        palm_points = keypoints[[0, 5, 9, 13, 17], :2]
        palm_conf = keypoints[[0, 5, 9, 13, 17], 2]
        if float(np.mean(palm_conf)) < HAND_KEYPOINT_CONFIDENCE:
            return False, 0.0

        palm_size = max(
            float(np.linalg.norm(keypoints[9, :2] - wrist)),
            float(np.linalg.norm(keypoints[5, :2] - keypoints[17, :2])),
            1.0,
        )

        curled_count = 0
        usable_count = 0
        curl_scores = []
        for mcp_idx, pip_idx, dip_idx, tip_idx in ((5, 6, 7, 8), (9, 10, 11, 12), (13, 14, 15, 16), (17, 18, 19, 20)):
            conf = keypoints[[mcp_idx, pip_idx, dip_idx, tip_idx], 2]
            if float(np.mean(conf)) < HAND_KEYPOINT_CONFIDENCE:
                continue

            mcp = keypoints[mcp_idx, :2]
            pip = keypoints[pip_idx, :2]
            dip = keypoints[dip_idx, :2]
            tip = keypoints[tip_idx, :2]

            mcp_to_tip = float(np.linalg.norm(tip - mcp)) / palm_size
            wrist_to_mcp = float(np.linalg.norm(mcp - wrist)) / palm_size
            wrist_to_pip = float(np.linalg.norm(pip - wrist)) / palm_size
            wrist_to_dip = float(np.linalg.norm(dip - wrist)) / palm_size
            wrist_to_tip = float(np.linalg.norm(tip - wrist)) / palm_size

            v1 = mcp - pip
            v2 = tip - pip
            denom = max(float(np.linalg.norm(v1) * np.linalg.norm(v2)), 1e-6)
            pip_angle = math.degrees(math.acos(clamp(float(np.dot(v1, v2)) / denom, -1.0, 1.0)))

            folded_in = wrist_to_tip < (wrist_to_dip + 0.03) or wrist_to_tip < (wrist_to_pip + 0.10)
            short_finger = mcp_to_tip < 0.48
            sharp_bend = pip_angle < 118.0
            curled = (sharp_bend and folded_in) or (short_finger and wrist_to_tip < (wrist_to_mcp + 0.62))
            usable_count += 1
            if curled:
                curled_count += 1
            bend_score = clamp((145.0 - pip_angle) / 55.0, 0.0, 1.0)
            fold_score = clamp((wrist_to_dip + 0.14 - wrist_to_tip) / 0.34, 0.0, 1.0)
            short_score = clamp((0.62 - mcp_to_tip) / 0.28, 0.0, 1.0)
            curl_scores.append(max(bend_score * 0.7 + fold_score * 0.3, short_score))

        if usable_count < 3:
            return False, 0.0

        curl_ratio = curled_count / usable_count
        curl_confidence = clamp((curl_ratio * 0.60) + (float(np.mean(curl_scores)) * 0.40), 0.0, 1.0)
        return curled_count >= 4, curl_confidence

    def _queue_camera_status(self, detail: str) -> None:
        try:
            self.camera_status_queue.put_nowait(detail)
        except queue.Full:
            pass

    def _queue_camera_frame(self, frame: object, label: str, confidence: float) -> None:
        if cv2 is None:
            return

        try:
            preview = cv2.resize(frame, (320, 240), interpolation=cv2.INTER_AREA)
            ok, encoded = cv2.imencode(".png", preview)
            if not ok:
                return
            data = base64.b64encode(encoded).decode("ascii")
        except Exception:
            return

        if self.camera_frame_queue.full():
            try:
                self.camera_frame_queue.get_nowait()
            except queue.Empty:
                pass

        try:
            self.camera_frame_queue.put_nowait((data, label, confidence))
        except queue.Full:
            pass

    def _perform_fist_input(self) -> bool:
        if not self.desktop_input.available:
            self.last_input_detail = self.desktop_input.backend_name
            return False

        configured_action = self.binding_vars["FIST"].get()
        if configured_action == "Do nothing":
            self.last_input_detail = "Fist ignored"
            return True

        if configured_action in CONTINUOUS_ACTIONS:
            distance = 130
            dx = 0
            dy = 0
            if configured_action == "Move mouse right":
                dx = distance
            elif configured_action == "Move mouse left":
                dx = -distance
            elif configured_action == "Move mouse up":
                dy = -distance
            elif configured_action == "Move mouse down":
                dy = distance

            if self.desktop_input.move_relative(dx, dy):
                self.last_input_detail = f"Fist: {configured_action}"
                return True
            self.last_input_detail = self.desktop_input.last_error or f"{configured_action} failed"
            return False

        if configured_action in DISCRETE_ACTIONS or configured_action in REPEATING_ACTIONS:
            if self.desktop_input.perform(configured_action):
                self.last_discrete_action_at = time.monotonic()
                self.last_input_detail = f"Fist: {configured_action}"
                return True
            self.last_input_detail = self.desktop_input.last_error or f"{configured_action} failed"
            return False

        self.last_input_detail = f"Fist: {configured_action}"
        return False

    def _update_input_controls(self) -> None:
        if self.input_enabled:
            self.enable_button_var.set("Disable")
            self.input_state_var.set("Input enabled")
            self.enable_button.configure(bg="#ff7ea5", activebackground="#ff9ab8", fg="#07121b")
        else:
            self.enable_button_var.set("Enable")
            self.input_state_var.set("Input disabled")
            self.enable_button.configure(bg="#66efb4", activebackground="#7dffc5", fg="#07121b")

    def _data_source_loop(self) -> None:
        self._serial_reader()

    def _serial_reader(self) -> None:
        if serial is None:
            try:
                self.status_queue.put_nowait((False, "Serial support is unavailable"))
            except queue.Full:
                pass
            return

        while not self.stop_event.is_set():
            try:
                try:
                    self.status_queue.put_nowait((False, f"Opening {self.serial_port}"))
                except queue.Full:
                    pass

                with serial.Serial(self.serial_port, self.baud, timeout=1) as ser:
                    time.sleep(2.0)
                    try:
                        self.status_queue.put_nowait((True, f"Connected to {self.serial_port}"))
                    except queue.Full:
                        pass

                    while not self.stop_event.is_set():
                        line = ser.readline().decode("utf-8", errors="ignore").strip()
                        if not line:
                            continue

                        if self.show_raw:
                            print(line)

                        parsed_angles = self._parse_angles(line)
                        if parsed_angles is not None:
                            packet = MotionPacket(
                                roll=parsed_angles[0],
                                pitch=parsed_angles[1],
                                yaw=parsed_angles[2],
                                scene="Live serial telemetry",
                                timestamp=time.monotonic(),
                            )
                            try:
                                self.data_queue.put_nowait(packet)
                            except queue.Full:
                                pass

                        # The desktop app classifies actions from the raw angles so
                        # local orientation remaps work even if older firmware is running.
            except Exception:
                try:
                    self.status_queue.put_nowait((False, f"Waiting for {self.serial_port}"))
                except queue.Full:
                    pass
                time.sleep(1.0)

    def _parse_angles(self, line: str) -> tuple[float, float, float] | None:
        match = PATTERN_LABEL.search(line)
        if match:
            return float(match.group(1)), float(match.group(2)), float(match.group(3))

        match = PATTERN_CSV.search(line)
        if match:
            return float(match.group(1)), float(match.group(2)), float(match.group(3))

        return None

    def _parse_action(self, line: str) -> str | None:
        match = PATTERN_ACTION.search(line)
        if not match:
            return None
        return match.group(1)

    def _current_style(self) -> dict[str, str]:
        return ACTION_STYLES.get(self.action_name, ACTION_STYLES["NEUTRAL"])

    def _apply_action_style(self) -> None:
        style = self._current_style()
        panel = style["panel"]
        canvas_bg = style["canvas"]

        self.action_card.configure(bg=mix_hex("#102433", panel, 0.4))
        self.action_label.configure(bg=mix_hex("#102433", panel, 0.4))
        self.mapped_action_label.configure(bg=mix_hex("#102433", panel, 0.4))
        self.canvas.configure(bg=canvas_bg)
        self._update_mapping_rows()

    def _tick(self) -> None:
        self._drain_status_queue()
        self._drain_camera_status_queue()
        self._drain_action_queue()
        self._drain_data_queue()
        self._sync_current_mapping()
        self._update_status_vars()
        self._update_axis_bars()
        self._update_metric_bars()
        self._draw_camera_preview()
        self._draw_desktop_preview()
        self._draw_scene()
        if not self.stop_event.is_set():
            self.root.after(33, self._tick)

    def _drain_status_queue(self) -> None:
        changed = False
        while True:
            try:
                connected, detail = self.status_queue.get_nowait()
            except queue.Empty:
                break

            self.connected = connected
            self.connection_detail = detail
            changed = True

        if not changed:
            return

    def _drain_camera_status_queue(self) -> None:
        changed = False
        while True:
            try:
                detail = self.camera_status_queue.get_nowait()
            except queue.Empty:
                break

            self.camera_detail = detail
            changed = True

        if not changed:
            return

        self.camera_state_var.set(self.camera_detail)
        if self.camera_detail in {"No camera", "Install OpenCV", "Off"} and not self.camera_enabled:
            self.camera_button_var.set("Enable Camera")
        elif self.camera_enabled:
            self.camera_button_var.set("Disable Camera")

    def _drain_action_queue(self) -> None:
        while True:
            try:
                action = self.action_queue.get_nowait()
            except queue.Empty:
                break

            if action in ACTION_STYLES:
                self.external_action_name = action
                self.external_action_expire_at = time.monotonic() + 0.35

    def _drain_data_queue(self) -> None:
        while True:
            try:
                packet = self.data_queue.get_nowait()
            except queue.Empty:
                break

            self._consume_packet(packet)

    def _consume_packet(self, packet: MotionPacket) -> None:
        now = packet.timestamp if packet.timestamp > 0.0 else time.monotonic()
        dt = now - self.last_sample_time if self.last_sample_time > 0.0 else 0.0
        self.last_sample_time = now

        prev_roll = self.roll_deg
        prev_pitch = self.pitch_deg
        prev_yaw = self.yaw_deg

        self.roll_deg = packet.roll
        self.pitch_deg = packet.pitch
        self.yaw_deg = packet.yaw
        self.scene_name = packet.scene or self.scene_name

        if dt > 0.0:
            self.roll_rate_dps = angle_delta_deg(prev_roll, self.roll_deg) / dt
            self.pitch_rate_dps = angle_delta_deg(prev_pitch, self.pitch_deg) / dt
            self.yaw_rate_dps = angle_delta_deg(prev_yaw, self.yaw_deg) / dt
            self.sample_rate_hz = 1.0 / dt

        energy = (
            abs(self.roll_rate_dps) * 0.18 +
            abs(self.pitch_rate_dps) * 0.18 +
            abs(self.yaw_rate_dps) * 0.12
        )
        self.motion_energy = clamp(energy, 0.0, 100.0)
        attitude_load = (abs(self.roll_deg) * 0.8) + (abs(self.pitch_deg) * 1.0) + (abs(self.yaw_rate_dps) * 0.05)
        self.stability = clamp(100.0 - attitude_load, 0.0, 100.0)

        raw_confidence = 35.0
        raw_confidence += min(22.0, abs(self.roll_deg) * 0.7)
        raw_confidence += min(22.0, abs(self.pitch_deg) * 0.85)
        raw_confidence += min(16.0, abs(self.yaw_rate_dps) * 0.08)
        if self.action_name.startswith("TWIST"):
            raw_confidence += 8.0
        self.confidence = clamp(raw_confidence, 20.0, 100.0)

        self.roll_var.set(f"{self.roll_deg:+05.1f}°")
        self.pitch_var.set(f"{self.pitch_deg:+05.1f}°")
        self.yaw_var.set(f"{self.yaw_deg:+06.1f}°")

        self.samples.append(packet)
        if len(self.samples) > SAMPLE_LIMIT:
            self.samples = self.samples[-SAMPLE_LIMIT:]

        computed_action = self._classify_action(int(now * 1000.0))
        if time.monotonic() < self.external_action_expire_at:
            computed_action = self.external_action_name

        action_changed = self._set_action(computed_action)
        if self.input_enabled:
            self._update_virtual_cursor(dt)
        self._perform_configured_input(dt, action_changed)
        self.led_on = self._led_state_for_action(self.action_name, int(now * 1000.0))

    def _classify_action(self, now_ms: int) -> str:
        self.persistent_action = self._classify_persistent_action()
        self._update_yaw_anchor(now_ms)

        in_twist_pose = (
            abs(self.roll_deg) < TWIST_MAX_ROLL_PITCH_DEG and
            abs(self.pitch_deg) < TWIST_MAX_ROLL_PITCH_DEG
        )

        if in_twist_pose and (now_ms - self.last_twist_ms) >= TWIST_COOLDOWN_MS:
            yaw_offset_deg = angle_delta_deg(self.yaw_gesture_anchor_deg, self.yaw_deg)
            if yaw_offset_deg >= TWIST_ANGLE_THRESHOLD_DEG and self.yaw_rate_dps >= TWIST_RATE_THRESHOLD_DPS:
                self.transient_action = "TWIST_NEGATIVE"
                self.transient_action_until_ms = now_ms + TWIST_HOLD_MS
                self.last_twist_ms = now_ms
                self.yaw_gesture_anchor_deg = self.yaw_deg
            elif yaw_offset_deg <= -TWIST_ANGLE_THRESHOLD_DEG and self.yaw_rate_dps <= -TWIST_RATE_THRESHOLD_DPS:
                self.transient_action = "TWIST_POSITIVE"
                self.transient_action_until_ms = now_ms + TWIST_HOLD_MS
                self.last_twist_ms = now_ms
                self.yaw_gesture_anchor_deg = self.yaw_deg

        if now_ms >= self.transient_action_until_ms:
            self.transient_action = "NEUTRAL"

        if self.transient_action != "NEUTRAL":
            return self.transient_action
        return self.persistent_action

    def _classify_persistent_action(self) -> str:
        if self.persistent_action == "ROLL_POSITIVE" and self.pitch_deg < -PITCH_EXIT_DEG:
            return "ROLL_POSITIVE"
        if self.persistent_action == "ROLL_NEGATIVE" and self.pitch_deg > PITCH_EXIT_DEG:
            return "ROLL_NEGATIVE"
        if self.persistent_action == "PITCH_UP" and self.roll_deg < -ROLL_EXIT_DEG:
            return "PITCH_UP"
        if self.persistent_action == "PITCH_DOWN" and self.roll_deg > ROLL_EXIT_DEG:
            return "PITCH_DOWN"

        if self.roll_deg > ROLL_ENTER_DEG and abs(self.roll_deg) >= abs(self.pitch_deg):
            return "PITCH_DOWN"
        if self.roll_deg < -ROLL_ENTER_DEG and abs(self.roll_deg) >= abs(self.pitch_deg):
            return "PITCH_UP"
        if self.pitch_deg > PITCH_ENTER_DEG:
            return "ROLL_NEGATIVE"
        if self.pitch_deg < -PITCH_ENTER_DEG:
            return "ROLL_POSITIVE"
        return "NEUTRAL"

    def _update_yaw_anchor(self, now_ms: int) -> None:
        in_neutral_pose = (
            abs(self.roll_deg) < NEUTRAL_RESET_ROLL_PITCH_DEG and
            abs(self.pitch_deg) < NEUTRAL_RESET_ROLL_PITCH_DEG
        )

        if not in_neutral_pose:
            self.neutral_pose_since_ms = 0
            return

        if self.neutral_pose_since_ms == 0:
            self.neutral_pose_since_ms = now_ms
            return

        if (now_ms - self.neutral_pose_since_ms) >= NEUTRAL_RESET_HOLD_MS:
            self.yaw_gesture_anchor_deg = self.yaw_deg

    def _set_action(self, action: str) -> bool:
        if action not in ACTION_STYLES:
            action = "NEUTRAL"

        if action == self.action_name:
            return False

        self.action_name = action
        self.action_changed_at = time.monotonic()
        if action.startswith("TWIST"):
            self.click_flash_until = time.monotonic() + 0.32
            self.clicked_tile = self._tile_under_cursor()
        style = ACTION_STYLES[action]
        self.action_var.set(style["title"])
        self._sync_current_mapping()
        self._apply_action_style()
        return True

    def _sync_current_mapping(self) -> None:
        self.mapped_action_var.set(self.binding_vars[self.action_name].get())

    def _perform_configured_input(self, dt: float, action_changed: bool) -> None:
        if not self.input_enabled or not self.desktop_input.available:
            return

        configured_action = self.binding_vars[self.action_name].get()
        now = time.monotonic()

        if configured_action in CONTINUOUS_ACTIONS:
            self._perform_continuous_input(configured_action, dt)
            return

        if configured_action in REPEATING_ACTIONS:
            if now - self.last_repeating_action_at < 0.10:
                return
            if self.desktop_input.perform(configured_action):
                self.last_repeating_action_at = now
                self.last_input_detail = configured_action
            else:
                self.last_input_detail = self.desktop_input.last_error or f"{configured_action} failed"
            return

        if configured_action in DISCRETE_ACTIONS and action_changed:
            if now - self.last_discrete_action_at < 0.28:
                return
            if self.desktop_input.perform(configured_action):
                self.last_discrete_action_at = now
                self.last_input_detail = configured_action
            else:
                self.last_input_detail = self.desktop_input.last_error or f"{configured_action} failed"

    def _perform_continuous_input(self, configured_action: str, dt: float) -> None:
        if dt <= 0.0:
            return
        now = time.monotonic()
        if now - self.last_continuous_action_at < 0.045:
            return

        roll_strength = max(0.0, abs(self.roll_deg) - ROLL_EXIT_DEG)
        pitch_strength = max(0.0, abs(self.pitch_deg) - PITCH_EXIT_DEG)
        strength = max(roll_strength, pitch_strength)
        pixels_per_second = 120.0 + min(780.0, strength * 18.0)
        distance = max(1, int(pixels_per_second * dt))

        dx = 0
        dy = 0
        if configured_action == "Move mouse right":
            dx = distance
        elif configured_action == "Move mouse left":
            dx = -distance
        elif configured_action == "Move mouse up":
            dy = -distance
        elif configured_action == "Move mouse down":
            dy = distance

        if self.desktop_input.move_relative(dx, dy):
            self.last_continuous_action_at = now
            self.last_input_detail = configured_action
        else:
            self.last_input_detail = self.desktop_input.last_error or "Move failed"

    def _on_binding_changed(self, action: str) -> None:
        if action == self.action_name:
            self._sync_current_mapping()
        self._update_mapping_rows()

    def _update_mapping_rows(self) -> None:
        active = "FIST" if self.camera_fist_visible else self.action_name
        for action, widgets in self.mapping_rows.items():
            is_active = action == active
            bg = mix_hex("#10202f", ACTION_STYLES[action]["panel"], 0.28 if is_active else 0.0)
            fg = ACTION_STYLES[action]["accent"] if is_active else "#f4f8ff"
            widgets["row"].configure(bg=bg)
            widgets["label"].configure(bg=bg, fg=fg)
            widgets["menu_wrap"].configure(bg=bg)
            widgets["menu"].configure(
                bg=mix_hex("#173041", ACTION_STYLES[action]["accent"], 0.16 if is_active else 0.0),
                fg="#f4f8ff",
            )

    def _update_axis_bars(self) -> None:
        values = {
            "pitch": self.pitch_deg,
            "roll": self.roll_deg,
            "yaw": self.yaw_deg,
        }

        for key, config in self.axis_widgets.items():
            canvas = config["canvas"]
            max_abs = float(config["max_abs"])
            color = str(config["color"])
            width = max(canvas.winfo_width(), 50)
            height = max(canvas.winfo_height(), 18)
            mid = width / 2.0
            value = clamp(values[key], -max_abs, max_abs)
            fill_extent = (value / max_abs) * (width * 0.44)

            canvas.delete("all")
            canvas.create_rectangle(4, 5, width - 4, height - 5, fill="#0a1520", outline="")
            canvas.create_line(mid, 4, mid, height - 4, fill="#35506a", width=2)
            canvas.create_rectangle(8, (height / 2.0) - 1, width - 8, (height / 2.0) + 1, fill="#173041", outline="")
            if value >= 0:
                canvas.create_rectangle(mid, 4, mid + fill_extent, height - 4, fill=color, outline="")
            else:
                canvas.create_rectangle(mid + fill_extent, 4, mid, height - 4, fill=color, outline="")

    def _update_status_vars(self) -> None:
        self.connection_var.set(self.connection_detail)
        self.scene_var.set(self.scene_name)
        self.sample_rate_var.set(f"{self.sample_rate_hz:04.1f} Hz")
        self.uptime_var.set(format_uptime(time.monotonic() - self.start_monotonic))
        self.energy_var.set(f"{int(self.motion_energy):3d}%")
        self.stability_var.set(f"{int(self.stability):3d}%")
        self.confidence_var.set(f"{int(self.confidence):3d}%")
        self.led_var.set("On" if self.led_on else "Off")
        if self.input_enabled:
            self.input_detail_var.set(self.last_input_detail)
        elif not self.desktop_input.available:
            self.input_detail_var.set(self.desktop_input.backend_name)
        else:
            self.input_detail_var.set("Disabled")

    def _update_metric_bars(self) -> None:
        values = {
            "energy": self.motion_energy,
            "stability": self.stability,
            "confidence": self.confidence,
        }
        for key, config in self.metric_widgets.items():
            canvas = config["canvas"]
            color = str(config["color"])
            value = clamp(values[key], 0.0, 100.0)
            width = max(canvas.winfo_width(), 50)
            height = max(canvas.winfo_height(), 18)
            canvas.delete("all")
            canvas.create_rectangle(2, 5, width - 2, height - 5, fill="#08131c", outline="")
            fill_width = 4.0 + ((width - 8.0) * value / 100.0)
            canvas.create_rectangle(4, 5, fill_width, height - 5, fill=color, outline="")
            canvas.create_line(4, height - 4, width - 4, height - 4, fill="#173041")

    def _draw_camera_preview(self) -> None:
        canvas = self.camera_canvas
        canvas.delete("all")
        self.camera_indicator_dot.delete("all")
        width = max(canvas.winfo_width(), 245)
        height = max(canvas.winfo_height(), 176)

        while True:
            try:
                data, label, confidence = self.camera_frame_queue.get_nowait()
            except queue.Empty:
                break
            self.camera_preview_data = data
            self.camera_detection_label = label
            self.camera_detection_confidence = confidence

        canvas.create_rectangle(0, 0, width, height, fill="#08131c", outline="#183243")
        if self.camera_preview_data:
            try:
                self.camera_preview_photo = tk.PhotoImage(data=self.camera_preview_data)
                image_width = self.camera_preview_photo.width()
                image_height = self.camera_preview_photo.height()
                scale_x = width / max(1, image_width)
                scale_y = height / max(1, image_height)
                scale = min(scale_x, scale_y)
                draw_width = image_width * scale
                draw_height = image_height * scale
                x = (width - draw_width) / 2.0
                y = (height - draw_height) / 2.0
                canvas.create_image(x, y, anchor="nw", image=self.camera_preview_photo)
            except tk.TclError:
                self.camera_preview_data = ""

        if not self.camera_preview_data:
            text = "Enable camera" if not self.camera_enabled else self.camera_detail
            canvas.create_text(
                width / 2.0,
                height / 2.0,
                text=text,
                fill="#87a0b6",
                font=("Trebuchet MS", 12, "bold"),
            )
            if self.camera_enabled:
                self.camera_detection_label = "Waiting"
                self.camera_detection_confidence = 0.0

        if self.camera_detection_label == "Fist":
            dot_color = "#66efb4"
            label_text = f"Fist {int(self.camera_detection_confidence * 100):02d}%"
        elif self.camera_detection_label == "Hand":
            dot_color = "#7ad4ff"
            label_text = f"Hand {int(self.camera_detection_confidence * 100):02d}%"
        elif self.camera_enabled:
            dot_color = "#7e94a9"
            label_text = "No hand"
        else:
            dot_color = "#293b4c"
            label_text = "Off"

        self.camera_detection_var.set(label_text)
        self.camera_indicator_dot.create_oval(4, 4, 24, 24, fill=dot_color, outline="")

    def _update_virtual_cursor(self, dt: float) -> None:
        if dt <= 0.0:
            return

        speed = 0.10 + min(0.34, abs(self.roll_deg + self.pitch_deg) / 180.0)
        if self.action_name == "ROLL_POSITIVE":
            self.virtual_cursor_x += speed * dt
        elif self.action_name == "ROLL_NEGATIVE":
            self.virtual_cursor_x -= speed * dt
        elif self.action_name == "PITCH_UP":
            self.virtual_cursor_y -= speed * dt
        elif self.action_name == "PITCH_DOWN":
            self.virtual_cursor_y += speed * dt

        self.virtual_cursor_x = clamp(self.virtual_cursor_x, 0.08, 0.92)
        self.virtual_cursor_y = clamp(self.virtual_cursor_y, 0.10, 0.90)

    def _tile_under_cursor(self) -> str:
        return ""

    def _sync_preview_cursor_to_pointer(self) -> None:
        root_x = self.root.winfo_rootx()
        root_y = self.root.winfo_rooty()
        root_width = max(1, self.root.winfo_width())
        root_height = max(1, self.root.winfo_height())

        pointer_x = self.root.winfo_pointerx()
        pointer_y = self.root.winfo_pointery()
        self.virtual_cursor_x = clamp((pointer_x - root_x) / root_width, 0.08, 0.92)
        self.virtual_cursor_y = clamp((pointer_y - root_y) / root_height, 0.10, 0.90)

    def _draw_desktop_preview(self) -> None:
        canvas = self.desktop_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 260)
        height = max(canvas.winfo_height(), 220)
        style = self._current_style()
        accent = style["accent"]
        if self.input_enabled:
            self._sync_preview_cursor_to_pointer()

        canvas.create_rectangle(0, 0, width, height, fill="#08131c", outline="")
        canvas.create_rectangle(14, 16, width - 14, height - 18, fill="#0d1c29", outline="#1f3b50")
        canvas.create_rectangle(14, 16, width - 14, 42, fill="#102433", outline="")
        canvas.create_oval(25, 25, 35, 35, fill="#ff7ea5", outline="")
        canvas.create_oval(43, 25, 53, 35, fill="#ffe15c", outline="")
        canvas.create_oval(61, 25, 71, 35, fill="#66efb4", outline="")
        canvas.create_rectangle(30, 58, width - 30, height - 34, fill="#0a1722", outline="#1c3548")

        cx = 14 + ((width - 28) * self.virtual_cursor_x)
        cy = 16 + ((height - 34) * self.virtual_cursor_y)
        if time.monotonic() < self.click_flash_until:
            radius = 24.0 * clamp((self.click_flash_until - time.monotonic()) / 0.32, 0.0, 1.0)
            canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, outline=accent, width=3)
        canvas.create_polygon(cx, cy, cx, cy + 25, cx + 8, cy + 18, cx + 14, cy + 32, cx + 20, cy + 29, cx + 14, cy + 16, cx + 25, cy + 16, fill="#ffffff", outline="#07121b")

    def _draw_scene(self) -> None:
        self.canvas.delete("all")
        width = max(self.canvas.winfo_width(), 400)
        height = max(self.canvas.winfo_height(), 300)

        style = self._current_style()
        accent = style["accent"]
        canvas_bg = style["canvas"]
        base_line = mix_hex(canvas_bg, "#dbe8ff", 0.12)

        self.canvas.create_rectangle(0, 0, width, height, fill=canvas_bg, outline="")

        for idx, blend in enumerate((0.08, 0.13, 0.20)):
            inset_x = 80 + (idx * 32)
            inset_y = 54 + (idx * 26)
            self.canvas.create_oval(
                inset_x,
                inset_y,
                width - inset_x,
                height - 210 + (idx * 14),
                fill=mix_hex(canvas_bg, accent, blend),
                outline="",
            )

        self._draw_horizon(width, height, accent, base_line)
        self._draw_cube(width, height, accent, canvas_bg)
        self._draw_compass(width, height, accent, base_line)
        self._draw_action_overlay(width, height, accent, canvas_bg)
        self._draw_timeline(width, height, accent)

    def _draw_horizon(self, width: int, height: int, accent: str, base_line: str) -> None:
        cx = width * 0.5
        cy = (height * 0.38) + clamp(self.pitch_deg, -45.0, 45.0) * 2.4
        span = width * 0.72
        angle = math.radians(self.roll_deg * 0.85)
        dx = math.cos(angle) * span * 0.5
        dy = math.sin(angle) * span * 0.5

        for offset in (-90, -45, 0, 45, 90):
            shift = offset * math.cos(angle + math.pi / 2.0)
            shift_y = offset * math.sin(angle + math.pi / 2.0)
            x1 = cx - dx + shift
            y1 = cy - dy + shift_y
            x2 = cx + dx + shift
            y2 = cy + dy + shift_y
            color = accent if offset == 0 else base_line
            width_px = 3 if offset == 0 else 1
            self.canvas.create_line(x1, y1, x2, y2, fill=color, width=width_px)

    def _draw_cube(self, width: int, height: int, accent: str, canvas_bg: str) -> None:
        rotated = [self._rotate_vertex(x, y, z) for (x, y, z) in VERTICES]
        projected = [self._project(x, y, z, width, height) for (x, y, z) in rotated]

        for i, j in EDGES:
            x1, y1 = projected[i]
            x2, y2 = projected[j]
            z_avg = (rotated[i][2] + rotated[j][2]) * 0.5
            t = clamp((z_avg + 2.0) / 4.0, 0.0, 1.0)
            color = mix_hex(mix_hex(canvas_bg, "#ffffff", 0.10), accent, 0.35 + (0.65 * t))
            self.canvas.create_line(x1, y1, x2, y2, fill=color, width=2.5 + (1.6 * t))

        front_face = [projected[idx] for idx in (4, 5, 6, 7)]
        self.canvas.create_polygon(
            *[coord for point in front_face for coord in point],
            fill=mix_hex(canvas_bg, accent, 0.16),
            outline="",
        )

    def _draw_compass(self, width: int, height: int, accent: str, base_line: str) -> None:
        cx = width * 0.5
        cy = height * 0.40
        radius = min(width * 0.28, 220.0)
        self.canvas.create_arc(
            cx - radius,
            cy - radius,
            cx + radius,
            cy + radius,
            start=210,
            extent=120,
            outline=base_line,
            style="arc",
            width=3,
        )

        yaw_angle = math.radians(self.yaw_deg - 90.0)
        tip_x = cx + (math.cos(yaw_angle) * radius)
        tip_y = cy + (math.sin(yaw_angle) * radius)
        inner_x = cx + (math.cos(yaw_angle) * (radius - 28.0))
        inner_y = cy + (math.sin(yaw_angle) * (radius - 28.0))
        self.canvas.create_line(inner_x, inner_y, tip_x, tip_y, fill=accent, width=4)
        self.canvas.create_oval(tip_x - 8, tip_y - 8, tip_x + 8, tip_y + 8, fill=accent, outline="")

    def _draw_action_overlay(self, width: int, height: int, accent: str, canvas_bg: str) -> None:
        style = self._current_style()
        age = time.monotonic() - self.action_changed_at
        pulse = clamp(1.0 - (age / 0.45), 0.0, 1.0)
        cx = width * 0.5
        cy = height * 0.64
        box_w = min(420.0, width * 0.58)
        box_h = 86.0

        fill = mix_hex(canvas_bg, style["panel"], 0.58)
        outline = mix_hex(accent, "#ffffff", 0.22 + (0.35 * pulse))
        self.canvas.create_rectangle(
            cx - (box_w / 2.0),
            cy - (box_h / 2.0),
            cx + (box_w / 2.0),
            cy + (box_h / 2.0),
            fill=fill,
            outline=outline,
            width=2 + int(3 * pulse),
        )
        self.canvas.create_text(
            cx,
            cy - 17,
            text=style["gesture"],
            fill="#ffffff",
            font=("Avenir Next", 22, "bold"),
        )
        self.canvas.create_text(
            cx,
            cy + 19,
            text=self.binding_vars[self.action_name].get(),
            fill=mix_hex("#c8d7e8", accent, 0.55),
            font=("Trebuchet MS", 13, "bold"),
        )

    def _draw_timeline(self, width: int, height: int, accent: str) -> None:
        chart_left = 40
        chart_right = width - 40
        chart_top = height - 170
        chart_bottom = height - 36
        mid_y = (chart_top + chart_bottom) / 2.0

        self.canvas.create_rectangle(
            chart_left,
            chart_top,
            chart_right,
            chart_bottom,
            fill="#08131c",
            outline="#183243",
        )
        self.canvas.create_line(chart_left + 10, mid_y, chart_right - 10, mid_y, fill="#173041")

        if len(self.samples) < 2:
            return

        samples = self.samples[-90:]
        self._plot_series(samples, chart_left, chart_right, mid_y, 48.0, "roll", 90.0, "#ffae57")
        self._plot_series(samples, chart_left, chart_right, mid_y, 48.0, "pitch", 90.0, "#66efb4")
        self._plot_series(samples, chart_left, chart_right, mid_y, 48.0, "yaw", 180.0, accent)

    def _plot_series(
        self,
        samples: list[MotionPacket],
        left: float,
        right: float,
        mid_y: float,
        amplitude: float,
        axis: str,
        max_abs: float,
        color: str,
    ) -> None:
        points: list[float] = []
        sample_count = len(samples)
        usable_width = right - left - 20.0
        for idx, sample in enumerate(samples):
            value = getattr(sample, axis)
            x = left + 10.0 + (usable_width * idx / max(1, sample_count - 1))
            y = mid_y - ((clamp(value, -max_abs, max_abs) / max_abs) * amplitude)
            points.extend((x, y))
        self.canvas.create_line(points, fill=color, width=2.0, smooth=True)

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
        scale = min(width, height) * 0.25
        depth = z + distance
        if depth < 0.1:
            depth = 0.1
        px = (x * scale / depth) + (width / 2.0)
        py = (-y * scale / depth) + (height * 0.42)
        return px, py

    def _led_state_for_action(self, action: str, now_ms: int) -> bool:
        if action == "NEUTRAL":
            return False
        if action == "ROLL_POSITIVE":
            return ((now_ms // 180) % 2) == 0
        if action == "ROLL_NEGATIVE":
            return ((now_ms // 700) % 2) == 0
        if action == "PITCH_UP":
            return True
        if action == "PITCH_DOWN":
            phase_ms = now_ms % 1100
            return phase_ms < 80 or (160 <= phase_ms < 240)
        if action == "TWIST_POSITIVE":
            return (now_ms % 200) < 60
        if action == "TWIST_NEGATIVE":
            phase_ms = now_ms % 300
            return phase_ms < 50 or (100 <= phase_ms < 150) or (200 <= phase_ms < 250)
        return False

    def _close(self) -> None:
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        self.camera_enabled = False
        self.camera_stop_event.set()
        if self.gesture_recognizer is not None:
            try:
                self.gesture_recognizer.close()
            except Exception:
                pass
            self.gesture_recognizer = None
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Display ESP32 MPU6050 orientation as a richer desktop dashboard."
    )
    parser.add_argument("--port", default="/dev/ttyUSB0", help="Serial port (default: /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--show-raw", action="store_true", help="Print raw serial lines to the terminal")
    parser.add_argument("--camera-index", type=int, default=0, help="OpenCV camera index for fist clicks (default: 0)")
    parser.add_argument(
        "--auto-close-seconds",
        type=float,
        default=0.0,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    if serial is None:
        print("pyserial is not available. Install pyserial to read ESP32 telemetry.")
    elif not os.path.exists(args.port):
        print(f"Serial port {args.port} was not found. Waiting for the live device.")

    app = RotationWindow(
        serial_port=args.port,
        baud=args.baud,
        show_raw=args.show_raw,
        auto_close_seconds=args.auto_close_seconds,
        camera_index=args.camera_index,
    )
    app.run()


if __name__ == "__main__":
    main()
