# Tải lại dữ liệu ngày giao dịch bằng DNSE

Người dùng đã yêu cầu tải nguyên ngày 28/10/2026 cho tất cả cổ phiếu. Nguồn danh mục được lấy trực tiếp từ GET `/instruments?securityGroupId=ST`, phân trang đủ `total`, kiểm tra nhóm ST và ba thị trường STO/STX/UPX. Ngày 28/10 nguồn trả **1.520 mã: HOSE 407, HNX 299, UPCOM 814**, khớp với danh mục VCI đã lưu. Không gồm ETF, chứng quyền, hợp đồng tương lai hoặc trái phiếu.

## Chạy hoặc tiếp tục

```powershell
.\.venv\Scripts\python.exe -m scripts.collect_dnse_history --date 2026-10-28
```

Chỉ chạy sau 15:10 giờ Việt Nam của ngày cần tải. Truy vấn từ 00:00 đến 15:10 để giữ cả thông điệp trước mở cửa và các giai đoạn đóng cửa, bảng lô chẵn **G1**, thứ tự thời gian tăng dần. Đây là lịch sử trạng thái bid/ask, không phải chỉ số OHLC hoặc lịch sử khớp lệnh. Không áp bộ lọc 09:15–14:30 của collector Vnstock cũ lên lịch sử DNSE.

Trang 1.000 quote đã kiểm tra nhận được; yêu cầu 2.000, 5.000 và 10.000 bị HTTP 400 nên giữ tối đa 1.000. Khoảng cách request quotes ít nhất 0,5 giây, tối đa lý thuyết 7.200/giờ, dưới hạn mức công bố 10.000/giờ; endpoint instruments giãn ít nhất bốn giây. Chỉ một tiến trình có thể giữ khóa bộ tải. Không chạy các phép probe cùng lúc khi đang tải đầy đủ.

Mỗi lượt lấy một trang/mã theo vòng để có dữ liệu toàn danh mục trước khi đi sâu vào mã nhiều cập nhật. Quote hai phía rỗng hoặc `null` được giữ nguyên; không giả định mọi mã đều có đủ ba mức. Đơn vị giá/khối lượng DNSE **chưa xác minh**, chỉ lưu raw, chưa đưa vào A1/B5.

## Lưu trữ và kiểm tra

- Database raw riêng: `data/history/dnse/2026-10-28/history.db`; mỗi trang nén gzip, checksum SHA-256, thời điểm nhận UTC, thời gian nguồn GMT+7 và token nối trang.
- Danh mục và phản hồi tham chiếu: cùng thư mục, `universe.json`, `universe_page_*.json`.
- Tiến độ/báo cáo: `report/dnse_history_2026-10-28.json`, cập nhật khoảng 15 giây. `COMPLETE` chỉ khi API hết token; `NO_DATA_RETURNED` ghi riêng, không coi là bằng chứng mã không có giao dịch.
- Ghi trang và checkpoint trong cùng một transaction SQLite, WAL/synchronous FULL. Chạy lại cùng lệnh tiếp tục token còn thiếu, bỏ mã đã hoàn tất; không lấy lại từ đầu. Không đổi page size khi resume.
- Dừng nếu còn dưới 5 GiB đĩa trống, gần hết quota, lỗi mạng/schema hoặc gặp STOP. Những trạng thái này không được gọi là hoàn thành.
- Với timeout, lỗi kết nối/DNS tạm thời hoặc HTTP 500/502/503/504, thử tối đa ba lần trên cùng trang với giãn cách 2 và 5 giây. Không retry lỗi khóa/quyền/quota hoặc xác minh TLS. Các lần gọi thất bại cũng được ghi vào bộ đếm request trước khi I/O.
- Dữ liệu raw và trạng thái cục bộ đều gitignored, không tự đưa lên repo public.

Giữ máy bật và có mạng trong khi tải. Trên Windows, tiến trình tạm yêu cầu hệ thống không tự sleep và khôi phục trạng thái trước đó khi kết thúc; không đổi power plan lâu dài. Muốn dừng có lưu tiến độ: Ctrl+C hoặc tạo `data/control/STOP_DNSE_HISTORY`. Muốn tiếp tục sau khi dừng bằng cờ, xóa đúng tệp STOP đó rồi chạy lại lệnh. Không tự bật lịch Windows hoặc GitHub từ lượt tải thủ công này.

Sau khi tải hết:

```powershell
.\.venv\Scripts\python.exe -m scripts.audit_dnse_history --date 2026-10-28 --export
```

Audit đọc từng trang, giải nén và kiểm tra checksum, nối token, số trang/bản ghi, mã/bảng/thị trường và khoảng thời gian. Export streaming `quotes.raw.jsonl.gz` không đưa toàn bộ dataset lên RAM. Số raw records có thể bao gồm bản ghi giống nhau do nguồn trả ở ranh giới trang; không tuyên bố đây là số trạng thái độc nhất. Hết token chứng minh đã đọc hết những trang API cung cấp trong phạm vi yêu cầu, không độc lập chứng nhận upstream không thiếu dữ liệu.
