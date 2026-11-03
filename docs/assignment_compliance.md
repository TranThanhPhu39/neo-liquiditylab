# Đối chiếu yêu cầu đồ án — cập nhật 03/11/2026

Nguồn chính là **DNSE OpenAPI**; kế hoạch tích hợp NEO VPBankS đã dừng. [Quyết định nguồn](data_source_decision.md) phân biệt thiết kế hiện tại với điều kiện NEO trong đề gốc. Người thực hiện đã xác nhận việc dùng DNSE được thống nhất ngày 03/11; nghiệm thu theo phạm vi DNSE đã điều chỉnh.

| Yêu cầu | Hiện trạng | Bằng chứng / còn thiếu |
| --- | --- | --- |
| Vấn đề đầu tư thực tế | Đã triển khai | Quy mô lệnh so ba mức giá, chi phí quét sổ, thanh khoản danh mục giả định. |
| API là nguồn cốt lõi | DNSE là nguồn chính | REST thật, dữ liệu trong Dashboard/A1/B5 theo phạm vi xác minh. DNSE thay điều kiện nguồn NEO theo xác nhận của người thực hiện. |
| Stream lưu hoặc hai nhóm endpoint liên quan | Có REST và probe WS với mẫu lưu cục bộ | Danh mục/lịch sử quotes và probe WS G1 phục vụ bài toán; đã lưu tám mẫu WS theo allowlist trên máy, chưa thu stream dài phiên/toàn thị trường. |
| Ít nhất ba trường tài chính | Có | Giá/lượng bid và ask ba mức, thời gian nguồn; không bịa last/ref khi quotes không cung cấp. |
| Thu–chuẩn hóa–lưu–xử lý | Đã chạy với DNSE | 4.367.925 vị trí raw từ năm kho → 3.746.982 snapshot duy nhất, giữ lineage/raw. Kho sáng 29/10 trùng phạm vi kho cả ngày. |
| Logic tài chính kiểm chứng | Có trong phạm vi nêu rõ | A1/B5, Decimal, partial coverage, phí/thuế giả định, danh mục nhập tay; DNSE hiện mở cho ACB đủ điều kiện. |
| Phần mềm tương tác | Có | Streamlit ba màn hình, Replay, bộ lọc nguồn/ngày/mã/thời điểm. |
| Ít nhất ba KPI / hai dạng chart / trạng thái / giải thích | Có trên MVP | Depth, spread, OBI, coverage, VWAP/cost; biểu đồ đường/cột, cảnh báo. Cần kiểm tra trực quan và ảnh demo. |
| Nhiều ngày cho phân tích thời gian | Bốn ngày DNSE | 28, 29, 30/10 và 02/11. Chưa đạt mốc ít nhất năm ngày đề khuyến nghị; mục tiêu tốt hơn là mười ngày trở lên. |
| Múi giờ Việt Nam | Có | UTC aware, ngày/giờ nguồn và UI giờ Việt Nam; thời điểm nhận giữ riêng. |
| Missing/duplicate/type/late/disconnect | Có cơ chế REST/offline | Phân trang, checkpoint, validation, gộp trùng với lineage, giữ bản ghi trễ, dừng lỗi và Replay; chưa nghiệm thu live stream. |
| Không tự giao dịch thật | Có | Client chỉ cho route thị trường đọc; simulated holdings. |
| Bảo mật | Có cơ chế MVP | Env/Secrets, lọc log, dữ liệu riêng không đưa lên source public; chưa audit production. |
| Tests và bằng chứng | 197 tests ở lần hồi quy gần nhất | Audit 02/11 đạt; 80 kiểm tra thực hiện lệnh/40 snapshot đủ điều kiện; AppTest ba màn hình ngày mới đạt. |
| Tài liệu/báo cáo | Đã có, đang hoàn thiện | Tài liệu DNSE, báo cáo ngày; phân tích hiện dùng 28–29/10. Cần nghiên cứu nhiều ngày, báo cáo cuối, demo và slide. |

Audit hiện tại: 0 cách ly, SQLite integrity ok, 0 lỗi khóa ngoại. Đây là kiểm tra nhất quán dữ liệu/số học, không chứng nhận upstream đầy đủ hoặc khớp lệnh thực tế. ACB có bằng chứng đơn vị thực nghiệm; các mã khác giữ ASSUMED_SHARED_SCHEMA, chưa mở A1/B5 mặc định.

Tự chạy: task Windows Disabled, GitHub LIQUIDITYLAB_WORKER_ENABLED=false theo kiểm tra 03/11. DNSE sau phiên đang chạy thủ công trên máy. Xem [chốt ngày](../report/dnse_day_completion_2026-11-02.md), [nhật ký tests](../report/test_results.md).



