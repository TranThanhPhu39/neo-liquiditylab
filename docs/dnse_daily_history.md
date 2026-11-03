# Tải lại dữ liệu ngày giao dịch bằng DNSE

Để chạy đủ chuỗi thu/audit/chuẩn hóa bằng một lệnh, dùng python -m scripts.update_dnse_day --date YYYY-MM-DD sau 15:10, hoặc thêm --session morning sau 11:30. Ví dụ 28/10 dưới đây minh họa CLI; không cần tải lại kho đã hoàn tất.

Nguồn chính hiện là DNSE. Danh mục lấy từ GET /instruments?securityGroupId=ST, phân trang và kiểm tra ST/STO/STX/UPX; chỉ cổ phiếu G1, không gồm ETF/chứng quyền/phái sinh/trái phiếu. Danh mục lấy mới theo kho/ngày: 28/10 có 1.520 mã; 02/11 có 1.518 mã (408 HOSE, 299 HNX, 811 UPCOM). Đã thu bốn ngày 28, 29, 30/10 và 02/11 với 3.746.982 snapshot chuẩn hóa; xem [chốt ngày gần nhất](../report/dnse_day_completion_2026-11-02.md).

## Chạy hoặc tiếp tục

```powershell
.\.venv\Scripts\python.exe -m scripts.collect_dnse_history --date 2026-10-28
```

Chỉ chạy sau 15:10 giờ Việt Nam của ngày cần tải. Truy vấn từ 00:00 đến 15:10 để giữ cả thông điệp trước mở cửa và các giai đoạn đóng cửa, bảng lô chẵn **G1**, thứ tự thời gian tăng dần. Đây là lịch sử trạng thái bid/ask, không phải chỉ số OHLC hoặc lịch sử khớp lệnh. Không áp bộ lọc 09:15–14:30 của collector Vnstock cũ lên lịch sử DNSE.

### Tải riêng phiên sáng

```powershell
.\.venv\Scripts\python.exe -m scripts.collect_dnse_history --date 2026-10-29 --session morning
.\.venv\Scripts\python.exe -m scripts.report_dnse_missing --date 2026-10-29 --session morning
.\.venv\Scripts\python.exe -m scripts.audit_dnse_history --date 2026-10-29 --session morning --export
```

Chế độ `morning` chạy sau 11:30, cố định khoảng 00:00–11:30 giờ Việt Nam. Lưu kho riêng `data/history/dnse/YYYY-MM-DD/morning`, không ghi đè hoặc đổi checkpoint của bộ tải cả ngày. Báo cáo tiến độ có hậu tố `_morning`.

Lệnh `report_dnse_missing` chỉ đọc kho đang có và tạo ba tệp `report/dnse_missing_YYYY-MM-DD_morning.md/.json/.txt`. Tách rõ API trả rỗng (`quotes=[]`, hết token), lỗi HTTP, mã chưa có trang đầu và mã còn trang tiếp. Book có `bid` hoặc `offer=null` nhưng vẫn có quote không được xếp vào API trả rỗng. Khi chạy báo cáo trong lúc bộ tải còn hoạt động, danh sách chỉ là tạm thời; cần chạy lại sau khi kết thúc để chốt kết quả.

Danh mục lấy mới từ DNSE mỗi kho/ngày. Mã không còn trong danh mục hiện tại được tách khỏi nhóm trả rỗng; không tự kết luận hủy niêm yết. So sánh với ngày trước có thể là sáng so với cả ngày nên thay đổi trạng thái không tự chứng minh lỗi nguồn.

Khi người dùng đối chiếu bảng giá, nhật ký riêng ở `data/control/dnse_manual_checks_YYYY-MM-DD_SESSION.json` ghi nguồn và loại bằng chứng. Nếu người dùng xác nhận khớp lệnh và dư mua/bán đều hiển thị 0, báo cáo tách mã sang nhóm đã đối chiếu và loại khỏi danh sách còn cần kiểm tra. Tổng số phản hồi lịch sử rỗng và raw vẫn giữ nguyên. Bằng chứng bảng hiển thị không được biến thành snapshot lịch sử hoặc xác nhận mọi thời điểm trong phiên đều có độ sâu bằng 0.

Trang 1.000 quote đã kiểm tra nhận được; yêu cầu 2.000, 5.000 và 10.000 bị HTTP 400 nên giữ tối đa 1.000. Khoảng cách request quotes ít nhất 0,5 giây, tối đa lý thuyết 7.200/giờ, dưới hạn mức công bố 10.000/giờ; endpoint instruments giãn ít nhất bốn giây. Chỉ một tiến trình có thể giữ khóa bộ tải. Không chạy các phép probe cùng lúc khi đang tải đầy đủ.

Mỗi lượt lấy một trang/mã theo vòng trước khi đi sâu vào mã nhiều cập nhật. Quote hai phía rỗng/null giữ nguyên; không giả định mọi mã đủ ba mức. Raw được lưu riêng trước chuẩn hóa; ACB đã xác minh đơn vị thực nghiệm và mở A1/B5 với snapshot đủ điều kiện, mã khác giữ ASSUMED_SHARED_SCHEMA. Xem [chuẩn hóa](dnse_normalization.md).

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



