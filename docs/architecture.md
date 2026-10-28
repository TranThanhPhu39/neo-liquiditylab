# Kiến trúc và trách nhiệm

Ứng dụng Python đơn tiến trình với SQLite cục bộ. Không cần Kafka, Redis hay dịch vụ giao dịch. SDK Vnstock chỉ được import trong worker khi người dùng chủ động gọi nguồn thực. Demo, tests và replay không cần SDK.

| Module | Trách nhiệm |
|---|---|
| config/settings.py | Đọc .env, database, stale và skew |
| data_providers/base.py | Interface snapshot, info, readiness |
| data_providers/demo_provider.py | Fixture cố định, provenance synthetic |
| data_providers/vnstock_provider.py | Gate quyền/đơn vị, rate gate, subprocess timeout |
| data_providers/neo_provider.py | Boundary chưa cấu hình; không transport giả |
| data_pipeline/normalization.py | Decimal, CP nguyên, UTC/local, missing/invalid |
| data_pipeline/validation.py | Thứ tự giá, crossed/locked, partial, freshness |
| data_pipeline/storage.py | Raw allowlist, snapshots, levels, issues, run audit |
| data_pipeline/collector.py | Retry timeout giới hạn, audit thành công/thất bại |
| data_pipeline/replay.py | Event order, latest-at không look-ahead |
| finance/ | A1, B5, portfolio, lịch sử độc lập provider |
| app/main.py | Ba màn hình tiếng Việt, trạng thái và nhập/xuất |
| cli.py và scripts/ | Collection/export/quality từ PowerShell |

SQLite dùng WAL, foreign keys và index provider/symbol/time. Mỗi snapshot có ID, raw và 6 levels liên kết. Unique event key là hash(provider, upstream, symbol, event_id nếu có; nếu không dùng collected_at_utc). Retry cùng observation không ghi lại. Hai lần nhận khác thời điểm dù cùng book vẫn lưu; fingerprint chỉ dùng đo unchanged. `BEGIN IMMEDIATE` giúp rate gate trong database tránh hai lần thu cùng nguồn ngay nhau.

Timestamp nguồn có thể null; không suy diễn bằng thời điểm nhận REST. Database query theo thời điểm nhận UTC. Replay xét cả event time và thời điểm đến để tránh thông điệp tương lai. Thông điệp đến muộn được giữ trong kho và tính trong quality, không ghi đè/xóa lịch sử. Snapshot giữa các mã cùng nguồn được kiểm tra skew và stale trước tổng hợp.

Các lớp có context manager đóng connection; ghi snapshot là transaction gồm snapshot/levels/raw/issues. Chờ khóa SQLite có timeout và retry 0,1/0,2 giây. Hỏng DB/permission hiển thị lỗi; không ghi đè database người dùng.
