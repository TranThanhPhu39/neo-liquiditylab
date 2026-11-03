# Kiến trúc và trách nhiệm — 03/11/2026

**Nghiệm thu mới 03/11:** WebSocket Upgrade, xác thực, subscribe G1 và nhận quote MSR đạt trên hai runner GitHub mới. Mỗi lượt giữ thống kê 10 mẫu; ACB/PVS chưa có thông điệp trong cửa sổ thử. Probe chưa bật lịch hoặc nhập dữ liệu vào A1/B5. Xem [hướng dẫn WS](dnse_websocket.md), [bằng chứng](../report/dnse_ws_validation_2026-11-03.md). Các nhận định chỉ thử mạng/chưa auth ở mốc trước dưới đây được giữ như lịch sử.

Ứng dụng Python, SQLite cục bộ, Streamlit và engine nhận schema nội bộ. **DNSE là nguồn chính** theo [quyết định nguồn](data_source_decision.md); Demo phục vụ tests, Vnstock là nguồn cũ. Adapter NEO chưa cấu hình còn trong code, không thuộc kế hoạch tích hợp tiếp.

| Module | Trách nhiệm |
| --- | --- |
| config/settings.py | Môi trường, kho, stale/skew; đường dẫn DNSE riêng. |
| data_providers/dnse_readonly.py | Ký REST, GET danh mục/quotes/trades, TLS, giới hạn nhịp, lọc thông tin nhạy cảm. |
| scripts/collect_dnse_history.py | Danh mục cổ phiếu G1 ba sàn, lịch sử sau phiên, phân trang/checkpoint, khóa OS, dừng an toàn. |
| data_pipeline/dnse_normalization.py | Decimal, hệ số tường minh, đơn vị, source/arrival time, phân loại phiên. |
| scripts/normalize_dnse_history.py | Canonical snapshots, catalog, ledger trang, lineage và gộp raw giống hoàn toàn. |
| scripts/audit_dnse_history.py, audit_dnse_normalized.py | Kiểm tra trang/hash/phạm vi/scale/tổng dòng/FK/export. |
| scripts/update_dnse_day.py | Thu → audit raw → báo cáo rỗng → chuẩn hóa → audit/export → kiểm tra tài chính. |
| data_pipeline/dnse_history.py | SQLite chỉ đọc, truy vấn chỉ mục, tái dựng hồi tố và lấy mẫu giới hạn. |
| app/dnse_history.py | Ba màn hình offline, bộ lọc, nhãn đơn vị, A1/B5, simulated portfolio. |
| finance/ | Depth/spread/OBI, A1/B5/portfolio/history và guard DNSE. |
| scripts/report_dnse_sessions.py, report_dnse_market.py | ACB và mô tả toàn danh mục, freshness và tập phút chung. |
| Provider/pipeline cũ | Demo/Vnstock và data/liquidity.db, tách hệ số/lineage khỏi DNSE. |

Raw: data/history/dnse/YYYY-MM-DD/history.db; kho morning/history.db riêng khi tải sáng. Trang giữ payload thị trường nén, hash, thời điểm nhận và token cho phép tiếp tục.

Canonical: data/processed/dnse/normalized.db, gồm archives, raw_catalog, snapshots, lineage, processed_pages, quarantine, normalization_rules, manual_checks. Tiền trong payload JSON dùng string Decimal, float chỉ dùng khi vẽ. Mọi occurrence giữ lineage kể cả khi gộp trùng; cùng book ở thời điểm nguồn khác vẫn giữ.

Ghi theo transaction, khóa OS tránh trùng tiến trình, kiểm tra raw trước/sau. Đổi rule đơn vị cần quy trình phiên bản mới, không tự ghi đè hồ sơ. Các bước làm mới coverage, tổng kết ngày và AppTest ngày mới hiện có thể chạy riêng sau pipeline chính.

Replay chung của nguồn cũ xét arrival và event time để tránh dùng thông điệp chưa đến. DNSE backfill có đường tái dựng theo source time riêng, ghi rõ hồi tố và giữ arrival thật; không làm yếu quy tắc Replay chung. Query DNSE giới hạn mã/ngày/cutoff; biểu đồ mặc định tối đa 600 điểm, không nạp toàn kho vào DataFrame.

