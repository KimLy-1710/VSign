// Cấu hình web app thu thập dữ liệu. Giữ khớp với collect_data.py / keypoints.py.
export const CONFIG = {
  // URL Web app của Google Apps Script (Deploy → Web app), dạng https://script.google.com/macros/s/.../exec
  endpoint: "https://script.google.com/macros/s/AKfycbwydAzEEFevZpXbepxSBIAuma0TkpBZV95rkGzDm3KevZ0uRzIofQiPKvwmkT4uBUWT/exec",
  frames: 30,              // keypoints.SEQUENCE_LENGTH
  fps: 12,                 // keypoints.CAPTURE_FPS: 30 khung ≈ 2,5 giây, khớp tập ViSL
  minFps: 9,               // máy quá chậm thì chuỗi bị kéo dài, khác nhịp tập ViSL → loại
  countdown: 2,            // giây chuẩn bị trước mỗi lượt
  minHandRatio: 0.5,       // cùng ngưỡng với collect_data.py
  minPoseRatio: 0.8,
  quota: { 1: 3, 2: 2 },   // SESSION_QUOTA: số lượt mỗi người cho mỗi ký hiệu
  size: 480,               // cạnh ảnh vuông lưu lại (px)
  jpegQuality: 0.9,
};
