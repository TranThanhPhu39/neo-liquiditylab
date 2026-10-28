# Chất lượng dữ liệu

Pipeline giữ raw thị trường theo allowlist, chuẩn hóa có provenance và gắn issue, không thay ATO/ATC thành giá 0. Giá <=0, NaN, Infinity, volume âm/phân số hoặc không chuyển được thành số trở thành null + issue. Missing một mức ⇒ PARTIAL; sử dụng các mức hợp lệ và cảnh báo phạm vi. Giá sai thứ tự, crossed/locked, timestamp lỗi/future ⇒ INVALID và engine từ chối. Không tự sort hoặc sửa book.

Freshness dùng source time nếu có, nếu không dùng arrival như proxy và luôn hiện SOURCE TIME UNKNOWN. Unchanged khác duplicate: event key chống ghi retry, fingerprint đo tỷ lệ hai book liên tiếp giống nhau. Không xóa book giống nhau ở hai thời điểm nhận khác nhau; unchanged không chứng minh stale. Các bản ghi trễ giữ nguyên và đếm late_observations.

SQLite: WAL, transaction snapshot nguyên tử, retry lock giới hạn. Collection runs ghi SUCCESS/PARTIAL/FAILED, loại lỗi và missing symbols. Không lưu SDK message thô, token hay header. Rest polling không là tick streaming. Chế độ offline không tự bật mạng. Health check chỉ xác nhận readiness cục bộ, `network_verified=false`.

Báo cáo `scripts.validate_database`: snapshots, symbols, collection_days, real_observed_days (không tính demo), sessions_with_verified_source_time, valid_ratio, missing_level_ratio, failed_runs, unchanged_ratio, late_observations, gap_candidates, latest_received. Ngày thu thập khác phiên giao dịch; không coi quote UNKNOWN là phiên đã xác minh. Gap candidates chỉ đo cặp cùng ngày/cùng cửa sổ sáng hoặc chiều theo lịch tham khảo, gap >1,5 interval; không tự tuyên bố mất kết nối.

Khi có 0 mẫu hoặc 1 mẫu, các tỷ lệ không có mẫu số trả null. `report/demo_quality.json` là bằng chứng chạy demo, không là chất lượng nguồn thực. Chưa có một tháng dữ liệu thật. Kế hoạch: 3 mã → kiểm tra quota/tính đúng đơn vị → thu nhiều phiên → tăng mã nếu được phép → đối chiếu thời điểm thiếu và ngày nghỉ thủ công.
