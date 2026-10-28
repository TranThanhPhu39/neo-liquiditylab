# Chuyển raw sang schema chuẩn và chốt lượt thu

## Điều kiện hiện tại

Cập nhật sau 14:40 ngày 28/10: đã có hồ sơ thực nghiệm `config/unit_contract.vnstock-kbs-5.2.1.json`, hệ số 1/1; 66 snapshot đã chuẩn hóa thành công. [Bản thẩm định](../report/kbs_unit_review_2026-10-28.md) ghi rõ thời điểm ảnh xấp xỉ, 8 cặp khớp, nguồn chú thích KBS và các giới hạn. Dùng hồ sơ thật này khi chạy lại migration; hồ sơ example bên dưới vẫn UNVERIFIED theo đúng vai trò mẫu.

```powershell
.\.venv\Scripts\python.exe -m scripts.normalize_raw --contract config/unit_contract.vnstock-kbs-5.2.1.json --report report/normalization_status_rerun.json
.\.venv\Scripts\python.exe -m scripts.validate_normalized_sample
```

Chạy lại cùng hồ sơ bỏ qua snapshot đã chuyển, không sinh thêm lịch sử. Để xem kết quả trên UI, chọn vnstock → Replay lịch sử. Collector raw đã COMPLETED lúc 14:30; báo cáo chốt RAW_ONLY là bằng chứng trạng thái lúc chốt, trước migration. Các đoạn tiếp theo mô tả điều kiện chặn và quy trình được triển khai trước thẩm định; không còn là trạng thái đơn vị hiện tại.

Collector raw đang ghi dữ liệu thật từ Vnstock/KBS, version 5.2.1. Đơn vị volume chưa được chứng minh; các cổng chuẩn hóa giữ đóng. Trang bảng giá KBS và đường dẫn `/category` đều trả HTTP 400 khi đối chiếu từ môi trường này. Không đổi hệ số dựa vào con số có vẻ hợp lý, không dùng hệ số hiển thị web như đơn vị SDK.

`config/unit_contract.example.json` là mẫu **UNVERIFIED**, không chứa hệ số giả. Không đổi trạng thái thành VERIFIED chỉ để chạy phần mềm.

## Hồ sơ đơn vị và bằng chứng

Cần hợp đồng dữ liệu chính thức có phiên bản hoặc đối chiếu bảng giá cùng thời điểm. Hồ sơ ghi provider, upstream source, phiên bản SDK, hệ số sang VND và CP, thời điểm có timezone, loại bằng chứng, ghi chú thẩm định, URL nguồn, đường dẫn bản bằng chứng cục bộ và SHA-256. Đường dẫn bằng chứng được giải tương đối từ file hồ sơ.

`load_unit_contract` kiểm tra sự đầy đủ và checksum, **không tự xác nhận nội dung tài liệu là đúng**. Việc thẩm định nội dung và xác định hệ số phải dựa trên bằng chứng nguồn. Không có hồ sơ đạt yêu cầu thì CLI trả `BLOCKED_UNITS_UNVERIFIED`, không ghi snapshot, không sửa raw.

## Chuyển đổi có truy vết

```powershell
# Hiện tại lệnh này bị chặn đúng thiết kế vì hồ sơ mẫu chưa xác minh.
.\.venv\Scripts\python.exe -m scripts.normalize_raw --contract config/unit_contract.example.json --report report/normalization_status_2026-10-28.json
```

Khi có hồ sơ thật đã thẩm định, truyền đường dẫn hồ sơ đó. CLI tạo backup trước khi chuyển đổi. `--batch-ids` có thể giới hạn các batch cụ thể. Hệ số được nhân tường minh bằng Decimal; dữ liệu không phải số như ATO/ATC vẫn đi vào validation để gắn issue, không thành giá 0.

Raw giữ nguyên. Snapshot nhận `capture_method=raw_migration`, `event_id=raw:<batch_id>:<symbol>` và timestamp nhận gốc; không dùng thời điểm chạy migration làm thời điểm quote. Source timestamp null vẫn null. Bảng `unit_contracts` lưu hồ sơ/checksum; `snapshot_lineage` liên kết raw batch, symbol, contract hash và snapshot ID.

Toàn bộ migration chạy trong một transaction; snapshot/levels/raw/lineage lỗi thì rollback. Chạy lại cùng hồ sơ bỏ qua bản đã có. Hồ sơ khác trên một raw batch đã chuyển sẽ bị từ chối, cần quy trình hiệu chỉnh riêng; không ghi đè âm thầm. INVALID/PARTIAL được giữ trạng thái, không tự sửa cho hợp lệ.

## Kiểm chứng phần giá đã đủ điều kiện

```powershell
.\.venv\Scripts\python.exe -m scripts.validate_market_sample --report report/market_sample_validation_2026-10-28.json
```

Lệnh kiểm tra giá dương hữu hạn, thứ tự ba mức, crossed/locked và spread. Relative spread là tỷ số không cần đơn vị khối lượng. `spread_source_price_units` giữ nguyên đơn vị giá nguồn; không tự nhân hệ số hiển thị. Đây là **price-only diagnostics**, không tính depth, coverage, VWAP, proceeds, stress hay danh mục từ raw. Không gọi đó là kiểm chứng đầy đủ A1/B5 trên thị trường thật.

## Tự chốt khi lượt thu kết thúc

```powershell
.\.venv\Scripts\python.exe -m scripts.finalize_collection --collector-status report/raw_collection_2026-10-28.json --at '2026-10-28T14:30:00+07:00' --report report/session_close_2026-10-28.json
```

Tác vụ này đã được khởi động cho lượt thu hiện tại; không chạy thêm bản thứ hai. Chỉ đọc database và gọi CLI audit/kiểm tra giá, không gửi request thị trường. Chờ collector báo COMPLETED/STOPPED/FAILED; nếu sau thời điểm hẹn cộng 90 giây vẫn chưa xác nhận dừng, báo cáo ghi `AUDITED_COLLECTION_END_UNCONFIRMED`, không giả mạo hoàn thành.

Khi collector báo COMPLETED, báo cáo chốt là `COMPLETED_RAW_ONLY`, kèm số batch/quan sát, gap, CSV, backup và kiểm tra giá. Điều này xác nhận chốt phần cửa sổ đã thu, **không xác nhận đơn vị volume, một phiên trọn vẹn hay đủ 5–10 phiên**. Tác vụ cần máy/tiến trình tiếp tục chạy; không tự khôi phục sau reboot.

Các file `session_close_2026-10-28_audit.json` và `_prices.json` đã được tạo lúc chốt. `session_close_2026-10-28.json` ghi COMPLETED_RAW_ONLY lúc 14:30 ngày 28/10, phản ánh trạng thái trước migration; không ghi đè bằng kết quả chuẩn hóa sau đó.
