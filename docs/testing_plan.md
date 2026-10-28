# Kiểm thử và nghiệm thu

Tests tự động hoàn toàn offline; fixture synthetic, fake transport chỉ bên trong tests, không xuất thành dữ liệu live. `pytest` dùng thư mục tạm trong workspace để tránh quyền Windows Temp. Streamlit AppTest chạy logic và widgets thực; bổ sung kiểm tra HTTP health của server thật.

| Nhóm | Bằng chứng |
|---|---|
| Schema | Decimal, CP nguyên, UTC/local, đơn vị chưa biết từ chối |
| Chất lượng | null/0/âm/NaN/Infinity/ATO/ATC, partial, unsorted/crossed/locked, timestamp lỗi |
| A1 | exact 89.875.000 gross, 125.000 cost; 4.000 yêu cầu chỉ 3.500 covered |
| A1 edge | nhỏ/exact/over-depth, không bid, thiếu best ⇒ undefined benchmark, buy dùng Ask, phí/thuế |
| B5 | 0–90%, giữ giá/asks, price shift, monotonic executed, reverse stress boundary |
| Portfolio | CSV lỗi/trùng/âm/phân số, fraction, thiếu mã, stale/skew, phần bị loại |
| Storage | insert/query/range/source, unique event, unchanged retained, raw secret allowlist, lock retry |
| Collector/providers | no permission, empty/unsupported version, timeout, no fallback, run audit |
| Replay/history | empty, arrival time tránh look-ahead, deterministic, summary mẫu |
| UI | 3 màn hình, nhập 4.000 ⇒ uncovered 500, demo/NEO disconnected/no data, lưu và replay |

Tình huống demo bằng HPG: (1) bán 500, (2) bán 3.000, (3) bán 4.000, (4) stress 50% với 4.000 ⇒ depth/covered 1.750, uncovered 2.250, (5) portfolio HPG/FPT/ACB. Kỳ vọng cụ thể tính từ fixture, không là bằng chứng thị trường thực.

Kết quả thực tế sau chạy ghi tại report/test_results.md. Vnstock integration thực và toàn bộ NEO integration còn pending vì quyền, đơn vị và transport chưa được xác nhận; tests mock không thay thế bước đó.
