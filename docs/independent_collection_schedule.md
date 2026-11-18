# Lịch dự phòng độc lập với GitHub cron

Kiểm tra ngày 18/11 lúc 13:29 giờ Việt Nam: SSI, DNSE và SSI guard đều active, cờ thu bật, nhưng không có run được tạo từ lịch trong ngày. Dispatch trực tiếp được GitHub tiếp nhận và SSI thực sự nhận quote/trade, nên đường khởi động từ workflow_dispatch hoạt động. API không cho biết nguyên nhân nội bộ vì sao cron chưa phát sự kiện; không quy lỗi này cho broker hoặc khẳng định GitHub quá tải.

## Ba lớp lịch hiện có

| Lớp | Lịch giờ Việt Nam | Vai trò |
| --- | --- | --- |
| GitHub cron SSI/DNSE | Giữ các mốc hiện có | Nguồn lịch chính, có thể trễ/thiếu sự kiện |
| Windows Realtime Dispatch Guard | Thứ Hai–Sáu, 08:35–15:35, mỗi 5 phút; thêm lúc đăng nhập | Kiểm tra/kích hoạt GitHub qua workflow_dispatch bằng đồng hồ Windows |
| Windows DNSE Daily Fallback | Thứ Hai–Sáu, từ 15:15 lặp mỗi 30 phút trong 8 giờ; thêm lúc đăng nhập | Tải bù ngày đã đóng, chuẩn hóa/audit, xuất bản theo chính sách hiện có |

Guard Windows chỉ phát lệnh khởi động khi không có collector queued/running, không quá hai lần thử trong cửa sổ phiên và cách lần thất bại gần nhất ít nhất 10 phút. Giữ cờ tắt/stop file và lịch nghỉ được cấu hình. SSI/DNSE xử lý độc lập: lỗi một nguồn không chặn kiểm tra nguồn kia.

DNSE được làm mới danh mục từ REST và lưu checkpoint danh mục mã hóa trước khi kích hoạt nếu chưa có catalog đã xuất bản của ngày hiện tại. Không kéo dài tuổi catalog bằng cách đổi ngày trên danh sách cũ. Vì vậy lỗi danh mục hết hạn bảy ngày được tránh bằng tải thật, không gỡ kiểm tra tuổi dữ liệu.

Ngày mới chưa đến 15:15 không được tải như một ngày hoàn chỉnh. Các lần kiểm tra sau 15:15 giải quyết trường hợp tác vụ cũ còn xuất bản tại mốc 15:15: lần này bị IgnoreNew thì lần 15:45/16:15 tiếp theo vẫn có thể bắt kịp. Không khẳng định chắc chắn chạy đúng 15:15 nếu máy tắt, tác vụ cũ còn chạy hoặc mạng lỗi.

## Điều kiện vận hành

Windows guard cần máy này bật, có mạng và người dùng hiện tại đăng nhập. Các bộ thu sau khi đã khởi động tiếp tục chạy trên runner GitHub đến hết cửa sổ phiên, độc lập với việc giữ cửa sổ terminal mở. Cơ chế này không giải quyết việc kích hoạt khi máy đã tắt trước giờ phiên; muốn độc lập hoàn toàn cần một bộ lập lịch bên ngoài luôn hoạt động. Không tạo dịch vụ trả phí hoặc đưa GitHub token sang bên thứ ba trong thay đổi này.

Task dùng Interactive/Limited, không lưu mật khẩu vào Task Scheduler. GitHub credential được đọc từ cơ chế Git đã có của người dùng; không nhúng token vào file task, command line, source hoặc log. Không có API giao dịch.

## Cài và kiểm tra

```powershell
.\.venv\Scripts\python.exe -m scripts.install_cloud_guard_task
.\.venv\Scripts\python.exe -m scripts.install_dnse_day_task
.\.venv\Scripts\python.exe -m scripts.install_cloud_guard_task --verify-only
.\.venv\Scripts\python.exe -m scripts.install_dnse_day_task --verify-only
```

Chạy kiểm tra ngay (không tạo bản trùng khi collector đang chạy):

```powershell
.\.venv\Scripts\python.exe -m scripts.scheduled_cloud_guard
```

Báo cáo hoạt động:

- `report/automation/cloud_dispatch_status.json`: kết quả kiểm tra/kích hoạt từng provider.
- `report/automation/cloud_dispatch_task_registration.json`: lịch Windows thực đã đăng ký.
- `report/automation/dnse_task_registration.json`: lịch cuối ngày và các mốc lặp.
- `report/dnse_ws_catalog_preparation.json`: ngày danh mục thật và tag checkpoint mã hóa.

Tạm dừng kích hoạt GitHub bằng cách tạo `data/control/STOP_GITHUB_DISPATCH`; không tự dừng các runner đã chạy. Tác vụ thu ngày DNSE có stop file `data/control/STOP_DNSE_HISTORY` riêng. Xóa stop file chỉ khi muốn cho phép tiếp tục.

## Khôi phục ngày 18/11

- SSI dispatch 13:31: run 44251912701, web nhận quote/trade thật cho ACB/FPT/HPG.
- Catalog DNSE làm mới ngày 18/11, 1.517 mã, đã xuất bản mã hóa.
- DNSE dispatch 13:35: run 44252492913. Trạng thái chạy không tự chứng minh đã thu đủ dữ liệu; cần kiểm tra checkpoint/kết quả cuối phiên riêng.
- Chạy thử task Windows lúc 13:41: LastTaskResult=0, cả hai provider COLLECTOR_ALREADY_ACTIVE, không tạo run trùng.
- Bộ kiểm thử liên quan: 75/75 đạt tại máy.
- Phần phiên sáng và đầu phiên chiều bị lỡ không được tuyên bố đã phục hồi realtime. Lịch tải REST cuối ngày là dữ liệu lịch sử riêng, tùy API hỗ trợ và không phục hồi receipt/gap WebSocket đã bỏ lỡ.
