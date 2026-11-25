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
  minShoulder: 0.22,       // độ rộng vai / cạnh khung vuông; ViSL: trung vị 0,33, 5–95% là 0,29–0,42
  maxShoulder: 0.48,       // lớn hơn = quá gần (mất tay và thân), như lượt thử đầu tiên (0,62–0,66)
  // Tư thế nghỉ: chỉ đếm ngược và bắt đầu quay khi hai cổ tay đã hạ thấp hơn vai ít nhất restDepth × độ rộng vai
  // và giữ yên restHoldMs. Đo trên lượt thật: tay chụm trước bụng ≈ 1,1; đang làm ký hiệu thường < 0,5.
  restDepth: 0.8,
  restHoldMs: 500,
  restLostMs: 300,         // trong lúc đếm ngược, tay rời tư thế nghỉ quá lâu thì quay lại bước chờ
  // Loại lượt bị cắt khi tay còn đang làm: khung cuối tay chưa về nghỉ và còn chạy nhanh hơn ngưỡng này
  // (độ rộng vai mỗi khung). Bị cắt: 0,2–0,6; giữ yên tư thế cuối như Nhà, Trường học: < 0,13.
  cutSpeed: 0.15,
  // Clip video gốc của mỗi lượt (toàn khung camera, thường 1280×720, không cắt, không lật), lưu kèm 30 ảnh trong zip.
  // Bắt đầu preMs trước khi khung chuyển đỏ, dừng postMs sau khung cuối: ≈ 4,5 giây, ~1,1 MB ở 2 Mbps.
  // Dùng để sau này cắt lại cửa sổ thời gian, lấy toàn khung hoặc độ phân giải cao hơn khi trích keypoints.
  video: { enabled: true, bitrate: 2_000_000, preMs: 1000, postMs: 1000 },
  quota: { 1: 3, 2: 2 },   // SESSION_QUOTA: số lượt mỗi người cho mỗi ký hiệu
  size: 480,               // cạnh ảnh vuông lưu lại (px)
  jpegQuality: 0.9,
};
