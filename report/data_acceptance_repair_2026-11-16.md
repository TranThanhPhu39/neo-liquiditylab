# Cập nhật xử lý sau nghiệm thu — 16/11/2026

**Đã sửa bộ thu SSI và cập nhật web với lịch sử quote đã xác minh đơn vị cho ACB/FPT/HPG: 13 ngày, 39 replay chính. Luồng Windows đã tự thu raw hôm nay; GitHub phát lịch quá muộn. Chưa nghiệm thu realtime cả phiên.** Báo cáo nghiệm thu ban đầu được giữ nguyên để truy vết.

## Dữ liệu hôm nay có tự thu không?

Tại lần kiểm tra khoảng 15:00 giờ Việt Nam, GitHub có workflow đang bật và Actions được cho phép, nhưng API trả về **0 lượt `event=schedule` trong ngày 16/11**. Những lượt hôm nay là `workflow_dispatch` do mình khởi chạy để kiểm tra và thu bù. Vì vậy chưa được gọi là đã tự thu theo cron.

**Cập nhật 15:32:** GitHub tạo lượt schedule [43832096296](https://github.com/TranThanhPhu39/neo-liquiditylab/actions/runs/43832096296) cho cron `37 1 * * 1-5` (08:37 Việt Nam) lúc **15:32:48**, trễ khoảng 6 giờ 56 phút. Job kết thúc Failure sau khi phiên sáng đã đóng. Guard theo phiên trong mã nguồn trả `FAILED_SESSION_WINDOW_ALREADY_CLOSED`; chưa có dữ liệu SSI nào được thu từ lịch sáng đó. CLI đã được bổ sung in JSON cho cả đường trả về sớm, để lượt sau không chỉ có exit 1 mà thiếu lý do trong log. Chưa đủ bằng chứng về nguyên nhân nội bộ khiến GitHub phát sự kiện muộn.

- Lượt [43823435962](https://github.com/TranThanhPhu39/neo-liquiditylab/actions/runs/43823435962) thu từ khoảng 14:23 đến 14:30: **1.953 quote, 1.689 trade**. Dừng vì lỗi topic chuyển phiên. Checkpoint cuối được tải lại, giải mã và kiểm tra Parquet thành công, rồi nhập vào kho SSI local.
- Lượt phục hồi [43824990785](https://github.com/TranThanhPhu39/neo-liquiditylab/actions/runs/43824990785) bắt đầu khoảng 14:36, dự kiến dừng 15:10. Đã qua mốc 14:45 mà không lỗi phạm vi. Checkpoint 14:51 được tải lại và kiểm tra độc lập: **716 quote, 930 trade; 1.838 bản ghi gồm metadata**. Đây là checkpoint giữa phiên, không phải kết quả cuối cùng.
- Kiểm tra 15:04: collector còn active, đã kết nối lần thứ hai và ghi một gap. Chưa có quote mới sau ATC để nghiệm thu nhận quote qua reconnect; không gọi đây là luồng cả phiên không mất dữ liệu.
- **Chốt lúc 15:10:** lượt phục hồi dừng đúng giới hạn thời gian, không lỗi parser; kết quả `PARTIAL_LATE_START`, 716 quote, 930 trade, **2 gap**, không quarantine. Checkpoint cuối **2.065 bản ghi** đã tải lại, giải mã, kiểm tra Parquet và nhập vào kho SSI local. Relay gửi hết, pending 0, errors 0. GitHub hiện **Failure** vì thu thiếu đầu phiên, không phải thất bại lưu dữ liệu. Hai lượt cloud cộng lại có 2.669 quote và 2.619 trade, vẫn không đủ cả ngày.
- Hai khoảng thu này không bao phủ cả ngày. SSI chưa có đường backfill lịch sử sổ lệnh được xác minh; không điền khoảng thiếu bằng 0.
- Task Windows `LiquidityLab - DNSE Daily Fallback` đã **tự chạy lúc 15:15:01 ngày 16/11**, không được khởi chạy thủ công trong đợt kiểm tra này. Lúc 15:42, database hôm nay có **2.077 trang, 729.742 raw quote**, 1.300 mã `COMPLETE`, 217 mã `NO_DATA_RETURNED`, không còn `PARTIAL/PENDING`. Luồng đã qua `collect`, `audit_raw`, `report_missing` và đang tiếp tục các bước chuẩn hóa/audit. Số `NO_DATA_RETURNED` không được tự đổi thành khối lượng 0.

## Những phần đã sửa

1. **Parser SSI:** thông điệp thực tế tại 14:30 là `market.FLAG`, gồm cờ HOSE/HNX/DER. Nhận cả viết hoa/thường, chuẩn hóa topic nội bộ và giữ `source_topic`; bỏ qua cờ sàn ngoài phạm vi. Quote/trade sai mã và kênh riêng tư vẫn bị chặn. Bốn thông điệp lỗi thực tế đã replay đạt.
2. **Guard lịch:** thêm kiểm tra bỏ lỡ giờ khởi động; không tạo collector trùng, có cooldown và giới hạn số lần thử. Đã sửa quyền đọc cờ cấu hình bằng cách truyền repo variables vào workflow. Lượt thử guard thành công trả `COLLECTOR_ALREADY_ACTIVE`. Chưa có bằng chứng guard tự chạy do cron.
3. **SSI units:** cài contract đã rà soát cho ba mã trên máy, tách quote/trade. FPT/HPG có hai trạng thái book sáu mức và hai giao dịch khác nhau khớp bảng Maybank với chú thích đơn vị rõ ràng. Nguồn tham chiếu trễ khoảng 5–17 giây, nên đây là kiểm chứng đơn vị; không sửa kết luận kiểm tra đồng thời ±5 giây và không chứng nhận độ trễ.
4. **Lịch sử DNSE:** đối chiếu riêng quote và trade với SSI sau khi SSI đã có neo đơn vị độc lập. Dựng bản dẫn xuất riêng cho **829.255 quan sát, 13 ngày, 39 replay**. Kiểm tra từng quan sát trước/sau và đọc lại Parquet: giá, lượng, thời gian, mã và event ID giữ nguyên; tính lại chất lượng và điều kiện phân tích từ contract mới. Kho cũ không bị ghi đè. Không tuyên bố đã đối chiếu lại mọi raw row trong đợt này.
5. **Replay sự kiện:** khi mở detector FPT, metadata sự kiện vượt giới hạn JSON 20 MB. Chuyển event patches sang Parquet, giữ đủ dữ liệu, tiếp tục đọc định dạng cũ. API hosted stream cả frames và patches qua gzip theo batch.
6. **Sao lưu:** tạo bản tăng dần **201.536.828 byte**, giải nén đọc lại và kiểm tra đủ **1.079 tệp**. Bản này phụ thuộc bản nền 12/11; đã kiểm tra hash bản nền. Chưa tuyên bố đã thử khôi phục toàn bộ chuỗi rồi xây lại toàn bộ canonical. Chưa xóa nguồn hoặc upload bản sao này.

## Kết quả và giới hạn còn lại

- Bộ kiểm thử cuối sau các thay đổi parser, revision, stream, phục hồi tham số và CLI: **500 passed trong 43,83 giây**. Tùy chọn CLI chọn kho dữ liệu riêng đã được kiểm tra `--help` và API dữ liệu thật.
- Gói lịch sử mới: **216.019.466 byte sau khôi phục, 200.261.346 byte ciphertext**, 366 tệp, 39 replay; đã thử khôi phục và chạy lại phân tích ACB ngày 13/11. Các gói giao dịch cũ được giữ nguyên; ngày 12–13/11 chưa có trade package trong kho cũ, nên không có bubbles giao dịch cho hai ngày đó.
- Đã chạy API HTTP/gzip thật trên localhost với ngày 13/11: ACB 10.931 frame / 7.601 quan sát đủ điều kiện, FPT 38.034 / 35.385, HPG 13.150 / 10.866. Đọc đủ toàn bộ event patches, không cắt lấy mẫu. Đây là kiểm tra backend bằng dữ liệu thật, chưa phải nghiệm thu trực quan trên trình duyệt.
- Gói mới đã được chấp thuận, **upload ciphertext lên GitHub Releases, tải lại/giải mã kiểm tra, deploy Render và phục hồi READY**. Web có 39 replay chính, thêm một phiên bản phân tích đã lưu, tổng 40 mục. Bộ tham số cũ được tính lại với ID mới và lưu ánh xạ phiên bản; không tự chuyển ghi chú giữa các ID.
- Chứng nhận SSI **13.206 byte đã lưu thành Actions Secret**. Lượt kiểm tra [43831303824](https://github.com/TranThanhPhu39/neo-liquiditylab/actions/runs/43831303824) đạt trên runner mới, xác minh quote/trade cả ba mã mà không cấp khóa môi giới cho job kiểm tra. Các capture cũ giữ nguyên contract/nhãn tại thời điểm thu; không tự nâng nhãn hồi tố.
- Các gói giao dịch khớp DNSE cũ giữ nguyên bằng chứng/nhãn riêng. Ví dụ HPG còn `ASSUMED_SHARED_SCHEMA`; đợt revision lịch sử này cập nhật quote, không tự nâng nhãn các gói bubbles cũ. Ngày 12–13/11 chưa có trade package trong kho cũ.
- Cờ nguồn `C` được giữ nguyên, **không đổi thành `LO`**. Detector realtime vẫn bị khóa khi đơn vị hoặc phiên liên tục chưa đủ bằng chứng. Việc thu và lưu quote/trade vẫn hoạt động.
- Chưa kiểm chứng cả phiên sáng/chiều, reconnect trong điều kiện thật hoặc completeness của nhà cung cấp. Các detector vẫn mang tính thử nghiệm, chưa phải kết luận thao túng.

## Sử dụng bản lịch sử mới trên máy

```powershell
.\.venv\Scripts\python.exe -m scripts.serve_orderbook_monitor --port 8878 --data-root data/control/verified-monitor-history/eb7906b42b3d436d945665e5368a3912
```

Mở `http://127.0.0.1:8878/`. Kho gốc và bản lịch sử đang dùng trên web không đổi.

## Bằng chứng

Các tệp JSON dưới đây nằm trên máy, không được đính kèm bản báo cáo trong repo công khai.

- [Bản lịch sử đã dựng](verified_monitor_history_latest.json)
- [Kiểm tra API cả ba mã](revised_history_http_2026-11-16.json)
- [Kiểm tra ba mã trên Render](hosted_acceptance_2026-11-16.json)
- [Windows tự thu ngày 16/11](windows_auto_collection_2026-11-16.json)
- [Lịch GitHub phát muộn](ssi_late_schedule_2026-11-16.json)
- [Chứng nhận SSI đạt trên runner](ssi_contract_runner_check_2026-11-16.json)
- [Gói mã hóa và kiểm tra khôi phục](online_monitor_preparation_2026-11-16.json)
- [Chứng nhận SSI local](ssi_units_promotion_2026-11-16.json)
- [Replay bản tin chuyển phiên](ssi_transition_replay_2026-11-16.json)
- [Checkpoint trước lỗi](ssi_cloud_checkpoint_2026-11-16.json)
- [Checkpoint lượt phục hồi](ssi_cloud_periodic_checkpoint_2026-11-16.json)
- [Checkpoint cuối 15:10](ssi_cloud_final_checkpoint_2026-11-16.json)
- [Bản sao tăng dần](recovery_incremental_latest.json)

Các chấp thuận upload gói lịch sử và lưu chứng nhận đã được nhận, thực hiện và kiểm chứng. Trang giới thiệu vẫn công khai; API và các trang dữ liệu vẫn dùng mật khẩu hiện có. Chưa xóa dữ liệu gốc.
