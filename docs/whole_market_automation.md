# Thu tự động toàn thị trường trong thời gian chờ NEO

Đã đăng ký Windows Task Scheduler cho tài khoản Windows hiện tại. Tên task: **NEO LiquidityLab - Vnstock Whole Market**. Task chạy bằng quyền Limited, không lưu mật khẩu, không đổi ExecutionPolicy, không mở cửa sổ terminal. Chương trình và `.env` tiếp tục ở thư mục dự án; không di chuyển thư mục nếu chưa đăng ký lại task.

## Phạm vi và nhịp chạy

- Làm mới danh mục mỗi ngày qua `Reference().equity.list_by_exchange(source="vci")`. Chỉ giữ cổ phiếu có exchange HOSE/HNX/UPCOM; không gồm mã DELISTED, ETF, chứng quyền, phái sinh hoặc trái phiếu.
- Lượt kiểm chứng 28/10 trả đủ **1.520/1.520 cổ phiếu**: HOSE 407, HNX 299, UPCOM 814. Danh mục tham chiếu có 234 mã DELISTED được loại có lưu bằng chứng. Số lượng sẽ thay đổi theo ngày.
- Quote qua Vnstock 5.2.1/KBS, nhóm tối đa 100 mã, không gửi đồng thời. Với danh mục hiện tại: 16 nhóm, cách khoảng 11,25 giây, mục tiêu một vòng 180 giây. Không phải 1.520 mã cùng một timestamp. Lượt thử là chẩn đoán sau cửa sổ thu, không cộng vào lịch sử 66 snapshot trong phiên.
- Task bắt đầu **09:10 thứ Hai–thứ Sáu**, thu trong **09:15–11:30 và 13:00–14:30**, chốt sau 14:30. Cửa sổ bảo thủ này không bao phủ toàn bộ giờ giao dịch của từng sàn.
- Có trigger khi đăng nhập để bắt kịp nếu máy mở muộn; ngoài cửa sổ/ngày thường thì bỏ qua. `StartWhenAvailable`, `WakeToRun` bật, nhưng không bảo đảm đánh thức mọi cấu hình máy. **Máy phải bật, tài khoản Windows đăng nhập và có mạng**; task không chạy được khi máy tắt.
- Kiểm tra trạng thái EQUITY/OPEN qua `Reference().market.status()` nguồn builtin trước mỗi vòng; không lấy quote nếu sàn đóng. Đây là lịch/trạng thái builtin của SDK, không phải bằng chứng realtime từ sàn. Có danh sách `holidays` trong cấu hình để bổ sung ngày nghỉ chính thức. Không tự xóa lịch sử hoặc bịa phiên bị bỏ lỡ.

## Quota, lưu trữ và phạm vi chuẩn hóa

`config/market_collection.json` là cấu hình chính. Kế hoạch hiện ước tính khoảng 1.276 lời gọi SDK/ngày đầy đủ, không tính các truy vấn nội bộ auth/policy của SDK. Bộ lập kế hoạch dùng tối đa 75% ngân sách quota hiện có, dự phòng 20 phiên/tháng và tự giãn chu kỳ khi ngân sách thấp; SDK vẫn kiểm tra quota trước từng request. Lỗi quyền/quota/schema/nguồn làm dừng lượt thu, không retry dày hoặc đổi nguồn để né giới hạn. Ngày sau thử một lượt mới theo lịch; lỗi cùng ngày không tự chạy lại khi đăng nhập.

Toàn bộ mã được lưu **raw** theo allowlist với timestamp nhận và source timestamp null. Chỉ FPT, HPG, ACB được chuẩn hóa tự động theo hồ sơ đơn vị đã thẩm định; không mở rộng kết luận thực nghiệm ba mã sang 1.520 mã để đưa vào A1/B5. Muốn mở thêm mã cần thẩm định đơn vị/phạm vi và cập nhật `normalize_symbols` cùng cổng kiểm tra trong code. Các mã thiếu hoặc book không hợp lệ được ghi nhận, không được bù bằng dữ liệu giả.

CSV và báo cáo từng ngày: `report/automation/YYYY-MM-DD/`. Trạng thái hiện tại: `report/automation/latest.json`. Danh mục ngày, trạng thái sàn gần nhất, số vòng đủ/thiếu, mã thiếu của vòng gần nhất, số batch/quan sát và lỗi được ghi riêng. CSV xuất theo luồng, không tải toàn bộ lịch sử thị trường vào RAM.

Backup SQLite đầy đủ được kiểm tra integrity rồi nén gzip tại `data/backups/automation/`; không xóa backup cũ. Tối thiểu 5 GiB trống trước và trong khi thu; thiếu dung lượng thì dừng. Dữ liệu toàn thị trường và backup tăng theo thời gian, cần theo dõi dung lượng. Để phục hồi, giải nén `.db.gz` ra một đường dẫn mới và kiểm tra SQLite trước khi dùng.

## Xem, dừng, chuyển sang NEO

```powershell
# Kiểm tra task đã đăng ký và thời điểm chạy tiếp:
.\.venv\Scripts\python.exe -m scripts.install_collection_task --verify-only

# Kiểm tra danh mục/quota, không lấy quote:
.\.venv\Scripts\python.exe -m scripts.scheduled_collection --preflight

# Dừng thu; chương trình đang chạy kiểm tra cờ ít nhất mỗi 5 giây khi chờ:
New-Item -ItemType Directory -Path data/control -Force | Out-Null
New-Item -ItemType File -Path data/control/STOP_VNSTOCK -Force | Out-Null

# Khi NEO đã được xác minh và sẵn sàng thay thế nguồn:
New-Item -ItemType File -Path data/control/NEO_READY -Force | Out-Null

# Tắt task Windows nếu không cần nữa:
Disable-ScheduledTask -TaskName 'NEO LiquidityLab - Vnstock Whole Market'
```

Có thể đặt `enabled=false` trong cấu hình để ngừng thu. Task Windows vẫn có thể được kích hoạt nhưng chương trình thoát trước request. `NEO_READY` dừng Vnstock; riêng việc có token không tự chứng minh NEO đã tích hợp thành công, nên không tự đổi nguồn chỉ từ sự tồn tại của token.

Task chưa có ngày kết thúc, tiếp tục theo lịch đến khi có cờ dừng/NEO_READY hoặc bị vô hiệu hóa. Task bỏ qua chạy trùng và chương trình có khóa file ở cấp OS. Nếu lỗi đã được xử lý và cần chạy lại cùng ngày, người vận hành dùng `python -m scripts.scheduled_collection --resume-after-failure` trong cửa sổ thu. Không chạy lệnh khôi phục để né hạn mức nguồn.

## Bằng chứng kiểm tra

- `report/market_scope_preflight_2026-10-28.json`: lượt kiểm chứng đủ 16 nhóm/1.520 mã, dữ liệu mẫu ở `data/raw/market_preflight_*.json`.
- `report/automation/task_registration.json`: trạng thái task, action, giờ chạy tiếp và LastTaskResult.
- `report/automation/2026-10-28/status.json`: preflight không gọi quote; không được coi là phiên thu tự động thành công.
- Bộ tests offline bao gồm chia nhóm không mất/trùng mã, quota thấp, giờ nghỉ/ngày nghỉ, trạng thái stale, chuẩn hóa đúng phạm vi, ngày thu giả lập, không tự retry sau lỗi, backup nén/khôi phục và khóa chống hai collector.

Tài liệu API: [Reference chính thức](https://vnstocks.com/docs/vnstock/tham-chieu-reference), [Market](https://vnstocks.com/docs/vnstock/thi-truong-market), [quota](https://vnstocks.com/docs/vnstock/tham-khao/cap-quyen-va-gioi-han). Không dùng scraping hoặc endpoint giao dịch thật.
