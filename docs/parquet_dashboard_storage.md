# Dashboard dùng Parquet

Cập nhật kiểm tra 16/11: dashboard vẫn đọc Parquet; catalog đã tăng lên 14 ngày. **SQLite dẫn xuất 19,85 GB chưa xóa**; đã đối chiếu toàn bộ 12 ngày thực sự có trong SQLite, 11.354.800 snapshot, 12.685.823 lineage và metadata với Parquet. Đã kiểm tra bản sao gốc và khôi phục các tệp bổ sung ngày 12/11; bằng chứng ở `report/legacy_sqlite_removal_eligibility.json` và `report/legacy_delta_restore_check.json`. Đang chờ chấp thuận xóa đúng `data/processed/dnse/normalized.db` cùng WAL/SHM rỗng bằng helper giới hạn đường dẫn `scripts.remove_legacy_sqlite`. Không xóa raw, Parquet hay backup. Thông tin tác vụ chờ ngày 12/11 bên dưới là lịch sử, không chứng minh database đã xóa. Luồng tự cập nhật web mới: [runbook](daily_monitor_automation.md).

Cập nhật 12/11/2026: dashboard lịch sử, watchlist, heatmap/replay, tái phân tích và volume bubbles đọc Parquet. JSON còn dùng để truyền qua HTTP và lưu metadata nhỏ; không cần giữ thêm gói JSON đầy đủ trên đĩa.

## Dữ liệu ở đâu?

- `data/compact/catalog.json`: danh mục 12 ngày sổ lệnh và 33 gói giao dịch khớp, tổng 11.354.800 snapshot và 338.942 giao dịch.
- `data/compact/dnse/<ngày-thời gian>/`: quotes theo shard, lineage, rules/catalog/ledger/manual checks và checksum. Kho quotes/trades khoảng 1,42 GB trước khi thêm replay.
- `data/compact/dnse-trades/<ngày-mã-thời gian>/`: giao dịch khớp riêng, giữ giá chính xác, mọi lần khớp và nhãn chất lượng.
- `data/compact/monitor/<dataset-id-checksum>/`: khung replay Parquet và metadata detector; giữ cả phiên bản cũ để ghi chú vẫn trỏ đúng sự kiện.
- `data/exports/orderbook-monitor/index.json` và `*.events.json`: chỉ mục và danh sách sự kiện nhỏ.
- `data/history/`, `data/raw/`, `data/control/orderbook-monitor-reviews.db`: raw, bằng chứng và ghi chú vẫn giữ. Không xóa raw vì đây là nguồn đối chiếu/tái dựng.

Parquet vẫn nằm trên máy. Nén không đồng nghĩa đã tải lên Drive. Người dùng đã xác nhận kết nối Drive, nhưng phiên công cụ hiện chưa có chức năng tải tệp Drive; `cloud_uploaded=false` cho đến khi xác minh được tệp đích.

## Sử dụng hằng ngày

Mở dashboard:

```powershell
.\.venv\Scripts\python.exe -m scripts.serve_orderbook_monitor
```

Mở `http://127.0.0.1:8877/`. Khởi động lại server sau khi cập nhật mã. Phiên bản backend mới là `offline-monitor-v4-parquet`. Streamlit tự chọn catalog nếu có; đường dẫn SQLite cũ vẫn được nhận diện để tương thích cấu hình đã lưu.

Cập nhật một ngày đã đóng phiên:

```powershell
.\.venv\Scripts\python.exe -m scripts.update_dnse_day --date 2026-11-12 --no-export --no-publish-web
```

Thay ngày tương ứng. Khi có catalog, lệnh này chỉ chuẩn hóa ngày được chọn trong `data/processed/dnse/work/<ngày>/`, audit độc lập với raw, kiểm tra tài chính, xuất/đối chiếu Parquet, thay catalog rồi xóa SQLite tạm. Nếu lỗi, bản làm dở giữ lại để tiếp tục; kho ngày cũ không bị xóa. Lịch Windows và chính sách xuất mẫu đã có không thay đổi do việc đổi định dạng lưu trữ.

Tạo replay từ kho đã audit, không gọi API:

```powershell
.\.venv\Scripts\python.exe -m scripts.build_orderbook_monitor --all-days --symbols ACB FPT HPG
.\.venv\Scripts\python.exe -m scripts.build_trade_bubbles --all-days --symbols ACB FPT HPG
```

Lệnh bubbles chỉ đọc giao dịch khớp đã thu, không suy ra khớp lệnh từ thay đổi sổ lệnh. Ngày chưa thu trades có thể báo thiếu. ACB giữ nhãn xác minh theo bằng chứng; FPT/HPG không được tự nâng nhãn đơn vị.

## Chuyển dữ liệu cũ và dọn dung lượng

`scripts.compact_dnse_history --all-days` dùng để chuyển từ SQLite lịch sử còn tồn tại. Sau chuyển, dùng luồng cập nhật từng ngày bên trên, tránh dựng lại SQLite toàn kho.

`scripts.migrate_dashboard_parquet` đã so toàn bộ khung của 34 bộ replay, 33 bộ trade và 144 trường hợp truy vấn theo thời gian với bản cũ. Báo cáo `report/dashboard_parquet_migration.json`. Giữ nguyên dataset/event ID, bản tái phân tích và ghi chú.

```powershell
.\.venv\Scripts\python.exe -m scripts.cleanup_dashboard_storage
```

Lệnh dọn kiểm tra bản sao đã thử khôi phục, checksum các tệp thay thế, khóa writer và audit trước khi xóa. Nếu tiến trình đang dùng SQLite, báo chờ và giữ database. Báo cáo chính xác tệp đã xóa, byte giải phóng và phần còn chờ ở `report/dashboard_parquet_cleanup.json`. Raw, bằng chứng, review và các kho WS/SSI khác không thuộc danh sách xóa.

Lần chuyển 12/11 đã xóa 67 gói JSON (798.541.156 byte). Dashboard 8877 đã khởi động lại bằng bộ đọc Parquet và kiểm tra API sau khi xóa. SQLite khoảng 19,85 GB đang được writer cũ sử dụng; đã mở một tác vụ chờ tối đa 24 giờ để dọn sau khi nhả khóa, audit đạt và đối chiếu lại toàn bộ với Parquet. Trạng thái ở `report/dashboard_parquet_cleanup.json`, thông tin tác vụ ở `report/dashboard_parquet_cleanup_worker.json`. Tác vụ chờ này cần máy tiếp tục hoạt động; nếu hết thời gian hoặc máy khởi động lại, chạy lại lệnh dọn ở trên khi writer đã kết thúc. Chưa coi SQLite là đã xóa.

Lệnh `scripts.normalize_dnse_history` cũ được chặn khi có catalog để tránh vô tình tạo lại database toàn lịch sử. Dùng `scripts.update_dnse_day --date ... --no-export` để cập nhật từng ngày.

## Sao lưu và khôi phục

```powershell
.\.venv\Scripts\python.exe -m scripts.create_recovery_backup
.\.venv\Scripts\python.exe -m scripts.create_recovery_backup --restore "data/backups/recovery-<thời gian>/liquiditylab-recovery.tar.gz" --destination ".tmp/restore-check-<thời gian>"
```

Bản sao gồm raw, các gói Parquet được catalog/replay tham chiếu, bằng chứng, review và mã nguồn; loại `.env`, khóa thật và database chuẩn hóa lớn đã được thay thế. Kiểm tra SHA-256 và kết quả khôi phục, rồi mới dọn đúng thư mục `payload/` và restore thử. File `.tar.gz` là nén, không phải mã hóa; lưu riêng tư. Chỉ tải archive đã kiểm tra lên Drive, không tải `.env` hay cả dự án. Bản sao trên cùng ổ chưa bảo vệ được trường hợp hỏng ổ đĩa.
