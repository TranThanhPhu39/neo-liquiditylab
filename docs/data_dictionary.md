# Data dictionary · schema 1.0

Kho tạm `raw_quote_batches` dùng riêng cho response chưa xác minh đơn vị: provider/upstream/version, received_at_utc, units_status=UNVERIFIED, run_id và payload chỉ chứa trường thị trường. Các giá trị raw volume chưa mang đơn vị CP. Bảng này không được trả qua `Storage.query()` và không chuyển tự động sang OrderBookSnapshot. Schema chuẩn bên dưới chỉ áp dụng dữ liệu đã đủ điều kiện chuẩn hóa.

`unit_contracts`: contract_hash, payload hồ sơ đơn vị, recorded_at_utc. `snapshot_lineage`: khóa ghép raw_batch_id/symbol, contract_hash, snapshot_id, normalized_at_utc. Thời gian normalized_at là audit xử lý, khác collected_at của quan sát. Các bảng này hiện chưa có hồ sơ đơn vị thật hoặc snapshot thật đã chuyển.

| Trường | Kiểu / đơn vị | Ý nghĩa |
|---|---|---|
| symbol / exchange | str | Mã chuẩn uppercase, sàn nguồn hoặc UNKNOWN |
| provider | demo / vnstock / neo | Nguồn hệ thống; không đổi nhãn Vnstock thành NEO |
| upstream_source | str | synthetic_fixture hoặc kbs; NEO chờ xác minh |
| capture_method | str | fixture / rest_poll / hypothetical:tên |
| provider_version | str | Phiên bản adapter/SDK thực dùng |
| collected_at_utc | aware datetime | Thời điểm thực nhận; UTC |
| received_at_local | aware datetime dẫn xuất | Cùng instant với collected_at, Asia/Ho_Chi_Minh |
| source_timestamp | aware datetime hoặc null | Thời điểm sự kiện nguồn; null nếu nguồn không cung cấp |
| market_session | str | Trạng thái nguồn; UNKNOWN khi chưa xác minh, SYNTHETIC ở demo |
| reference_price / last_price | Decimal hoặc null, VND/CP | Giá tham chiếu / giá gần nhất; close_price Vnstock ánh xạ last_price |
| bids / asks | tuple 3 QuoteLevel | Mức 1–3, không tự sort để che dữ liệu lỗi |
| QuoteLevel.price | Decimal hoặc null, VND/CP | Giá dương hữu hạn; ATO/ATC không phải số |
| QuoteLevel.volume | int hoặc null, CP | >=0, không lô; không tự đổi đơn vị chưa xác minh |
| bid_price_1..3 / ask_price_1..3 | string Decimal hoặc null | Dạng phẳng để SQLite/CSV không mất chính xác |
| bid_vol_1..3 / ask_vol_1..3 | int hoặc null | Dạng phẳng khối lượng |
| issues | list[str] | MISSING, INVALID, PARTIAL, UNSORTED, CROSSED, LOCKED... |
| data_quality_status | VALID / PARTIAL / INVALID | Chất lượng cấu trúc; tách với freshness và connectivity |
| event_id | str hoặc null | ID sự kiện nguồn nếu có; không tự bịa |
| schema_version | str | 1.0 |

`raw_observations` giữ các trường thị trường trước chuẩn hóa theo allowlist; không giữ payload toàn bộ chứa header/account/token. `orderbook_snapshots.payload` là canonical record (tiền là string Decimal). `collection_runs` ghi provider, started/finished, status, symbols, inserted và loại lỗi; không lưu nội dung exception SDK có thể chứa secret.

Trường kết quả A1: requested/executed/uncovered (CP), coverage (0–1), gross_value/benchmark_value/cost/fees/sell_tax/net_value (VND), vwap (VND/CP), cost_bps (bps), fills (mức, giá, CP), warnings. Null khác 0: không có fill ⇒ VWAP và cost undefined; phần không có benchmark không bị gán cost bằng 0.
