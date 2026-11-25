/**
 * VSign — điểm nhận dữ liệu cho web app thu thập (Google Apps Script).
 *
 * Chạy dưới tài khoản Google của người triển khai: mọi lượt quay được lưu vào MỘT thư mục Drive
 * của người đó, chia theo người quay và buổi quay:
 *     <FOLDER_ID>/<người>/buoi1/<tên lượt>.zip
 *     <FOLDER_ID>/<người>/buoi2/<tên lượt>.zip
 * Thư mục con được tạo tự động. File cũ nằm thẳng ở thư mục gốc (trước khi chia thư mục) vẫn được đếm và xóa được.
 * Thành viên không cần đăng nhập hay nhập mã gì.
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
 *   {action: "upload", name, meta, frames: [base64 JPEG × 30],
 *    video?: base64, video_type?: "video/webm" | "video/mp4"}  → {ok, name, video_saved}
 *     Clip video gốc (từ VERSION 6) được lưu chung zip thành video.webm / video.mp4. Không có video vẫn nhận như cũ.
 *   {action: "list", signer, session}                           → {ok, names: [...]}
 *   {action: "delete", name}                                    → {ok}   (chỉ lượt tải lên chưa quá DELETE_WINDOW_MIN phút)
 */

var VERSION = 6;   // 6: nhận clip video gốc kèm mỗi lượt
var NAME_RE = /^([a-z0-9]+)_s([12])_\d{2}_[a-z0-9_]+_\d{8}-\d{6}-\d{3}$/;   // nhóm 1: người quay, nhóm 2: buổi
var FRAMES = 30;
var MAX_FRAME_B64 = 400000;     // ~300 KB mỗi ảnh; ảnh 480×480 của web app thường 20–60 KB
var DELETE_WINDOW_MIN = 15;
var MAX_VIDEO_B64 = 16000000;   // ~12 MB; clip 4,5 giây ở 2 Mbps thường ~1,1 MB
var VIDEO_EXT = {"video/webm": "webm", "video/mp4": "mp4"};

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
  var video = null;
  if (req.video != null) {
    var ext = VIDEO_EXT[req.video_type];
    if (!ext || typeof req.video !== "string" || req.video.length > MAX_VIDEO_B64) return {ok: false, error: "Video không hợp lệ"};
    var vb = Utilities.base64Decode(req.video);
    // WebM bắt đầu bằng 1A 45 DF A3 (EBML); MP4 có "ftyp" ở byte 4–7. Bytes trong Apps Script là số có dấu.
    var isWebm = vb.length > 4 && vb[0] === 0x1A && vb[1] === 0x45 && vb[2] === -33 && vb[3] === -93;
    var isMp4 = vb.length > 8 && String.fromCharCode(vb[4], vb[5], vb[6], vb[7]) === "ftyp";
    if (!(ext === "webm" ? isWebm : isMp4)) return {ok: false, error: "Video không hợp lệ"};
    video = Utilities.newBlob(vb, req.video_type, "video." + ext);
  }
  var zipName = req.name + ".zip";
  if (findTake(folder, req.name)) return {ok: true, name: zipName, duplicate: true, video_saved: !!video};  // gửi lại sau lỗi mạng
  var blobs = req.frames.map(function (b64, i) {
    var bytes = Utilities.base64Decode(b64);
    if (bytes.length < 3 || bytes[0] !== -1 || bytes[1] !== -40) throw new Error("Ảnh không phải JPEG");  // FF D8
    return Utilities.newBlob(bytes, "image/jpeg", ("0" + i).slice(-2) + ".jpg");
  });
  blobs.push(Utilities.newBlob(JSON.stringify(req.meta || {}, null, 2), "application/json", "meta.json"));
  if (video) blobs.push(video);
  var m = req.name.match(NAME_RE);
  takeFolder(folder, m[1], m[2], true).createFile(Utilities.zip(blobs, zipName));
  return {ok: true, name: zipName, video_saved: !!video};
}

function list(folder, req) {
  if (!/^[a-z0-9]+$/.test(String(req.signer || "")) || [1, 2].indexOf(Number(req.session)) < 0) return {ok: false, error: "Tham số không hợp lệ"};
  var prefix = req.signer + "_s" + Number(req.session) + "_";
  var names = [];
  var sub = takeFolder(folder, req.signer, Number(req.session), false);
  [sub, folder].forEach(function (f) {   // thư mục của người đó + file cũ ở thư mục gốc
    if (!f) return;
    var it = f.getFiles();
    while (it.hasNext()) {
      var n = it.next().getName();
      if (n.indexOf(prefix) === 0 && /\.zip$/.test(n) && names.indexOf(n) < 0) names.push(n);
    }
  });
  return {ok: true, names: names};
}

function remove(folder, req) {
  var base = String(req.name || "").replace(/\.zip$/, "");
  if (!NAME_RE.test(base)) return {ok: false, error: "Tên file không hợp lệ"};
  var file = findTake(folder, base);
  if (!file) return {ok: false, error: "Không tìm thấy file"};
  if (Date.now() - file.getDateCreated().getTime() > DELETE_WINDOW_MIN * 60 * 1000) {
    return {ok: false, error: "Chỉ xóa được lượt vừa quay trong " + DELETE_WINDOW_MIN + " phút"};
  }
  file.setTrashed(true);   // vào thùng rác Drive, khôi phục được trong 30 ngày
  return {ok: true};
}

// Thư mục con theo tên; create=true thì tạo nếu chưa có. Khóa script để hai lượt tải lên cùng lúc
// không tạo ra hai thư mục trùng tên (Drive cho phép trùng tên).
function childFolder(parent, name, create) {
  var it = parent.getFoldersByName(name);
  if (it.hasNext()) return it.next();
  if (!create) return null;
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    it = parent.getFoldersByName(name);   // kiểm tra lại sau khi có khóa
    return it.hasNext() ? it.next() : parent.createFolder(name);
  } finally {
    lock.releaseLock();
  }
}

function takeFolder(root, signer, session, create) {
  var person = childFolder(root, signer, create);
  return person ? childFolder(person, "buoi" + session, create) : null;
}

// Tìm file của một lượt: trong <người>/buoi<n>/, sau đó ở thư mục gốc (file tải lên trước khi chia thư mục).
function findTake(root, base) {
  var m = base.match(NAME_RE);
  var sub = takeFolder(root, m[1], m[2], false);
  var it = sub ? sub.getFilesByName(base + ".zip") : null;
  if (it && it.hasNext()) return it.next();
  it = root.getFilesByName(base + ".zip");
  return it.hasNext() ? it.next() : null;
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
