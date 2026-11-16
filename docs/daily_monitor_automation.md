# Vận hành dashboard tự động từ 16/11/2026

**Mở rộng theo yêu cầu mới cùng ngày:** khi `market_enabled=true`, lịch giữ luồng detector ba mã đã nghiệm thu và bổ sung quan sát toàn bộ danh mục HOSE/HNX/UPCOM. Ngày cũ có gói hiển thị mã hóa riêng được dùng lại; chỉ công bố gói ngày mới/còn thiếu và bootstrap catalog. Render tải ngày theo yêu cầu, cache tối đa hai ngày. Những đoạn “đúng ba mã” bên dưới là phạm vi gói detector trước mở rộng, không còn là giới hạn danh mục được xem. Xem [hướng dẫn toàn thị trường](market_monitor.md).

Dashboard hiện tại: https://neo-liquiditylab-monitor.onrender.com/dashboard. Trang chủ giới thiệu công khai; dữ liệu có mật khẩu riêng, không cần tài khoản ChatGPT. GitHub Pages là ứng dụng phân tích cũ và giữ bộ mẫu công khai đã được duyệt.

## Hằng ngày cần làm gì?

Giữ máy bật, đăng nhập Windows và có mạng vào **15:15 thứ Hai–thứ Sáu**. Task **LiquidityLab - DNSE Daily Fallback** sẽ gọi `scripts.scheduled_dnse_day`. Không cần tự chạy chuẩn hóa hoặc upload sau mỗi phiên. Nếu lỡ giờ, task kiểm tra tải bù khi đăng nhập lại. Không tắt máy cho đến khi tác vụ hoàn tất; Render có thể cần thêm thời gian build/khôi phục.

Luồng chạy thực tế:

1. Rà ngày đã đóng phiên chưa hoàn thành trong bảy ngày lịch gần nhất; bỏ qua ngày hoàn tất. Ngày nghỉ sàn khai báo trong `config/dnse_local_schedule.json`, trường `excluded_dates`.
2. Tải REST DNSE, giữ raw và tiến độ phân trang. Mã không được nguồn trả dữ liệu vẫn mang nhãn thiếu; không tự đổi thành 0.
3. Chuẩn hóa từng ngày bằng SQLite tạm, audit với raw, kiểm tra tài chính, ghi Parquet và đối chiếu toàn bộ. Chỉ cập nhật catalog sau khi đạt.
4. Công bố mẫu đã duyệt lên Pages. Riêng dashboard mới: dựng replay **ACB/FPT/HPG**, áp dụng bằng chứng đơn vị, kiểm tra chất lượng, mã hóa gói, kiểm tra khôi phục rồi upload ciphertext lên GitHub Releases.
5. Tải lại/giải mã gói từ Releases để xác minh; cập nhật tag dữ liệu cho backend Render Free hiện có, giữ nguyên mật khẩu và PostgreSQL. Kiểm tra web READY, ngày mới nhất và chất lượng quote đủ ba mã rồi mới báo hoàn tất.

Lỗi upload không làm mất raw hoặc Parquet đã có. Chạy lại tiếp tục từ checkpoint; nguồn không đổi và lần trước hoàn tất thì không dựng/upload/deploy lại. Thu các ngày mới được thực hiện trước bước công bố; lỗi tải một ngày cũ không chặn thử các ngày mới. Pages và dashboard riêng là hai đích độc lập. Tổng lượt vẫn trả lỗi nếu còn ngày hoặc bước chưa hoàn tất.

## Lệnh kiểm tra và chạy lại

Trong PowerShell tại thư mục dự án:

```powershell
# Chỉ xem ngày còn thiếu; không gọi broker hoặc upload
.\.venv\Scripts\python.exe -m scripts.scheduled_dnse_day --plan

# Chạy cùng luồng như lịch Windows; tiếp tục các phần còn thiếu
.\.venv\Scripts\python.exe -m scripts.scheduled_dnse_day

# Chỉ tiếp tục công bố dashboard từ Parquet đã audit, không tải broker
.\.venv\Scripts\python.exe -m scripts.publish_daily_monitor

Get-ScheduledTaskInfo -TaskName 'LiquidityLab - DNSE Daily Fallback'
```

Không chạy nhiều bản cùng lúc; khóa công việc chống writer chồng nhau. `.env`, bằng chứng đơn vị và khóa mã hóa có trên máy gốc, không có trong repo public. Clone repo không đủ để chạy luồng riêng này.

Kết quả kiểm tra:

- `report/automation/dnse_local_schedule_result.json`: ngày đã thử, trạng thái, bước Pages còn chờ và kết quả dashboard riêng.
- `report/automation/monitor_daily_publication.json`: fingerprint nguồn, bước đang làm, tag Releases, trạng thái `COMPLETED` hoặc `FAILED_RESUMABLE`. Trường `already_current=true` khi gọi lại dữ liệu không đổi.
- `report/dnse_update_YYYY-MM-DD_day.json`: kết quả tải/chuẩn hóa/audit từng ngày.
- `report/online_monitor_preparation_auto_*.json`: số replay, ngày, upload và kiểm tra khôi phục từ xa.

Chính sách dashboard riêng ở `config/monitor_publication.json`: chỉ ba mã ACB/FPT/HPG, mã hóa trên Releases; không chuyển API key, `.env`, raw DB hoặc database toàn thị trường lên Render. Chỉ giữ hai staging cache tự tạo gần nhất trên máy; chính sách này không xóa raw, Parquet gốc hay sao lưu. Đặt `enabled=false` để dừng riêng bước công bố dashboard. Để ngừng toàn task, xem `docs/dnse_local_fallback.md`.

## Bảy ngày và những giới hạn cần biết

**Bảy ngày chỉ là phạm vi rà tải bù khi mở máy**, không phải dữ liệu hết hạn, không phải lịch cập nhật danh mục và không tự xóa lịch sử. Nếu nghỉ lâu hơn, cần kiểm tra khả năng trả lịch sử của nhà cung cấp và tải ngày cụ thể bằng `scripts.update_dnse_day --date YYYY-MM-DD --no-export --no-publish-web`, sau đó chạy lại `scripts.publish_daily_monitor`. Không cam kết nguồn vẫn trả đủ mọi ngày quá khứ. Làm mới danh mục theo runbook broker khi cần; không tự đổi danh mục hoặc mở rộng phạm vi cloud ba mã.

Thu cuối ngày từ REST là **lịch sử đã lưu**, không thay thế stream realtime. SSI trên GitHub vẫn có lịch sáng/chiều và relay sang `/realtime`, nhưng GitHub có thể phát lịch trễ. Ngày 16/11 sự kiện sáng đến 15:32; chưa đủ bằng chứng nghiệm thu một phiên live trọn vẹn hay cờ LO. Máy cá nhân vẫn cần cho luồng REST cuối ngày này.

Quote/sổ lệnh và giao dịch khớp là hai loại dữ liệu. Gói quote mới không tự sinh bubbles. Ngày 12, 13 và 16/11 chưa có gói trades DNSE; không suy ra giao dịch từ thay đổi dư mua/bán, không dùng 0 thay phần thiếu. Nhãn chất lượng trade cũ được giữ, không nâng theo quote.

Render Free có cold start/ngủ; giao diện thử lại GET hữu hạn và hiển thị phục hồi, không bảo đảm phục vụ liên tục. Ghi chú lưu PostgreSQL Free hiện hết hạn **15/12/2026**; cần xuất `/api/review-history` có xác thực hoặc chuyển kho trước hạn. Gói thị trường trên Releases không sao lưu ghi chú PostgreSQL. Không tự nâng gói trả phí.

## Dữ liệu cũ

Dashboard đã đọc Parquet. Đã xóa các bản JSON dẫn xuất trùng khoảng **798,5 MB** ngày 12/11. SQLite dẫn xuất `data/processed/dnse/normalized.db` **19.846.524.928 byte** cùng WAL/SHM đã xóa ngày 16/11 sau khi người sở hữu chấp thuận; tổng hai đợt giải phóng khoảng **20,65 GB** bản dẫn xuất trùng.

Đã đối chiếu toàn bộ **11.354.800 snapshot**, **12.685.823 liên kết nguồn** và metadata của đúng 12 ngày SQLite cũ với Parquet; kiểm tra bản sao gốc và khôi phục phần bổ sung ngày 12/11. Bằng chứng: `report/legacy_sqlite_removal_eligibility.json` và `report/legacy_delta_restore_check.json`. Catalog hiện có thêm ngày mới không còn khiến phép kiểm tra nhầm tổng 14 ngày với database 12 ngày. Raw, Parquet và backup giữ nguyên. Kết quả kiểm tra đọc sau xóa nằm ở `report/legacy_sqlite_post_removal_check.json`.
