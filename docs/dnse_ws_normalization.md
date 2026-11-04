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

## Phạm vi đơn vị đã kiểm chứng ngày 04/11/2026

Lớp cấu trúc gốc giữ `UNVERIFIED_WEBSOCKET`, `price_unit=null`, `volume_unit=null`, `financial_eligible=false`; giá/lượng nguồn nằm trong `bid_levels`/`ask_levels`. **ACB/STO/G1** có thêm lớp `canonical_snapshot` theo `dnse-ws-canonical-v1`, VND/CP, hệ số giá ×1.000 và khối lượng ×10. Chỉ tạo khi chính thông điệp đó có đủ ba mức giá/lượng mỗi bên; không carry-forward hoặc ghép với thông điệp trước.

Bằng chứng thực tế: ACB khớp cả sáu cặp giá/lượng ở **88/88 thông điệp, 83 thời điểm nguồn khác nhau**. REST được ghép theo khoảng biểu diễn của timestamp nguồn (mẫu này có độ chính xác mili giây), không dùng thời điểm gần nhất. REST ACB đã có đối chiếu độc lập trước đó. CEO/HNX khớp 200/200 và MSR/UPCOM khớp 64/64 nhưng mới chứng minh tỷ lệ tương đối WS–REST, vì REST hai mã đó chưa xác minh độc lập; không mở A1/B5 cho chúng.

Quy tắc metadata ở `config/unit_contract.dnse-ws-v1.json` ghi phạm vi, hệ số, số mẫu, checksum bằng chứng và hash quy tắc REST tham chiếu. Payload đối chiếu riêng lưu trong `data/control/dnse_ws_rest_review/`; không công khai giá/lượng của bằng chứng. Chưa xác nhận semantics đầy đủ của 10 mức hoặc khẳng định dữ liệu bao phủ toàn phiên. Nguồn tham khảo giao thức: [DNSE market WS](https://developers.dnse.com.vn/docs/guide/market-data/connect/), [PriceLevel/Quote SDK](https://github.com/dnse-tech/openapi-sdk/blob/main/python/dnse/websocket/models.py).

Audit tái tính `canonical_snapshot` từ lớp cấu trúc và quy tắc được duyệt. Khi hash quy tắc thay đổi, normalizer xử lý lại sự kiện cũ thay vì bỏ qua theo input hash. `canonical_events` và `canonical_financial_eligible` đếm phần đã quy đổi/đủ điều kiện; cờ toàn kho `units_verified=false` vẫn đúng vì phần còn lại chưa xác minh.

Checkpoint smoke thật có 9.423 sự kiện/9.432 receipts: 88 bản ACB có canonical và đủ điều kiện; không có quarantine. Đã so 528 kết quả A1 WS–REST bằng Decimal (hai chiều, ba quy mô lệnh, 88 quan sát), kết quả trùng nhau. Đây là các cửa sổ thu ngắn, không phải dữ liệu trọn ngày.

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

Xuất bản canonical đã kiểm chứng từ database về tệp riêng để chọn **Mở tệp riêng** trên dashboard (xử lý trong trình duyệt, không upload):

```powershell
.\.venv\Scripts\python.exe -m scripts.export_dnse_ws_browser --database <duong-dan-normalized.db> --output data/processed/dnse_ws/browser-verified.json
```

Tệp thực tế đã xuất: `data/processed/dnse_ws/browser-verified-2026-11-04.json` (88 quan sát ACB). Chỉ mở được trên bản dashboard có engine hỗ trợ `dnse-ws-canonical-v1`. Trên web, timestamp nhận WS và timestamp tải REST có nhãn riêng. Bản web tự tải vẫn là bộ REST đã công bố; checkpoint WS được giữ mã hóa, chưa tự thêm dữ liệu mới vào Pages.

Đối chiếu thêm với REST (chỉ đọc, tối đa 12 mã và cửa sổ một giờ mỗi mã):

```powershell
.\.venv\Scripts\python.exe -m scripts.review_dnse_ws_units --database <duong-dan-normalized.db> --symbols ACB CEO MSR
```

Lệnh ghi báo cáo riêng cùng bằng chứng nén và checksum, không tự thay đổi phạm vi đơn vị. Promotion chỉ dành cho ACB/STO/G1, tái đọc/checksum và đối chiếu toàn bộ bằng chứng trước khi ghi contract:

```powershell
.\.venv\Scripts\python.exe -m scripts.promote_dnse_ws_units --comparison <thu-muc-bang-chung>/ACB.comparison.json
```

Lỗi normalizer không làm mất checkpoint raw đã lưu và không dừng sớm luồng ghi raw; checkpoint sau thử lại. Nếu lần cuối vẫn lỗi, job trả `FAILED_NORMALIZATION_RESUMABLE`. Quarantine được ghi trong audit. Không có quote sẽ có 0 records, không tạo snapshot giả.
