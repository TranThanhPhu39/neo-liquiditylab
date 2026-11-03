# Kiểm thử và nghiệm thu — 03/11/2026

DNSE là nguồn chính theo [quyết định nguồn](data_source_decision.md). Tests tự động offline dùng fixture/fake transport được ghi rõ; nghiệm thu dữ liệu thực là bước riêng. Lần hồi quy gần nhất: **197 passed in 23.56s** ngày 03/11 sau khi thêm probe WebSocket và kiểm thử scope/auth/clocks/redaction/budget. Sửa tài liệu không được tính là chạy lại tests.

| Nhóm | Nội dung kiểm chứng |
| --- | --- |
| Schema/đơn vị | Decimal, CP nguyên, UTC/local, null/0, hồ sơ/hash; ACB đã xác minh, nhóm assumed không tự mở A1/B5. |
| Chất lượng | NaN/Infinity/âm/thiếu/partial/crossed/locked/unsorted, source time/phiên; không tự sửa book che lỗi. |
| A1 | Fixture gross 89.875.000, cost 125.000 VND cho 3.000 CP; yêu cầu 4.000 bao phủ 3.500; buy/fees/tax/benchmark, không định giá phần vượt depth. |
| B5/portfolio | Stress, monotonicity, reverse stress, CSV/fraction, stale/skew/missing và exclusion riêng. |
| DNSE REST/raw | Ký request, route/query allowlist, timeout/TLS, token, checkpoint, khóa và dừng an toàn; API rỗng khác book 0. |
| Lưu trữ/chuẩn hóa | Hash/scale/time, lineage, ledger nguyên tử, dedup/resume, giữ raw không đổi. |
| Replay/UI | Không dùng snapshot tương lai/ngày trước, tách hồi tố khỏi arrival Replay, ba màn hình và số điểm giới hạn. |
| Phân tích | Lịch từng sàn, freshness, phút chung, không carry qua trưa/ngày, zero khác unknown, cache theo fingerprint. |
| Đối chiếu đơn vị | Sáu cặp theo giá/bên, giờ/ảnh có bằng chứng; comparator không tự cấp VERIFIED. |

Kho bốn ngày 28, 29, 30/10 và 02/11: audit 4.367.925 vị trí raw, 3.746.982 snapshot duy nhất, 0 cách ly, integrity ok, 0 lỗi FK. Ngày 02/11 có 1.079.933 snapshot; kiểm tra tài chính 80 trường hợp/40 snapshot đủ điều kiện đạt, guard nhóm chưa xác minh đạt. AppTest ACB 14:20 ngày 02/11 đạt ba màn hình.

AppTest và Node VM với DOM/Plotly giả lập xác nhận logic, không thay kiểm tra vẽ biểu đồ/bố cục bằng trình duyệt. Còn cần nghiệm thu trực quan, ảnh demo, backup/restore ngoài máy, chốt dài ngày và tự chạy DNSE trên môi trường phù hợp. WebSocket đã đạt probe auth/quotes MSR trên hai runner; chưa nghiệm thu collector dài phiên hoặc phạm vi toàn thị trường. Xem [hướng dẫn WS](dnse_websocket.md).

Bằng chứng: [nhật ký tests](../report/test_results.md), [chốt ngày](../report/dnse_day_completion_2026-11-02.md), [AppTest](../report/dnse_dashboard_full_day_smoke_2026-11-02.json). Nghiệm thu nguồn học phần cần đối chiếu [đề gốc và quyết định đổi DNSE](assignment_compliance.md).

