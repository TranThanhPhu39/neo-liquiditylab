# Dashboard online: Render + GitHub SSI

## Kiến trúc

Frontend và API dashboard cùng origin trên Render. Tài khoản xem là `liquiditylab`; mật khẩu riêng ở `MONITOR_WEB_PASSWORD`. Không cần đăng nhập ChatGPT. Mọi dữ liệu/giao diện riêng được bảo vệ; chỉ `/healthz` công khai và không trả thông tin thị trường. Không tự công khai dữ liệu SSI hoặc toàn bộ dữ liệu lịch sử lên Pages.

GitHub tiếp tục giữ `SSI_API_KEY`/`SSI_API_SECRET` và chạy lịch sáng/chiều. Collector chuyển bản ghi hiện có qua HTTPS theo batch khoảng 5 giây; không dùng thay đổi sổ lệnh để suy ra giao dịch khớp. Backend không có khóa SSI và không tự mở thêm kết nối môi giới. Giao diện báo riêng dữ liệu cũ/khoảng ngắt; không gọi đường truyền này là tick-by-tick trực tiếp. Hai biến relay chưa cấu hình thì bộ thu GitHub giữ hành vi cũ.

SSI raw/canonical và Parquet đầy đủ vẫn được checkpoint mã hóa lên GitHub Releases. Backend khôi phục tối đa sáu capture gần nhất từ ciphertext khi khởi động. Quote/trade ghi đến đâu thì hiển thị đến đó; không coi phần chưa chuyển tới web là khối lượng bằng 0. Checkpoint quá 64 MiB không được âm thầm tải một phần.

Gói bootstrap riêng chứa toàn bộ quan sát đã lưu của ACB/FPT/HPG cho các ngày có replay, các replay Parquet, giao dịch khớp và bằng chứng audit. Không đưa toàn bộ thị trường, `.env`, mật khẩu, API key, raw history DB hoặc database local vào image. Gói mã hóa AES-GCM, checksum từng file và kiểm tra đường dẫn trước khi khôi phục. Chỉ private backend giữ khóa giải mã, không chuyển khóa ra browser.

Ghi chú dùng PostgreSQL. Nếu chưa có database bền vững, nút lưu trả lỗi rõ ràng thay vì giả vờ lưu vào đĩa tạm. Bản cloud không cho người xem bấm bật/tắt bộ thu GitHub.

## Tài nguyên miễn phí và giới hạn

Blueprint `render.yaml` chỉ chọn Free cho một Web Service và một PostgreSQL. [Render Free](https://render.com/docs/free) có thể ngủ sau 15 phút không có request và mất filesystem khi ngủ/restart. Khi mở lại có thể chờ khoảng một phút rồi khôi phục dữ liệu mã hóa. Đây là cấu hình đồ án/demo, không cam kết vận hành liên tục.

PostgreSQL Free hết hạn sau 30 ngày theo tài liệu Render; cần xuất ghi chú/chuyển kho trước hạn. Free có giới hạn giờ, build và bandwidth; không tự nâng lên gói trả phí. Kiểm tra plan và số tiền hiển thị trong tài khoản trước khi tạo.

## Triển khai

1. Kết nối Render. Dùng repo `TranThanhPhu39/neo-liquiditylab`, Blueprint `render.yaml`, chỉ plan Free.
2. Tạo gói private: `.\.venv\Scripts\python.exe -m scripts.prepare_monitor_web_seed`. Gói staging nằm trong `data/control/web-seed/`, được gitignore.
3. Mã hóa, kiểm tra khôi phục rồi lưu ciphertext thành Release `monitor-web-seed-*`. Không upload staging plaintext.
4. Cấu hình `LIQUIDITYLAB_STATE_KEY` bằng khóa khôi phục có sẵn; `MONITOR_SEED_TAG` là tag ciphertext. Không tạo/đổi khóa mã hóa cũ. Mật khẩu web và token ingest do Render tạo; không nhập API key SSI vào Render.
5. Lưu URL origin vào Actions Variable `SSI_MONITOR_URL`; token ingest vào Actions Secret `SSI_MONITOR_INGEST_TOKEN`. URL phải HTTPS, không chứa user/password/query.
6. Kiểm tra `/healthz`, xác thực web, dữ liệu lịch sử, replay, bubbles, ghi chú và khôi phục; thử relay từ runner. Chỉ công bố link khi deployment thực sự chạy.

Khóa và mật khẩu không gửi vào chat, không commit vào Git và không log. Giao diện public Pages cũ vẫn được giữ nguyên; có thể thêm link sang dashboard mới sau khi URL đã xác minh.

## Chạy thử trên máy

Giữ server local cũ bằng `scripts.serve_orderbook_monitor`; cloud server là entry point riêng `scripts.serve_monitor_cloud`. Cloud mode cần `MONITOR_PUBLIC_ORIGIN` (HTTPS; localhost HTTP chỉ phục vụ thử), `MONITOR_WEB_PASSWORD` dài ít nhất 24 ký tự, `MONITOR_INGEST_TOKEN` dài ít nhất 32 ký tự và tùy chọn `MONITOR_DATABASE_URL`. Health check không chứa dữ liệu nhạy cảm.
