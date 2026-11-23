# Nghiên cứu vượt đỉnh phiên trước

Chức năng trong **Phân tích mã → Nghiên cứu vượt đỉnh phiên trước**, dùng lịch sử giao dịch khớp riêng và sổ lệnh đã lưu. Không gọi API giao dịch hoặc thay đổi lịch thu. Bản thu SSI hiện chưa được ghép vào nghiên cứu này.

## Sử dụng

1. Chọn **Lịch sử**, ngày và mã có tập giao dịch khớp. Kho Parquet cục bộ tại thời điểm triển khai có 33 tập giao dịch ACB/FPT/HPG trong 11 ngày đến 11/11/2026. Có sổ lệnh không đồng nghĩa đã có giao dịch khớp riêng.
2. Mở khối nghiên cứu. Để trống giá để dùng **đỉnh các giao dịch đã lưu** của ngày tham chiếu. Đây là mức thăm dò, không phải đỉnh OHLC toàn phiên đã xác minh. Hoặc nhập đỉnh phiên trước đã đối chiếu, theo VNĐ: `30000`, không phải `30`.
3. Ngày mặc định là ngày làm việc trước (bỏ thứ Bảy/Chủ nhật). Chưa có lịch nghỉ của sở trong chức năng; chủ động chọn ngày thực tế nếu có ngày nghỉ. Ngày không có dữ liệu sẽ được báo thiếu; không tự lùi qua nhiều ngày. Lưu ý chia tách/quyền cổ tức có thể làm mức giá danh nghĩa giữa hai ngày không so sánh trực tiếp được.
4. **Phân tích tới con trỏ** giữ cutoff đang xem. **Đến cuối dữ liệu và phân tích** chủ động tua đến snapshot cuối, rồi phân tích đến mốc đó; không chứng nhận đã có toàn bộ phiên.
5. Đọc đường giá khớp theo phút, mức tham chiếu, các lần vượt; bảng giá quan sát sau 5/15 phút và nhóm sổ lệnh. **Xem sổ lệnh** tua về snapshot không muộn hơn lần vượt để xem heatmap và bằng chứng.
6. **Tải nghiên cứu JSON** lưu tham chiếu, cutoff, cấu hình, chất lượng, từng lần vượt và kết quả so sánh. Tua lùi, đổi mã/ngày hoặc tham chiếu sẽ ẩn kết quả và tệp xuất cũ; bấm phân tích để tính lại.

## Quy tắc v1

- Một lần vượt cần hai giao dịch cùng phiên liên tục: giá trước ≤ tham chiếu và giá sau > tham chiếu. Bắt đầu dữ liệu khi giá đã trên mức không được tính là một lần vượt. Khoảng cách hai giao dịch >60 giây gắn nhãn thưa.
- Giữ các lần vượt cách nhau ít nhất 900 giây, tối đa 100 lần; vượt giới hạn trả kết quả một phần. Tham số phát triển được cố định/version hóa, chưa hiệu chỉnh hoặc chứng minh hiệu quả thị trường.
- Mốc 5/15 phút là thời gian đồng hồ trong cùng phiên liên tục. Không nối qua nghỉ trưa/đấu giá/hết phiên. Giá mốc là giao dịch gần nhất không muộn hơn mốc, cách mốc tối đa 30 giây. Không dùng giá sau cutoff để hoàn thành mốc còn chờ.
- Nhóm đủ điều kiện so sánh yêu cầu chuỗi giao dịch trong cửa sổ không có khoảng >60 giây và đối soát tăng bộ đếm lũy kế giữa các giao dịch. Đây vẫn chỉ là các giao dịch đã quan sát, không có chứng nhận sequence của sở. Khoảng thưa có thể do không giao dịch hoặc dữ liệu thiếu; hệ thống không khẳng định nguyên nhân.
- “Trên mức tại mốc” không có nghĩa duy trì trên mức trong toàn bộ 5/15 phút. Biến động tính từ giá khớp lần vượt tới giá khớp quan sát ở mốc, không phải lợi nhuận thực thi hoặc kết quả chiến lược.
- Sổ lệnh chỉ dùng snapshot không muộn hơn lần vượt, tuổi tối đa 15 giây, ba mức hợp lệ hai phía, đơn vị đã xác minh và đúng phiên liên tục. Không đủ điều kiện thì giữ `UNAVAILABLE`, không thay bằng 0 hoặc nâng nhãn đơn vị.
- Nhóm thử nghiệm: imbalance top 3 >0 và spread ≤ median 10 phút trước, lấy tối đa một quan sát mỗi giây, tối thiểu 5 giây có mẫu. Book sau lần vượt không được đưa vào baseline. Điều kiện này chưa phải “xác nhận breakout”.
- Bảng nhóm là so sánh mô tả cùng mẫu; nhóm ALL chứa cả các nhóm con. Không coi chênh lệch tỷ lệ là bằng chứng cải thiện dự báo, quan hệ nhân quả hoặc độ chính xác ngoài mẫu. Chưa triển khai mô hình giá–khối lượng so với mô hình thêm sổ lệnh, kiểm định ngoài mẫu hoặc backtest chi phí.
- Thiếu giao dịch khớp thì không dựng giao dịch từ midpoint/bid/ask. Giá rời top 3 không chứng minh hủy lệnh. Các bước chuẩn hóa, phiên và bằng chứng đơn vị cũ vẫn được giữ.

## Dùng dòng lệnh

```powershell
.\.venv\Scripts\python.exe -m scripts.research_breakout --day 2026-11-11 --symbol ACB --output report/breakout_acb_2026-11-11.json
.\.venv\Scripts\python.exe -m scripts.research_breakout --day 2026-11-11 --symbol ACB --reference-day 2026-11-10 --reference-price 20700 --cutoff 2026-11-11T11:00:00+07:00 --output report/breakout_acb_manual.json
```

API chỉ đọc: `/api/research/breakout?dataset_id=...&cutoff=...`, tùy chọn `reference_day` và `reference_price`. Trên cloud, endpoint dùng cùng semaphore tác vụ nặng với replay và recovery; bị bận trả 503 có thể thử lại. Xử lý quote Parquet theo batch và chỉ giữ các đặc trưng nhỏ, không sao chép cả replay vào RAM. Không xuất ghi chú riêng, thông tin đăng nhập hoặc raw database trong báo cáo nghiên cứu.
