# Chuẩn hóa lịch sử DNSE

## Chạy trên kho đã thu

```powershell
.\.venv\Scripts\python.exe -m scripts.normalize_dnse_history
.\.venv\Scripts\python.exe -m scripts.audit_dnse_normalized --export
```

Lệnh tìm các kho `data/history/dnse/YYYY-MM-DD/history.db` và kho `morning/history.db` đã tải hết trang. Dữ liệu chuẩn hóa được lưu riêng trong `data/processed/dnse/normalized.db`; bản xuất là `quotes.normalized.jsonl.gz`. Không cần gọi API để chuẩn hóa lại.

## Đơn vị và phạm vi bằng chứng

- Giá nhân 1.000, dùng Decimal, xuất theo VND.
- Khối lượng nhân 10, giữ số nguyên cổ phiếu; không làm tròn để che lỗi dữ liệu.
- ACB/STO/G1 mang trạng thái `EMPIRICALLY_VERIFIED`, dựa trên sáu cặp giá/khối lượng khớp ảnh ngày 29/10 và các đối chiếu khối lượng khớp trước đó.
- Các mã khác vẫn được chuyển đổi đầy đủ theo cùng schema, nhưng mang trạng thái `ASSUMED_SHARED_SCHEMA`; không trình bày là đã trực tiếp xác minh tất cả mã/sàn. A1/B5 mặc định từ chối tính toán từ nhóm này.
- Hồ sơ bất biến và checksum bằng chứng nằm trong `data/control/dnse_normalization_rules.json`. Đây là tệp riêng cục bộ; clone source public không tự có dữ liệu và bằng chứng cá nhân.

## Quy tắc xử lý

Giữ ba mức đầu của mỗi phía và ghi số mức nguồn. `null`, mảng rỗng hoặc mức thiếu vẫn là chưa có dữ liệu; không tự đổi thành số 0. Độ sâu quan sát bằng tổng khối lượng các mức có giá/khối lượng hợp lệ. Nếu không có mức sử dụng được, độ sâu là null. Cờ `*_depth_is_partial` thể hiện phạm vi quan sát chưa đủ ba mức.

Không dùng `totalBidQtty` hoặc `totalOfferQtty` làm độ sâu: hai trường này có thể trả 0 dù các mức có khối lượng. Cờ riêng ghi nhận tổng nguồn không nhất quán. Không tự tạo giá tham chiếu hoặc giá khớp gần nhất từ bid/ask khi endpoint quotes không cung cấp.

Thời gian `time` của DNSE được đọc theo GMT+7 như đặc tả API, lưu thêm UTC và ngày nguồn. Thời điểm nhận trang lịch sử được giữ riêng, không thay bằng thời điểm nguồn hoặc thời điểm chuẩn hóa. Vì dữ liệu được tải hồi tố, không dùng thời gian nguồn để giả rằng chương trình đã nhận được dữ liệu trong phiên; replay tái dựng lịch sử cần được phân biệt với backtest theo thời điểm nhận thật.

Phân loại cửa sổ liên tục theo giờ và sàn: HOSE 09:15–11:30/13:00–14:30, HNX 09:00–11:30/13:00–14:30, UPCOM 09:00–11:30/13:00–15:00. Những giai đoạn khác và cuối tuần được giữ nhưng không đưa vào mô phỏng khớp liên tục. Đây là phân loại theo lịch giờ, không thay cho trạng thái mở cửa hoặc lịch nghỉ chính thức của sàn. Tham chiếu [hướng dẫn giao dịch của PHS](https://www.phs.vn/san-pham-dich-vu/huong-dan-giao-dich/62) và [quy định UPCOM của Asean Securities](https://www.aseansc.com.vn/wp-content/uploads/2025/05/Quy-dinh-giao-dich-chung-khoan-tai-san-UPCOM.pdf).

Mã có phản hồi lịch sử rỗng chỉ có dòng trong danh mục phủ dữ liệu, không có snapshot giả bằng 0. Kết quả người dùng đối chiếu bảng giá được sao chép vào bảng `manual_checks` riêng, không thay thế lịch sử API.

## Chống trùng, tiếp tục và truy vết

Một snapshot được nhận diện bằng hash toàn bộ thông điệp raw, gồm mã, sàn, bảng và thời gian nguồn. Chỉ gộp thông điệp giống hoàn toàn; cùng sổ lệnh ở hai thời điểm khác nhau vẫn là hai quan sát. Mỗi vị trí gốc `(archive_id, page_id, ordinal)` vẫn có một dòng lineage. Nếu nhiều lần tải cùng sự kiện, giữ thời điểm nhận sớm nhất và toàn bộ đường dẫn nguồn.

Ghi kết quả và ledger từng trang trong một transaction. Chạy lại bỏ qua trang đã ghi, không nhân hệ số lần hai. Đổi bộ hệ số hoặc bằng chứng cần kho đầu ra phiên bản mới; không ghi đè dữ liệu đã dùng quy tắc khác. Các tệp raw và manifest được đọc bằng kết nối chỉ đọc, kiểm tra SHA-256 trước và sau.

## Nghiệm thu

`audit_dnse_normalized` đối chiếu lại từng vị trí raw với bản chuẩn hóa: mức giá/khối lượng, đơn vị, thời gian, mã/sàn/bảng, độ sâu, nhận diện sự kiện và lineage. Kiểm tra tổng dòng, checksum, SQLite, khóa ngoại và số dòng xuất. Mẫu ACB đủ điều kiện được chạy qua A1/B5; một mẫu chưa xác minh đơn vị phải bị bộ tính chặn.

Báo cáo: `report/dnse_normalization.json`, `report/dnse_normalization_audit.json`, `report/dnse_normalization_finance_checks.json`. Chuẩn hóa không đồng nghĩa chứng nhận dữ liệu đầu nguồn đầy đủ hoặc đã hoàn thành yêu cầu NEO API.

## Dashboard và quy trình cập nhật

Kho chuẩn hóa đã được tích hợp vào ba màn hình khi chọn nguồn `dnse`; xem [hướng dẫn sử dụng](dnse_dashboard.md). Replay DNSE dùng chế độ tái dựng hồi tố được ghi rõ trên màn hình, giữ riêng thời điểm nhận và không thay đổi replay theo thời điểm nhận của các nguồn khác. Các truy vấn giới hạn theo mã/ngày/thời điểm; biểu đồ dùng tối đa 600 điểm đại diện.

`python -m scripts.update_dnse_day --date YYYY-MM-DD` thực hiện toàn bộ chuỗi thu, audit, báo cáo thiếu, chuẩn hóa và nghiệm thu; thêm `--session morning` cho phiên sáng. Chạy `--plan` để xem các bước và giờ được bắt đầu mà không gọi API hoặc ghi tệp. Khi có lỗi, chuỗi dừng, không đưa dữ liệu chưa tải đủ sang bước chuẩn hóa; chạy lại cùng lệnh để tiếp tục.
