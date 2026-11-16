# Lịch sử sổ lệnh toàn thị trường

Theo yêu cầu mở rộng ngày 16/11/2026, dashboard lịch sử hỗ trợ toàn bộ danh mục được nguồn DNSE trả về, gồm HOSE/HNX/UPCOM. Danh mục ngày 16/11 có 1.517 mã: 1.300 có quan sát, 217 không được nguồn trả dữ liệu. Danh mục từng ngày có thể khác nhau; không đồng nhất với mọi loại chứng khoán đang tồn tại trên các sở.

## Sử dụng

Mở `/dashboard`, chọn ngày, lọc sàn hoặc nhập mã vào **Tìm mã**, chọn mã có dữ liệu rồi bấm **Mở mã**. Heatmap, sổ lệnh ba mức và replay dùng tất cả quan sát đã lưu của ngày/mã, không lấy mẫu để vừa giới hạn. Các mã có trên danh mục nhưng không có quan sát nằm trong bảng **Danh mục thị trường và dữ liệu đã lưu**, không xuất hiện như một sổ lệnh khối lượng 0.

Trang **Theo dõi** hiển thị tối đa 50 mã mỗi trang; dùng bộ lọc mã/sàn phía trên và nút Trước/Sau ở bảng danh mục. Bảng theo dõi chỉ lấy snapshot không muộn hơn cutoff đang xem. Thống kê số quan sát trong bảng kho dữ liệu là hồi tố cả ngày, có nhãn riêng. Mã không có snapshot trước cutoff vẫn báo chưa có dữ liệu, dù có quan sát ở cuối ngày.

ACB/FPT/HPG giữ replay, bằng chứng đơn vị, detector và ghi chú cũ. Các mã bổ sung là **chỉ quan sát**: giữ nhãn đơn vị/chất lượng của kho nguồn, khóa detector và cấu hình phân tích. Không có sự kiện không đồng nghĩa thanh khoản bình thường. Sổ lệnh chỉ bao phủ top 3, không có order ID hoặc thông tin hủy lệnh.

Volume bubbles chỉ có khi đã thu tập giao dịch khớp riêng đúng mã/ngày. Mở rộng quote không sinh trades hoặc nâng chất lượng trades. SSI realtime ở trang `/realtime` vẫn theo ba mã đã cấu hình; bước này không mở rộng subscription SSI ra toàn thị trường và không chứng nhận stream cả phiên.

## Lưu trữ và bảo mật

Kho canonical/raw gốc giữ nguyên. Bản phục vụ web chỉ chiếu các cột cần hiển thị: mã/sàn, ID quan sát, thời gian nguồn/thu, ba mức mua/bán, đơn vị/chất lượng và hash quy tắc. Đối chiếu từng trường sau ghi/đọc Parquet, số dòng phải khớp toàn bộ canonical của ngày; không lấy mẫu và không suy ra khối lượng 0.

Mỗi ngày có một gói Parquet mã hóa AES-GCM bất biến, kiểm tra khôi phục ở máy rồi tải lại từ Releases để kiểm tra từ xa. Catalog riêng và 42 replay ba mã nằm trong gói bootstrap nhẹ. Render chỉ tải và giải mã gói thị trường của ngày được người xem yêu cầu; giữ tối đa hai ngày trong cache đĩa tạm. Vì vậy cold start không phải tải tất cả lịch sử thị trường. Lần đầu mở một ngày có thể chờ lâu hơn; nếu tải lỗi, giao diện báo lỗi và có thể thử lại, không giả lập dữ liệu.

Các gói trên GitHub Releases đều là ciphertext; website và API thị trường giữ mật khẩu hiện có. Không upload `.env`, API key, thông tin tài khoản, raw DB hoặc database toàn thị trường. Khóa giải mã ở backend, không đưa vào browser. Cache chỉ chứa bản hiển thị dẫn xuất, không thuộc bản sao ghi chú PostgreSQL.

## Tự cập nhật

Task Windows 15:15 vẫn tải → chuẩn hóa → audit → Parquet. Khi `config/monitor_publication.json` có `market_enabled=true`, bước `scripts.publish_daily_monitor` thêm catalog và gói hiển thị toàn thị trường. Ngày cũ có cùng hash được dùng lại, chỉ upload/kiểm tra từ xa các gói mới chưa hoàn tất; bootstrap mới trỏ tới các tag ngày đã xác minh. Lỗi upload/deploy giữ checkpoint để chạy tiếp.

Máy vẫn cần bật, đăng nhập và có mạng. Bảy ngày là cửa sổ rà tải bù, không phải lịch xóa dữ liệu. Luồng này không tự làm GitHub cron chạy đúng giờ. Xem [vận hành hằng ngày](daily_monitor_automation.md).

Giới hạn riêng cho chế độ quan sát là 100.000 quan sát/mã/ngày; nguồn hiện có mã đạt 78.400, được giữ đầy đủ. Vượt giới hạn sẽ báo lỗi, không âm thầm lấy mẫu. Backend truyền khung hình theo batch nén gzip; danh mục được nén khi truyền và các bảng phân trang để giới hạn bộ nhớ trên Free.

Catalog có giới hạn an toàn 50.000 bộ ngày–mã và 64 MiB metadata, gói bootstrap tối đa 768 MiB. Khi lịch sử tăng tới ngưỡng cần phân vùng catalog/bootstrap tiếp; hệ thống báo lỗi để xử lý, không tự xóa dữ liệu cũ hay đổi sang lấy mẫu. Phạm vi hiện tại và các ngày tới hạn nộp 01/12 nằm trong các ngưỡng này; cấu hình Free không phải kho tăng trưởng vô hạn.

Lệnh chuẩn bị ở máy gốc (không gọi broker, chưa upload):

```powershell
.\.venv\Scripts\python.exe -m scripts.prepare_market_monitor
```

Kết quả: `report/market_monitor_preparation_latest.json`, các báo cáo `online_monitor_preparation_market_*.json` và dữ liệu dẫn xuất ở `data/control/market-projection-cache/`. Gói từng ngày được lưu lại để tránh mã hóa/upload lại lịch sử; chỉ cache chạy web trên Render bị giới hạn hai ngày, không phải kho lưu trữ gốc.
