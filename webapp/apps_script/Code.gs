/**
 * VSign — điểm nhận dữ liệu cho web app thu thập (Google Apps Script).
 *
 * Chạy dưới tài khoản Google của người triển khai: mọi lượt quay được lưu vào MỘT thư mục Drive
 * của người đó. Thành viên không cần đăng nhập Google; chỉ cần nhập đúng mã nhóm (TOKEN).
 *
 * Cài đặt (xem webapp/README.md):
 *   Project Settings → Script properties:
 *     FOLDER_ID  id thư mục Drive nhận dữ liệu (phần cuối URL của thư mục)
 *     TOKEN      mã nhóm, gửi riêng cho các thành viên (không đưa lên GitHub)
 *   Deploy → New deployment → Web app: Execute as "Me", Who has access "Anyone".
 *
 * API (POST, body là JSON dạng text/plain để trình duyệt không gửi preflight CORS):
 *   {token, action: "upload", name, meta, frames: [base64 JPEG × 30]}  → {ok, name}
 *   {token, action: "list", signer, session}                           → {ok, names: [...]}
 *   {token, action: "delete", name}                                    → {ok}
 */

var NAME_RE = /^[a-z0-9]+_s[12]_\d{2}_[a-z0-9_]+_\d{8}-\d{6}-\d{3}$/;
var FRAMES = 30;

function doPost(e) {
  try {
    var req = JSON.parse(e.postData.contents);
    var props = PropertiesService.getScriptProperties();
    if (!req.token || req.token !== props.getProperty("TOKEN")) return reply({ok: false, error: "Sai mã nhóm"});
    var folder = DriveApp.getFolderById(props.getProperty("FOLDER_ID"));
    if (req.action === "upload") return reply(upload(folder, req));
    if (req.action === "list") return reply(list(folder, req));
    if (req.action === "delete") return reply(remove(folder, req));
    return reply({ok: false, error: "action không hợp lệ"});
  } catch (err) {
    return reply({ok: false, error: String(err)});
  }
}

function doGet() {
  return reply({ok: true, service: "vsign-collect"});
}

function upload(folder, req) {
  if (!NAME_RE.test(req.name || "")) return {ok: false, error: "Tên file không hợp lệ"};
  if (!req.frames || req.frames.length !== FRAMES) return {ok: false, error: "Cần đúng " + FRAMES + " khung"};
  var zipName = req.name + ".zip";
  if (folder.getFilesByName(zipName).hasNext()) return {ok: true, name: zipName, duplicate: true};  // gửi lại sau lỗi mạng
  var blobs = req.frames.map(function (b64, i) {
    return Utilities.newBlob(Utilities.base64Decode(b64), "image/jpeg", ("0" + i).slice(-2) + ".jpg");
  });
  blobs.push(Utilities.newBlob(JSON.stringify(req.meta || {}, null, 2), "application/json", "meta.json"));
  folder.createFile(Utilities.zip(blobs, zipName));
  return {ok: true, name: zipName};
}

function list(folder, req) {
  var prefix = req.signer + "_s" + req.session + "_";
  var names = [];
  var it = folder.getFiles();
  while (it.hasNext()) {
    var n = it.next().getName();
    if (n.indexOf(prefix) === 0 && /\.zip$/.test(n)) names.push(n);
  }
  return {ok: true, names: names};
}

function remove(folder, req) {
  if (!NAME_RE.test((req.name || "").replace(/\.zip$/, ""))) return {ok: false, error: "Tên file không hợp lệ"};
  var it = folder.getFilesByName(req.name.replace(/\.zip$/, "") + ".zip");
  if (!it.hasNext()) return {ok: false, error: "Không tìm thấy file"};
  it.next().setTrashed(true);   // vào thùng rác Drive, khôi phục được trong 30 ngày
  return {ok: true};
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
