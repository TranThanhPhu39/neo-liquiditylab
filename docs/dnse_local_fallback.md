# Lịch DNSE dự phòng trên Windows

Người dùng chọn bật ngày 05/11/2026. Task **LiquidityLab - DNSE Daily Fallback** chạy 15:15 giờ Việt Nam từ thứ Hai đến thứ Sáu, thêm lượt kiểm tra khi người dùng hiện tại đăng nhập. `StartWhenAvailable` và `WakeToRun` được bật; không lưu mật khẩu Windows. Máy phải bật và người dùng phải đăng nhập để chạy. Nếu tắt máy lúc 15:15, lần đăng nhập sau sẽ kiểm tra và tải bù ngày đã đóng phiên.

Luồng: REST toàn bộ cổ phiếu G1 → kiểm tra raw/phân trang → chuẩn hóa → audit → kiểm tra tài chính → xuất/công bố mẫu đã được duyệt lên GitHub Pages. Raw/database đầy đủ vẫn ở máy. Cơ chế công bố trên GitHub runner hiện chưa bật; lịch Windows không phụ thuộc runner kết nối REST DNSE.

`config/dnse_local_schedule.json` bắt đầu từ 05/11, rà tối đa bảy ngày lịch gần nhất. Đây là giới hạn rà bù cục bộ, không phải tuyên bố DNSE chỉ giữ dữ liệu bảy ngày; cũng không phải thời hạn bảy ngày của seed danh mục WS. Ngày có báo cáo COMPLETED, archive đầy đủ và đã có trong danh mục web được bỏ qua. Ngày chạy dở/lỗi được tiếp tục. Hai khóa `dnse_local_schedule.lock` và `dnse_update.lock` ngăn trùng lượt trên cùng máy; Task Scheduler cũng dùng IgnoreNew. Lỗi trả mã khác 0, có hai lượt thử lại cách 15 phút.

Chưa tích hợp lịch nghỉ lễ sàn: thêm ngày nghỉ dạng YYYY-MM-DD vào `excluded_dates` nếu cần. Ngày ngoài khoảng rà bù phải chạy lệnh chỉ định ngày và kiểm tra khả năng phục vụ lịch sử của DNSE.

```powershell
# Xem kế hoạch, không gọi API
.\.venv\Scripts\python.exe -m scripts.scheduled_dnse_day --plan
# Chạy ngay các ngày còn thiếu trong phạm vi rà bù
.\.venv\Scripts\python.exe -m scripts.scheduled_dnse_day
# Xem đăng ký task
.\.venv\Scripts\python.exe -m scripts.install_dnse_day_task --verify-only
# Tải lại một ngày cụ thể, chuẩn hóa và công bố mẫu theo cấu hình đã bật
.\.venv\Scripts\python.exe -m scripts.update_dnse_day --date 2026-11-05 --no-export
```

Kết quả gần nhất: `report/automation/dnse_local_schedule_result.json`; log: `report/automation/logs/dnse_*.log`; từng ngày: `report/dnse_update_YYYY-MM-DD_day.json`. Dừng thu mới bằng tệp `data/control/STOP_DNSE_HISTORY`, hoặc đặt `enabled: false` trong cấu hình. Không gửi khóa vào log/repo.

Lịch GitHub 08:52/12:52 vẫn giữ nguyên. Các lượt 05/11 khởi chạy trễ và tải bù REST thất bại ở TCP trước TLS/xác thực. Lịch Windows là đường dự phòng đã được chọn; không khẳng định đã sửa kết nối REST của GitHub hay khôi phục receipts WS bị lỡ.
