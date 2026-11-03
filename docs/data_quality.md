# Chất lượng dữ liệu

**WebSocket 03/11:** probe đã auth/nhận MSR trên hai runner, nhưng mẫu giữ riêng UNVERIFIED_WEBSOCKET. Quy tắc canonical/scale DNSE bên dưới áp cho kho REST hiện tại; chưa nhập WS vào normalized.db hoặc tự dùng qtty như quantity của REST. Xem [hướng dẫn WS](dnse_websocket.md).

**Phạm vi chính hiện tại: DNSE (03/11/2026).** Raw/canonical tách riêng theo [kiến trúc](architecture.md), [chuẩn hóa](dnse_normalization.md). Audit hiện có bốn ngày, 4.367.925 vị trí raw → 3.746.982 snapshot duy nhất, 0 cách ly, integrity ok, 0 lỗi FK. Báo cáo chính là scripts.audit_dnse_history, scripts.audit_dnse_normalized và scripts.report_dnse_missing; scripts.validate_database bên dưới phục vụ kho cũ.

DNSE phân biệt COMPLETE/NO_DATA_RETURNED/lỗi/chưa đủ trang. API rỗng không là zero; mức có giá hợp lệ và lượng 0 có thể là known zero, khác null/không có mức. Không dùng tổng aggregate nguồn bằng 0 thay độ sâu của các mức. Giữ source time và arrival riêng, nhận diện trùng bằng raw hash, lineage cho mọi occurrence; quality không thay bằng chứng đơn vị. ACB có phạm vi thực nghiệm, nhóm assumed vẫn bị chặn A1/B5. Các chỉ tiêu có thể bị đánh dấu PARTIAL do đơn vị chưa xác minh, không nhất thiết do thiếu mức.

Replay DNSE là hồi tố theo source time, không tuyên bố đã nhận trong phiên. Phân tích dùng mốc phút, freshness và tập phút chung, không carry qua nghỉ trưa hoặc ngày khác. Chưa có một tháng dữ liệu; toàn danh mục đã được thu, việc tiếp theo là thu nhiều ngày và kiểm chứng phạm vi.

**Cơ chế chung và kho Demo/Vnstock cũ:**

Pipeline giữ raw thị trường theo allowlist, chuẩn hóa có provenance và gắn issue, không thay ATO/ATC thành giá 0. Giá <=0, NaN, Infinity, volume âm/phân số hoặc không chuyển được thành số trở thành null + issue. Missing một mức ⇒ PARTIAL; sử dụng các mức hợp lệ và cảnh báo phạm vi. Giá sai thứ tự, crossed/locked, timestamp lỗi/future ⇒ INVALID và engine từ chối. Không tự sort hoặc sửa book.

Freshness dùng source time nếu có, nếu không dùng arrival như proxy và luôn hiện SOURCE TIME UNKNOWN. Unchanged khác duplicate: event key chống ghi retry, fingerprint đo tỷ lệ hai book liên tiếp giống nhau. Không xóa book giống nhau ở hai thời điểm nhận khác nhau; unchanged không chứng minh stale. Các bản ghi trễ giữ nguyên và đếm late_observations.

SQLite: WAL, transaction snapshot nguyên tử, retry lock giới hạn. Collection runs ghi SUCCESS/PARTIAL/FAILED, loại lỗi và missing symbols. Không lưu SDK message thô, token hay header. Rest polling không là tick streaming. Chế độ offline không tự bật mạng. Health check chỉ xác nhận readiness cục bộ, `network_verified=false`.

Báo cáo `scripts.validate_database`: snapshots, symbols, collection_days, real_observed_days (không tính demo), sessions_with_verified_source_time, valid_ratio, missing_level_ratio, failed_runs, unchanged_ratio, late_observations, gap_candidates, latest_received. Ngày thu thập khác phiên giao dịch; không coi quote UNKNOWN là phiên đã xác minh. Gap candidates chỉ đo cặp cùng ngày/cùng cửa sổ sáng hoặc chiều theo lịch tham khảo, gap >1,5 interval; không tự tuyên bố mất kết nối.

Khi có 0 mẫu hoặc 1 mẫu, các tỷ lệ không có mẫu số trả null. `report/demo_quality.json` là bằng chứng chạy demo, không là chất lượng nguồn thực. Chưa có một tháng dữ liệu thật. Kế hoạch: 3 mã → kiểm tra quota/tính đúng đơn vị → thu nhiều phiên → tăng mã nếu được phép → đối chiếu thời điểm thiếu và ngày nghỉ thủ công.


