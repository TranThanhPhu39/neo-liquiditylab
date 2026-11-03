# Data dictionary · schema 1.0

**WebSocket 03/11:** probe đã auth/nhận MSR trên hai runner, nhưng mẫu giữ riêng UNVERIFIED_WEBSOCKET. Quy tắc canonical/scale DNSE bên dưới áp cho kho REST hiện tại; chưa nhập WS vào normalized.db hoặc tự dùng qtty như quantity của REST. Xem [hướng dẫn WS](dnse_websocket.md).

**Cập nhật 03/11/2026 — DNSE là nguồn chính.** Kho DNSE tách khỏi storage Vnstock/Demo mô tả phía dưới. Xem [quyết định nguồn](data_source_decision.md) và [quy tắc chuẩn hóa](dnse_normalization.md).

| Kho/bảng DNSE | Ý nghĩa |
| --- | --- |
| data/history/dnse/YYYY-MM-DD/history.db | Raw: catalog, trang nén/hash, source time và arrival, token/checkpoint. |
| archives / raw_catalog | Hash/path/settings của từng kho nguồn, trạng thái và số raw theo mã. |
| snapshots | Canonical payload nén và chỉ mục mã/sàn/source_time/source_local_date, quality, units_status, phiên, financial_eligible. |
| lineage / processed_pages | Liên kết từng archive/page/ordinal về snapshot; ledger trang đã chuẩn hóa. |
| normalization_rules / quarantine / manual_checks | Hồ sơ đơn vị bất biến, raw không chuyển được và bằng chứng đối chiếu thủ công riêng. |

Canonical DNSE bổ sung market_id, board_id, unit_verification_status, unit_contract_hash, local_date, độ sâu quan sát và partial flags. Giá xuất string Decimal VND, lượng số nguyên CP theo hệ số tường minh; ASSUMED_SHARED_SCHEMA ghi rõ bằng chứng chưa đủ, không được engine coi là đã xác minh. Thiếu last/ref giữ null. Event ID DNSE là hash canonical raw gồm thời gian/mã/bảng, không phải ID được nhà cung cấp cấp. Xem file chuẩn hóa để đối chiếu tên trường phẳng đầy đủ.

**Phần storage/schema chung và Vnstock cũ:**

Kho tạm `raw_quote_batches` dùng riêng cho response chưa xác minh đơn vị: provider/upstream/version, received_at_utc, units_status=UNVERIFIED, run_id và payload chỉ chứa trường thị trường. Các giá trị raw volume chưa mang đơn vị CP. Bảng này không được trả qua `Storage.query()` và không chuyển tự động sang OrderBookSnapshot. Schema chuẩn bên dưới chỉ áp dụng dữ liệu đã đủ điều kiện chuẩn hóa.

`unit_contracts`: contract_hash, payload hồ sơ đơn vị, recorded_at_utc. `snapshot_lineage`: khóa ghép raw_batch_id/symbol, contract_hash, snapshot_id, normalized_at_utc. Thời gian normalized_at là audit xử lý, khác collected_at của quan sát. Các bảng Vnstock này đã có hồ sơ thực nghiệm và 66 snapshot chuyển ngày 28/10; không phải cấu trúc bảng raw/canonical DNSE ở trên.

| Trường | Kiểu / đơn vị | Ý nghĩa |
|---|---|---|
| symbol / exchange | str | Mã chuẩn uppercase, sàn nguồn hoặc UNKNOWN |
| provider | demo / vnstock / dnse / neo (adapter cũ chưa cấu hình) | Nguồn thực của quan sát; không đổi nhãn dữ liệu giữa các nhà cung cấp |
| upstream_source | str | synthetic_fixture hoặc kbs ở nguồn cũ; DNSE theo mapper riêng |
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
| event_id | str hoặc null | ID nguồn nếu có ở adapter cũ; DNSE dùng hash raw có truy vết như mô tả ở trên |
| schema_version | str | 1.0 |

`raw_observations` giữ các trường thị trường trước chuẩn hóa theo allowlist; không giữ payload toàn bộ chứa header/account/token. `orderbook_snapshots.payload` là canonical record (tiền là string Decimal). `collection_runs` ghi provider, started/finished, status, symbols, inserted và loại lỗi; không lưu nội dung exception SDK có thể chứa secret.

Trường kết quả A1: requested/executed/uncovered (CP), coverage (0–1), gross_value/benchmark_value/cost/fees/sell_tax/net_value (VND), vwap (VND/CP), cost_bps (bps), fills (mức, giá, CP), warnings. Null khác 0: không có fill ⇒ VWAP và cost undefined; phần không có benchmark không bị gán cost bằng 0.



