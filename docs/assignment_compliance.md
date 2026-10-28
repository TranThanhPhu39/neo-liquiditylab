# Đối chiếu yêu cầu đồ án

| Yêu cầu | Hiện trạng | Bằng chứng / còn thiếu |
|---|---|---|
| Vấn đề đầu tư thực tế | Đã xác định | Quy mô bán vượt depth hiển thị; proceeds khác giá × lượng |
| NEO API là lõi | **Chưa đạt** | Boundary + kế hoạch; cần credentials/docs/data thật |
| Lưu stream hoặc hai nhóm endpoint liên quan | **Chưa đạt** | Không stream giả/không gọi API giao dịch |
| >=3 trường có ý nghĩa tài chính | Có trên demo và Vnstock thật | Bid/Ask giá/volume, last/ref; vẫn cần NEO thật |
| Thu–chuẩn hóa–lưu–xử lý | Có trên 66 snapshot Vnstock thật | 22 raw batch → hồ sơ đơn vị 1/1 → 66 normalized + lineage → engines |
| Logic tài chính kiểm chứng | Có | A1/B5 trên 66 snapshot; danh mục giả định; reverse/unit tests offline |
| Phần mềm tương tác | Có | Streamlit 3 màn hình tiếng Việt |
| >=3 KPI / >=2 dạng chart / filter / trạng thái / giải thích | Có | metrics, bar/line/hist, mã/ngày/source, demo/stale/no data |
| >=5 phiên, tốt hơn >=10 | **Chưa đạt** | Một phần cửa sổ chiều 28/10, 66 snapshot thật; không chứng nhận phiên đầy đủ |
| Asia/Ho_Chi_Minh | Có | aware UTC storage, local UI/report |
| Missing/duplicate/type/late/disconnect | Có cơ chế MVP | validation, unique event, run audit, replay arrival; stream thực pending |
| Không tự giao dịch thật | Có | Không order endpoints |
| Bảo mật | Có cơ chế MVP | env ignore, raw allowlist, sanitized SDK logs; chưa audit triển khai production |
| Tests và bằng chứng | 105 tests offline + kiểm chứng dữ liệu thực thu | 132 buy/sell, 66 stress; AppTest 3 màn hình replay; NEO/live execution chưa kiểm chứng |
| Tài liệu / báo cáo | Có | README + docs + outline, chưa viết báo cáo kết quả thực nghiệm giả |

MVP trước NEO hỗ trợ phát triển và demo; không tự chấm đạt toàn bộ 100 điểm. Hoàn thiện NEO và dữ liệu nhiều phiên là điều kiện trước nộp cuối kỳ.
