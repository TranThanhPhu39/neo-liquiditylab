# Dashboard online: Render + GitHub SSI

## Truy cập bản đã triển khai ngày 15/11/2026

- Lịch sử, heatmap, replay và rà soát: **https://neo-liquiditylab-monitor.onrender.com/**.
- SSI live và phát lại capture SSI: **https://neo-liquiditylab-monitor.onrender.com/realtime**.
- Tên đăng nhập: `liquiditylab`. Mật khẩu tự sinh được lưu trên máy trong `.env` ở `MONITOR_WEB_PASSWORD`. Có thể tìm tên biến bằng Ctrl+F trong IDE; không chia sẻ `.env` hoặc gửi mật khẩu vào chat. Không cần tài khoản ChatGPT.
- Pages cũ vẫn ở `https://tranthanhphu39.github.io/neo-liquiditylab/`; link Render là bản giám sát sổ lệnh mới.

Gói lịch sử được người sở hữu cho phép gồm 34 replay gốc, 11 ngày ACB/FPT/HPG đến 11/11/2026 và 694.905 quan sát, không phải database toàn thị trường. Đã upload ciphertext 156 MB lên Releases, tải lại, giải mã và kiểm tra checksum. Có thể có thêm phiên bản phân tích được lưu trên web. Bộ phục hồi dùng đường tải public CDN và feed Atom của Releases; không phụ thuộc quota REST anonymous dùng chung trên Render. Giải mã AES-GCM và kiểm tra checksum vẫn bắt buộc trước khi cài dữ liệu.

Lịch GitHub SSI vẫn bật: launcher 08:37/12:37, khung thu 08:50–11:35 và 12:45–15:10 giờ Việt Nam từ thứ Hai đến thứ Sáu. Các giờ này là cửa sổ vận hành; detector vẫn kiểm tra phiên, đơn vị và chất lượng riêng. Actions Variable `SSI_MONITOR_URL` và Actions Secret `SSI_MONITOR_INGEST_TOKEN` đã nối vào backend. Không cần mở máy để lịch này chạy. GitHub có thể trễ lịch; mất kết nối/cửa sổ bị lỡ phải được hiển thị, không tự coi là dữ liệu đầy đủ.

Phép thử [runner GitHub → Render](https://github.com/TranThanhPhu39/neo-liquiditylab/actions/runs/43711033959) dùng 185 bản ghi thật của capture ngày 13/11; không gọi broker và không chứng minh thị trường đang phát dữ liệu mới. Ngày triển khai là Chủ nhật, nên cần quan sát phiên giao dịch kế tiếp để nghiệm thu live và độ trễ thực tế. Kiểm tra HTTP, lịch sử Parquet và dữ liệu bubbles đã đạt; công cụ điều khiển trình duyệt của phiên triển khai chưa hoạt động nên chưa nghiệm thu hình thức bằng browser thật.

Khi sử dụng: mở trang, đăng nhập, chọn ngày/mã rồi xem replay. Sang trang SSI để xem capture đã lưu hoặc chờ bộ thu GitHub. Ngoài cửa sổ thu, dữ liệu là bản ghi cũ và có nhãn stale; không gọi là live. Các nút bật/tắt bộ thu tại trình duyệt không điều khiển Actions. Sau khi Render ngủ/khởi động lại, chờ trang phục hồi dữ liệu trước khi thao tác.

PostgreSQL Free của lần triển khai này hết hạn **15/12/2026**. Trước hạn cần xuất ghi chú/cấu hình từ API có xác thực `/api/review-history` hoặc chuyển kho lưu. Gói lịch sử/SSI mã hóa trên GitHub không thay thế bản sao ghi chú PostgreSQL. Không có nâng cấp trả phí tự động trong cấu hình ứng dụng.

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

Khi dùng CLI thay cho Blueprint, tạo cùng hai tài nguyên Free/tên/region như cấu hình. `Dockerfile` ở root hỗ trợ default của CLI. Các giá trị riêng có thể nạp bằng secret file `monitor.env` và env công khai `MONITOR_CONFIG_FILE=/etc/secrets/monitor.env`; app chỉ đọc năm khóa cấu hình đã cho phép, không nạp khóa SSI. Không truyền giá trị bí mật qua tham số `--env-var` trên command line. Trong cách này cấu hình live được CLI quản lý, chưa có Blueprint sync tự động.

## Chạy thử trên máy

Giữ server local cũ bằng `scripts.serve_orderbook_monitor`; cloud server là entry point riêng `scripts.serve_monitor_cloud`. Cloud mode cần `MONITOR_PUBLIC_ORIGIN` (HTTPS; localhost HTTP chỉ phục vụ thử), `MONITOR_WEB_PASSWORD` dài ít nhất 24 ký tự, `MONITOR_INGEST_TOKEN` dài ít nhất 32 ký tự và tùy chọn `MONITOR_DATABASE_URL`. Health check không chứa dữ liệu nhạy cảm.
