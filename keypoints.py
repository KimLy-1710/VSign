"""Trích đặc trưng MediaPipe Holistic dùng chung cho thu thập dữ liệu và suy luận.

Mỗi khung hình cho một vector 1662 số, thứ tự cố định:
    pose  33 điểm × (x, y, z, visibility) = 132
    face 468 điểm × (x, y, z)             = 1404
    tay trái  21 điểm × (x, y, z)         = 63
    tay phải  21 điểm × (x, y, z)         = 63
Bộ phận không phát hiện được thì điền 0.

Node camera lúc suy luận phải làm đúng như lúc thu thập: cắt vuông (center_square),
đưa khung CHƯA lật gương vào detect, lấy mẫu CAPTURE_FPS khung/giây, rồi extract_keypoints.
Làm khác đi thì đặc trưng sẽ lệch so với dữ liệu train.
"""
from __future__ import annotations

import os
import urllib.request
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision

N_POSE, N_FACE, N_HAND = 33, 468, 21
POSE_DIM = N_POSE * 4      # 132
FACE_DIM = N_FACE * 3      # 1404
HAND_DIM = N_HAND * 3      # 63
FEATURE_DIM = POSE_DIM + FACE_DIM + 2 * HAND_DIM   # 1662
SEQUENCE_LENGTH = 30
# Tập ViSignLanguage-Video gói một ký hiệu trong 30 khung trải ~2,5 giây (~12 khung/giây).
# Thu thập và suy luận lấy mẫu cùng nhịp này để chuỗi tự quay khớp với dữ liệu công khai.
CAPTURE_FPS = 12.0

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/holistic_landmarker/"
    "holistic_landmarker/float16/latest/holistic_landmarker.task"
)
DEFAULT_MODEL_PATH = Path(__file__).resolve().parent / "models" / "holistic_landmarker.task"


def ensure_model(model_path: Path = DEFAULT_MODEL_PATH) -> Path:
    """Trả về đường dẫn file model; tự tải về nếu chưa có (khoảng 13 MB)."""
    model_path = Path(model_path)
    if not model_path.exists():
        model_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Đang tải model MediaPipe Holistic về {model_path} ...")
        tmp = model_path.with_suffix(f".{os.getpid()}.part")    # file tạm riêng: nhiều tiến trình có thể tải cùng lúc
        urllib.request.urlretrieve(MODEL_URL, tmp)
        tmp.replace(model_path)
    return model_path


def create_holistic(model_path: Path = DEFAULT_MODEL_PATH) -> vision.HolisticLandmarker:
    """Tạo bộ trích Holistic ở chế độ VIDEO. Nhớ gọi .close() (hoặc dùng `with`)."""
    options = vision.HolisticLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(ensure_model(model_path))),
        running_mode=vision.RunningMode.VIDEO,
    )
    return vision.HolisticLandmarker.create_from_options(options)


def center_square(frame_bgr: np.ndarray) -> np.ndarray:
    """Cắt phần vuông ở giữa khung hình. Video của tập công khai là 224×224 (vuông); tọa độ MediaPipe
    được chuẩn hóa theo chiều rộng/cao ảnh, nên webcam 4:3 hay 16:9 phải cắt vuông thì mới so sánh được."""
    h, w = frame_bgr.shape[:2]
    s = min(h, w)
    y, x = (h - s) // 2, (w - s) // 2
    return frame_bgr[y:y + s, x:x + s]


def detect(holistic: vision.HolisticLandmarker, frame_bgr: np.ndarray, timestamp_ms: int):
    """Chạy Holistic trên một khung BGR của OpenCV. timestamp_ms phải tăng dần."""
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    return holistic.detect_for_video(image, timestamp_ms)


def _block(landmarks, n_points: int, with_visibility: bool) -> np.ndarray:
    width = 4 if with_visibility else 3
    out = np.zeros((n_points, width), dtype=np.float32)
    if landmarks:
        for i, lm in enumerate(landmarks[:n_points]):
            out[i, 0], out[i, 1], out[i, 2] = lm.x, lm.y, lm.z
            if with_visibility:
                out[i, 3] = lm.visibility or 0.0
    return out.ravel()


def extract_keypoints(result) -> np.ndarray:
    """Kết quả Holistic → vector float32 dài 1662 (pose → face → tay trái → tay phải)."""
    vec = np.concatenate([
        _block(result.pose_landmarks, N_POSE, True),
        _block(result.face_landmarks, N_FACE, False),   # chỉ lấy 468 điểm lưới mặt đầu tiên
        _block(result.left_hand_landmarks, N_HAND, False),
        _block(result.right_hand_landmarks, N_HAND, False),
    ])
    assert vec.shape == (FEATURE_DIM,)
    return vec


def has_hand(result) -> bool:
    return bool(result.left_hand_landmarks or result.right_hand_landmarks)


def has_pose(result) -> bool:
    return bool(result.pose_landmarks)


def has_face(result) -> bool:
    return bool(result.face_landmarks)


_HAND_EDGES = [(c.start, c.end) for c in vision.HandLandmarksConnections.HAND_CONNECTIONS]
_POSE_EDGES = [(c.start, c.end) for c in vision.PoseLandmarksConnections.POSE_LANDMARKS]


def draw_landmarks(frame_bgr: np.ndarray, result) -> None:
    """Vẽ khung xương lên ảnh (tại chỗ) để người quay thấy MediaPipe bắt được gì."""
    h, w = frame_bgr.shape[:2]

    def pts(landmarks):
        return [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]

    def skeleton(landmarks, edges, color):
        p = pts(landmarks)
        for a, b in edges:
            if a < len(p) and b < len(p):
                cv2.line(frame_bgr, p[a], p[b], color, 2, cv2.LINE_AA)
        for q in p:
            cv2.circle(frame_bgr, q, 3, color, -1, cv2.LINE_AA)

    if result.face_landmarks:
        for q in pts(result.face_landmarks[:N_FACE:6]):
            cv2.circle(frame_bgr, q, 1, (200, 200, 200), -1)
    if result.pose_landmarks:
        skeleton(result.pose_landmarks, _POSE_EDGES, (80, 180, 80))
    if result.left_hand_landmarks:
        skeleton(result.left_hand_landmarks, _HAND_EDGES, (60, 120, 255))
    if result.right_hand_landmarks:
        skeleton(result.right_hand_landmarks, _HAND_EDGES, (255, 140, 60))
