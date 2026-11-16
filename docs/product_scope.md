# Phạm vi mới — LiquidityLab: theo dõi và xem lại sự kiện thanh khoản

**Điều chỉnh theo yêu cầu 16/11:** mở rộng **lịch sử quan sát sổ lệnh** ra toàn bộ danh mục DNSE, gồm HOSE/HNX/UPCOM, có tìm mã, lọc sàn và phân trang. Phạm vi detector đã nghiệm thu đơn vị và SSI subscription vẫn là ACB/FPT/HPG; các mã còn lại chỉ quan sát, không tự mở khóa detector. Đây là ngoại lệ thay thế giới hạn “không toàn thị trường” của kế hoạch 12/11 bên dưới, không phải cam kết realtime toàn thị trường. Xem [phạm vi và cách dùng mới](market_monitor.md).

Quyết định ngày 12/11/2026 theo đề xuất người dùng gửi. Người thực hiện xác nhận **làm một mình, hạn nộp 01/12/2026**. Khoảng 19 ngày còn lại thay cho giả định 10 tuần/ba người trong bản đề xuất. **Ưu tiên đã điều chỉnh: hoàn thiện sản phẩm offline trên dữ liệu cũ trước, sau đó mới nối realtime.** Bản local v2 có ba màn hình Theo dõi / Phân tích mã / Rà soát, D1/D2/D3, replay/evidence, review giữ lịch sử, phiên bản ngưỡng và xuất hồ sơ. Dữ liệu gồm 11 ngày, ba mã DNSE; detector FPT/HPG bị chặn do đơn vị chưa xác minh. Xem [hướng dẫn và giới hạn nghiệm thu](event_monitor_runbook.md). Chưa nối SSI live, deploy hoặc nghiệm thu giá trị/hiệu chỉnh; hình thức browser thật chưa kiểm tra được.

## Đề tài dự kiến

**Xây dựng nền tảng trực quan hóa sổ lệnh và phát hiện bất thường thanh khoản trên thị trường chứng khoán Việt Nam.**

Giữ tên repo và tài nguyên LiquidityLab hiện có. “Bất thường” chỉ có nghĩa vượt quy tắc mô tả được công bố, chưa phải gian lận, thao túng hoặc tín hiệu đầu tư.

## Công việc người dùng và kiểm chứng

Người dùng ứng viên: người theo dõi một watchlist trong phiên, môi giới hoặc người phân tích cần dựng lại một diễn biến đã bỏ lỡ.

Giả thuyết công việc: “Khi không thể nhìn liên tục nhiều mã, tôi cần tìm nhanh một đoạn thanh khoản thay đổi, mở lại trước–trong–sau và xem vì sao hệ thống đánh dấu.”

Chưa có phỏng vấn hoặc kết quả chứng minh nhu cầu. Trong giai đoạn đầu cần xin tình huống thực và so tác vụ bằng công cụ họ đang dùng với prototype; không dùng nhận xét ‘biểu đồ đẹp’ làm nghiệm thu giá trị.

## Hai sản phẩm tham khảo

- Bookmap: heatmap theo giá–thời gian, best bid/ask và tách book đang chờ với giao dịch đã khớp. [Tài liệu chính thức](https://bookmap.com/knowledgebase/docs/KB-SettingUpAndOperating-HeatmapMainChart).
- TT Trade Surveillance: học quy trình gom sự kiện, mở bằng chứng, replay và ghi nhận việc rà soát. TT phân tích audit trail của trader; dự án chỉ có dữ liệu thị trường tổng hợp, không sao chép khả năng kết luận hành vi theo tài khoản. [Cách TT hoạt động](https://library.tradingtechnologies.com/tt-trade-surveillance/overview-tt-trade-surveillance/how-tt-trade-surveillance-works/), [ghi nhận rà soát](https://library.tradingtechnologies.com/tt-trade-surveillance/using-tt-trade-surveillance/recording-reviews-and-opening-investigations/).

## Luồng sản phẩm

**Theo dõi watchlist → chọn sự kiện → heatmap đúng mã/thời gian → phát lại trước–trong–sau → đọc bằng chứng → ghi chú/đánh giá.**

Ba màn hình logic: Theo dõi; Phân tích mã; Xem lại sự kiện. Có thể triển khai trong một ứng dụng với các panel liên kết; không bắt buộc ba route hoặc ba dịch vụ.

## Phạm vi phù hợp với một người/19 ngày

| Thành phần | Mục tiêu nghiệm thu |
| --- | --- |
| Nguồn | SSI V3 market data; không gọi API giao dịch/tài khoản |
| Mã/sở | ACB đầu tiên; mở tối đa ACB/FPT/HPG trên HOSE sau kiểm chứng. Ba mã là giới hạn kế hoạch, không khẳng định đơn vị ba mã đã xác minh |
| Phiên | Chỉ phiên liên tục; loại nghỉ trưa, đấu giá và trạng thái không đủ điều kiện |
| Dữ liệu | Lưu raw/receipt, chuẩn hóa riêng SSI, giữ source/receive clocks, cờ chất lượng và gap |
| Trực quan | Canvas heatmap 2D, best bid/ask, bảng độ sâu và chỉ tiêu có phạm vi |
| Replay | Play/pause, đổi tốc độ, tua tới sự kiện, không nhìn tương lai trong chế độ phát tuần tự |
| Detector 1 | Spread mở rộng có baseline quá khứ, thời gian duy trì, gom sự kiện và giải thích |
| Detector 2 | Depth quan sát giảm, chỉ sau khi đủ điều kiện scope/matching mức giá; không gọi thay đổi top K là hủy lệnh |
| Imbalance | Chỉ tiêu mô tả trước; detector kéo dài là phần mở rộng nếu còn thời gian và đạt đánh giá |
| Review | Nhãn hữu ích/không hữu ích/vấn đề dữ liệu/chưa đủ thông tin; ghi chú và lịch sử chỉnh sửa tối thiểu |
| Live | Collector trên máy và giao diện mới nhận cập nhật trong phiên, báo gap/reconnect; cùng lõi feature/detector với replay |
| Bàn giao | Tình huống thực, fixtures kiểm soát, kết quả kiểm thử/đánh giá người dùng, tài liệu vận hành và video/demo dự phòng |

Không đưa vào MVP mới: B5, màn hình 3D, ML, dự báo giá, phát hiện spoofing/wash trading/đội lái, lệnh tự động, nhiều sở/toàn thị trường, thông báo ngoài ứng dụng, quy trình compliance nhiều cấp.

A1 giữ trong mã như thông tin phụ và bộ tính cũ; không là luồng chính. B5 và 3D cũ không bị xóa, nhưng không chiếm ưu tiên nghiệm thu mới. Việc thu DNSE hằng ngày và web cũ không bị tự tắt hoặc sửa trong bước đổi phạm vi này.

## Độ chính xác của cam kết

- Không gọi mười mức nhận được là toàn bộ thị trường hoặc full depth của sở.
- Không có trades riêng thì không có volume dots; không tách chênh lệch volume lũy kế thành giao dịch.
- Không có order ID/sequence thì không dựng vị trí hàng đợi hoặc chứng nhận không mất upstream event.
- Không đủ đơn vị/phiên/đồng bộ thì chỉ chẩn đoán dữ liệu; detector production phải bị chặn.
- Khả năng xem lại dựa trên dữ liệu đã thu; không hứa tải bù mọi đoạn SSI đã bỏ lỡ.
- Chưa mặc định được công bố dữ liệu SSI. Chuẩn bị demo local trước, xác minh điều kiện sử dụng/hiển thị trước khi công khai mẫu SSI.

## Kiến trúc và vận hành

Tái sử dụng Python, SQLite, kho raw/audit và web JS hiện có; thêm miền quan sát/sự kiện SSI độc lập. Không chuyển toàn bộ sang React + FastAPI + Parquet + DuckDB + PostgreSQL + Docker chỉ vì bản tham khảo liệt kê các lựa chọn đó. Những công nghệ này là tùy chọn sau khi đo nhu cầu, không phải tiêu chí đồ án hiện tại.

Backend local tối thiểu chỉ phục vụ dữ liệu ứng dụng được phép, không expose thư mục gốc/.env. Browser không cầm khóa SSI. Luồng thu/feature/detector không phụ thuộc tab trình duyệt; replay dùng cùng lõi với live. Events/reviews có schema/version riêng, không sửa nhãn hoặc lineage DNSE cũ.

Giữ GitHub Pages cũ làm bản lịch sử hiện có. Pages tĩnh không thay backend thu live. Không tự đưa khóa SSI lên GitHub, sửa cron hoặc triển khai hệ thống mới trong bước cập nhật phạm vi.

## Tài liệu triển khai

- [Khả năng SSI và điều kiện dữ liệu](ssi_source_capabilities.md).
- [Đặc tả bộ phát hiện](detector_specs.md).
- [Kế hoạch tới hạn nộp, tái sử dụng và đánh giá](event_monitoring_delivery.md).

Thứ tự ưu tiên hiện tại: luồng offline hoàn chỉnh trên dữ liệu cũ → rà soát và hiệu chỉnh có đối chứng → nối realtime đáp ứng contract → kiểm chứng vận hành → hoàn thiện báo cáo. Không bỏ evidence/quality/review để chuyển sớm sang live. Realtime là adapter cho luồng đã có, không thay mục tiêu của sản phẩm.
