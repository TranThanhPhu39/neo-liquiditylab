# Kiểm tra và hoàn thiện web ngày 16/11/2026

## Kết quả

Dashboard Render đã phục hồi gói mã hóa mới **42 replay, 14 ngày đến 16/11, 870.907 quan sát** của ACB/FPT/HPG. Quote cả ba mã mang nhãn đơn vị đã xác minh. Ngày 16/11 có ACB 6.617, FPT 21.093, HPG 13.942 quan sát; số đủ điều kiện tương ứng 5.844, 18.697, 11.463. Không đồng nhất số quan sát với số đủ điều kiện detector.

Nguyên nhân web cũ không lên dữ liệu: task Windows chỉ công bố mẫu Pages, chưa nối gói replay riêng sang Render. Đã nối luồng có checkpoint, kiểm tra khôi phục cục bộ/từ xa, kiểm tra ngày và chất lượng trên web rồi mới ghi hoàn tất. Người sở hữu duyệt xuất định kỳ đúng ba mã dưới dạng mã hóa ngày 16/11. Không upload plaintext thị trường, `.env`, broker key hoặc database toàn thị trường.

Gói ciphertext `monitor-web-seed-20261116-79fa09548053` có 208.697.764 byte; tải lại và giải mã xác minh 386 tệp. Chạy lại luồng Windows trả `COMPLETED`, không có ngày cần tải, publisher `already_current=true`.

## Các lỗi đã sửa

- Sidebar từng ghi FPT/HPG chưa xác minh bằng nhãn cố định; nay lấy từ chất lượng dataset đang chọn.
- Footer web từng báo dữ liệu nằm trên máy; nay phân biệt replay đã lưu trên web.
- GET bị 503 tạm thời từng làm trang ngừng tải; nay retry hữu hạn. POST lưu ghi chú không tự retry để tránh lưu lặp.
- Tải dataset lớn và trades cùng lúc cạnh tranh giới hạn xử lý của backend Free; nay đợi dataset xong rồi tải trades/reviews, bỏ kết quả cũ nếu người dùng chuyển mã/ngày.
- Màn hình điện thoại từng thiếu đường tới SSI và sidebar chiếm quá nhiều chiều cao; nay có liên kết SSI và dải chọn mã gọn, không tràn ngang toàn trang ở 390 px.
- Lỗi một ngày cũ hoặc upload mẫu Pages không còn chặn thu thử ngày mới và công bố riêng khi đủ dữ liệu. Tổng lượt vẫn báo lỗi nếu còn phần chưa xong.
- Kiểm tra dọn SQLite cũ từng so tổng 12 ngày với catalog đã tăng lên 14 ngày; nay so đúng các ngày có trong database cũ, kiểm tra mọi dòng, metadata và bản thay thế đã sao lưu.

## Tự động hóa và dữ liệu

Task `LiquidityLab - DNSE Daily Fallback` đã xác nhận Ready, chạy ngày 16/11 lúc 15:15, result 0; lần kế tiếp 17/11 lúc 15:15 giờ Việt Nam, có tải bù khi đăng nhập. **Máy phải bật, đăng nhập và có mạng**. Luồng hiện tại: REST → chuẩn hóa → audit → Parquet → replay ba mã → mã hóa Releases → Render. Xem [runbook](../docs/daily_monitor_automation.md).

Raw toàn thị trường ngày 16/11: 729.742 lượt, 2.077 trang; 1.300 mã COMPLETE, 217 mã NO_DATA_RETURNED. Chuẩn hóa 724.992 snapshot duy nhất, 4.750 lượt trùng còn lineage, không quarantine. Không gán 217 mã thiếu dữ liệu thành 0, không tuyên bố mọi mã toàn thị trường đã xác minh độc lập đơn vị.

SSI GitHub vẫn bật lịch, nhưng sự kiện lịch sáng 16/11 chỉ được tạo lúc 15:32; runner bắt đầu khoảng ba giây sau. Vị trí chậm nằm trước lúc giao runner. Chưa có bằng chứng xác định nguyên nhân nội bộ của GitHub. Một số đoạn dữ liệu chiều đã thu không thay thế cả phiên; trạng thái partial/gaps vẫn giữ.

## Kiểm tra

Kiểm tra trình duyệt Edge thật trên localhost dùng cùng CloudServer/giới hạn xử lý như Render: chọn ACB/FPT/HPG, tải sổ lệnh và canvas, tua 13:30, mở màn hình theo dõi, desktop 1440 px và mobile 390 px. Không có JavaScript exception, không tràn ngang toàn trang. Đã tiêm một lỗi GET 503 và chứng minh tự tải lại; đã kiểm tra request trades bắt đầu sau khi dataset tải xong. Bằng chứng `report/dashboard_browser_qa_2026-11-16.json`, ảnh `report/dashboard_qa_desktop.png`, `report/dashboard_qa_mobile.png`.

Đã kiểm tra API hosting READY, 42 dataset chính và quote ba mã ngày 16/11 trên Render. Trang giới thiệu công khai; dữ liệu vẫn cần mật khẩu. Bộ kiểm thử tự động và commit triển khai cuối được ghi trong `report/web_audit_final_2026-11-16.json` sau bước triển khai.

## Còn giới hạn

- Chưa nghiệm thu stream SSI trọn phiên, reconnect trong thị trường thật và cờ LO. Không gọi replay cuối ngày là realtime.
- Ngày 12, 13 và 16/11 chưa có gói trades DNSE; không có bubbles thật từ các gói quote này. Chất lượng giao dịch cũ giữ nguyên nhãn.
- Detector là công cụ phát hiện/giải thích sự kiện theo ngưỡng thử nghiệm, không kết luận thao túng hoặc đưa tín hiệu mua bán.
- Render Free ngủ/cold start; PostgreSQL Free hiện hết hạn 15/12/2026. Cần xuất ghi chú hoặc chuyển kho trước hạn; chưa tự động nâng phí.
- Đã xóa JSON dẫn xuất trùng 798.541.156 byte. SQLite cũ 19.846.524.928 byte còn trên máy, đã chứng minh thay thế đủ 11.354.800 snapshot và 12.685.823 lineage; chờ duyệt xóa đúng tệp. Raw, Parquet, backup không thuộc bước xóa.
