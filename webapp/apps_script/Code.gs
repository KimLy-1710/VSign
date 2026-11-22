/**
 * VSign — điểm nhận dữ liệu cho web app thu thập (Google Apps Script).
 *
 * Chạy dưới tài khoản Google của người triển khai: mọi lượt quay được lưu vào MỘT thư mục Drive
 * của người đó. Thành viên không cần đăng nhập hay nhập mã gì.
 *
 * Không có mã nhóm nên ai biết URL cũng gọi được. Bù lại, script chỉ nhận đúng định dạng một lượt quay
 * (tên file, 30 ảnh JPEG, giới hạn kích thước) và chỉ cho xóa lượt vừa tải lên trong DELETE_WINDOW_MIN phút,
 * nên dữ liệu cũ không thể bị xóa từ bên ngoài.
 *
 * Cài đặt (xem webapp/README.md):
 *   Project Settings → Script properties:
 *     FOLDER_ID  id thư mục Drive nhận dữ liệu (phần cuối URL của thư mục)
 *   Deploy → New deployment → Web app: Execute as "Me", Who has access "Anyone".
 *
 * API (POST, body là JSON dạng text/plain để trình duyệt không gửi preflight CORS):
 *   {action: "upload", name, meta, frames: [base64 JPEG × 30]}  → {ok, name}
 *   {action: "list", signer, session}                           → {ok, names: [...]}
 *   {action: "delete", name}                                    → {ok}   (chỉ lượt tải lên chưa quá DELETE_WINDOW_MIN phút)
 */

var VERSION = 4;
var NAME_RE = /^[a-z0-9]+_s[12]_\d{2}_[a-z0-9_]+_\d{8}-\d{6}-\d{3}$/;
var FRAMES = 30;
var MAX_FRAME_B64 = 400000;     // ~300 KB mỗi ảnh; ảnh 480×480 của web app thường 20–60 KB
var DELETE_WINDOW_MIN = 15;

function doPost(e) {
  try {
    var req = JSON.parse(e.postData.contents);
    var folderId = (PropertiesService.getScriptProperties().getProperty("FOLDER_ID") || "").trim();
    if (!folderId) return reply({ok: false, error: "Apps Script chưa có thuộc tính FOLDER_ID (Project Settings → Script properties)"});
    var folder = DriveApp.getFolderById(folderId);
    if (req.action === "upload") return reply(upload(folder, req));
    if (req.action === "list") return reply(list(folder, req));
    if (req.action === "delete") return reply(remove(folder, req));
    return reply({ok: false, error: "action không hợp lệ"});
  } catch (err) {
    return reply({ok: false, error: String(err)});
  }
}

function doGet() {
  // Mở URL /exec trên trình duyệt để kiểm tra cấu hình. Chỉ in true/false và số lượng, không in tên hay giá trị thuộc tính.
  var props = PropertiesService.getScriptProperties();
  var others = props.getKeys().filter(function (k) { return k !== "FOLDER_ID"; }).length;
  return reply({ok: true, service: "vsign-collect", version: VERSION,
    has_folder_id: !!(props.getProperty("FOLDER_ID") || "").trim(),
    other_properties: others});   // > 0: thuộc tính thừa (ví dụ TOKEN cũ), xóa được
}

function upload(folder, req) {
  if (!NAME_RE.test(req.name || "")) return {ok: false, error: "Tên file không hợp lệ"};
  if (!req.frames || req.frames.length !== FRAMES) return {ok: false, error: "Cần đúng " + FRAMES + " khung"};
  for (var i = 0; i < req.frames.length; i++) {
    if (typeof req.frames[i] !== "string" || req.frames[i].length > MAX_FRAME_B64) return {ok: false, error: "Ảnh không hợp lệ"};
  }
  var zipName = req.name + ".zip";
  if (folder.getFilesByName(zipName).hasNext()) return {ok: true, name: zipName, duplicate: true};  // gửi lại sau lỗi mạng
  var blobs = req.frames.map(function (b64, i) {
    var bytes = Utilities.base64Decode(b64);
    if (bytes.length < 3 || bytes[0] !== -1 || bytes[1] !== -40) throw new Error("Ảnh không phải JPEG");  // FF D8
    return Utilities.newBlob(bytes, "image/jpeg", ("0" + i).slice(-2) + ".jpg");
  });
  blobs.push(Utilities.newBlob(JSON.stringify(req.meta || {}, null, 2), "application/json", "meta.json"));
  folder.createFile(Utilities.zip(blobs, zipName));
  return {ok: true, name: zipName};
}

function list(folder, req) {
  if (!/^[a-z0-9]+$/.test(String(req.signer || "")) || [1, 2].indexOf(Number(req.session)) < 0) return {ok: false, error: "Tham số không hợp lệ"};
  var prefix = req.signer + "_s" + Number(req.session) + "_";
  var names = [];
  var it = folder.getFiles();
  while (it.hasNext()) {
    var n = it.next().getName();
    if (n.indexOf(prefix) === 0 && /\.zip$/.test(n)) names.push(n);
  }
  return {ok: true, names: names};
}

function remove(folder, req) {
  var base = String(req.name || "").replace(/\.zip$/, "");
  if (!NAME_RE.test(base)) return {ok: false, error: "Tên file không hợp lệ"};
  var it = folder.getFilesByName(base + ".zip");
  if (!it.hasNext()) return {ok: false, error: "Không tìm thấy file"};
  var file = it.next();
  if (Date.now() - file.getDateCreated().getTime() > DELETE_WINDOW_MIN * 60 * 1000) {
    return {ok: false, error: "Chỉ xóa được lượt vừa quay trong " + DELETE_WINDOW_MIN + " phút"};
  }
  file.setTrashed(true);   // vào thùng rác Drive, khôi phục được trong 30 ngày
  return {ok: true};
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
