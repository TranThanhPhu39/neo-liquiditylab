# Chuẩn hóa cấu trúc WebSocket DNSE

Luồng tự động sau mỗi checkpoint: **raw WS → chuẩn hóa cấu trúc → audit → checkpoint mã hóa**. Cả thu GitHub và thu cục bộ đều gọi normalizer; mã nguồn `src/data_pipeline/dnse_ws_normalization.py`, `scripts/normalize_dnse_realtime.py`.

## Đã làm

- Bảo toàn số thập phân nguồn dạng chuỗi, không đi qua phép tính float. `qtty`/`quantity` vào cùng trường `source_quantity`; nếu hai trường mâu thuẫn thì cách ly bản ghi.
- Timestamp protobuf có UTC ISO và Unix nanosecond chính xác riêng; timestamp không có timezone được đánh dấu chưa xác định. Giữ riêng thời điểm nhận, ngày nguồn và ngày partition.
- Giữ toàn bộ mức giá nhận được, bao gồm 10 mức trong mẫu thật. Mỗi thông điệp là một quan sát độc lập; không tự carry-forward trường thiếu hoặc ghép thành full book.
- `null`, mảng rỗng, giá/lượng 0 nguồn được phân biệt. Không có thông điệp cho một mã không trở thành book 0.
- Incremental/idempotent theo archive + event hash, giữ tất cả receipts, frame hash, connection, runs và gaps. Không sửa raw. Transaction rollback khi lineage thay đổi hoặc vượt ngân sách.
- Audit checksum đầu vào, checksum output, FK, số events/receipts và khóa finance. Bản ghi số không hợp lệ được cách ly nhưng vẫn giữ lineage.
- Output riêng, format `dnse-ws-structured-v1`; từ chối ghi đè raw hoặc database khác. Giới hạn output mặc định 2 GiB và tối thiểu 1 GiB đĩa trống.

## Đơn vị chưa xác minh

Đây là **chuẩn hóa cấu trúc**, chưa phải quy đổi đầy đủ sang VND/CP. Mọi record giữ `UNVERIFIED_WEBSOCKET`, `price_unit=null`, `volume_unit=null`, `financial_eligible=false`; giá/lượng gốc nằm trong `bid_levels`/`ask_levels`. Các cột canonical ba mức VND/CP để null.

Không kế thừa hệ số ×1.000/×10 và bằng chứng ACB của REST. SDK chính thức ánh xạ `qtty` thành `quantity` nhưng việc đổi tên không chứng minh đơn vị hoặc full-vs-delta semantics. Cần xác nhận riêng trước khi đưa WS vào A1/B5 hoặc xuất dữ liệu phân tích VND/CP trên Pages. Nguồn tham khảo: [DNSE market WS](https://developers.dnse.com.vn/docs/guide/market-data/connect/), [PriceLevel/Quote SDK](https://github.com/dnse-tech/openapi-sdk/blob/main/python/dnse/websocket/models.py).

## Nơi lưu và khôi phục

Local: `data/processed/dnse_ws/YYYY-MM-DD/<partition>/normalized.db`. Báo cáo của lần thu local ở `report/realtime/dnse/YYYY-MM-DD/<partition>/normalization.json`.

GitHub: namespace Releases `dnse-ws-normalized`, cùng AES-GCM/state key hiện có. Payload gồm SQLite cấu trúc, audit/status, danh mục; không chứa auth frame hoặc API key. Namespace raw `dnse-ws-state` được giữ nguyên. Không upload WS plaintext lên Pages.

Chuẩn hóa lại một raw archive trên máy:

```powershell
.\.venv\Scripts\python.exe -m scripts.normalize_dnse_realtime --input data/realtime/dnse/2026-11-04/morning/quotes.db
```

Tải và giải mã checkpoint cấu trúc từ GitHub về máy đã giữ state key:

```powershell
.\.venv\Scripts\python.exe -m scripts.sync_dnse_ws_normalized --date 2026-11-04 --partition morning
```

Lệnh restore tạo thư mục riêng trong `data/processed/dnse_ws/restored/`, không ghi đè database đang dùng. Chỉ có checkpoint nếu job đã thực sự chạy và lưu thành công.

Lỗi normalizer không làm mất checkpoint raw đã lưu và không dừng sớm luồng ghi raw; checkpoint sau thử lại. Nếu lần cuối vẫn lỗi, job trả `FAILED_NORMALIZATION_RESUMABLE`. Quarantine được ghi trong audit. Không có quote sẽ có 0 records, không tạo snapshot giả.
