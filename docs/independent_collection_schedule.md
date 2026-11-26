# Lịch dự phòng độc lập với GitHub cron

Kiểm tra ngày 18/11 lúc 13:29 giờ Việt Nam: SSI, DNSE và SSI guard đều active, cờ thu bật, nhưng không có run được tạo từ lịch trong ngày. Dispatch trực tiếp được GitHub tiếp nhận và SSI thực sự nhận quote/trade, nên đường khởi động từ workflow_dispatch hoạt động. API không cho biết nguyên nhân nội bộ vì sao cron chưa phát sự kiện; không quy lỗi này cho broker hoặc khẳng định GitHub quá tải.

## Ba lớp lịch hiện có

| Lớp | Lịch giờ Việt Nam | Vai trò |
| --- | --- | --- |
| GitHub cron SSI/DNSE | SSI prewarm 08:17 và 12:12; DNSE giữ lịch hiện có | Nguồn lịch chính, có thể trễ/thiếu sự kiện |
| Windows Realtime Dispatch Guard | Thứ Hai–Sáu, 08:15–15:35, mỗi 5 phút; thêm lúc đăng nhập | Kiểm tra/kích hoạt GitHub qua workflow_dispatch bằng đồng hồ Windows |
| Windows DNSE Daily Fallback | Thứ Hai–Sáu, từ 15:15 lặp mỗi 30 phút trong 8 giờ; thêm lúc đăng nhập | Tải bù ngày đã đóng, chuẩn hóa/audit, xuất bản theo chính sách hiện có |
| Windows SSI Local Session Fallback | Thứ Hai–Sáu, từ 08:52 lặp mỗi 5 phút đến 15:12; thêm lúc đăng nhập | Thu SSI trực tiếp trên máy khi thiếu bằng chứng cloud hoạt động; cửa sổ chiều bắt đầu 12:47 |

Guard Windows chỉ phát lệnh khởi động khi không có collector queued/running, không quá hai lần thử trong cửa sổ phiên và cách lần thất bại gần nhất ít nhất 10 phút. Giữ cờ tắt/stop file và lịch nghỉ được cấu hình. SSI/DNSE xử lý độc lập: lỗi một nguồn không chặn kiểm tra nguồn kia.

**Sửa missed-start SSI ngày 25/11:** mở cửa sổ prewarm trước giờ thu 35 phút, thêm ngày dự kiến vào dispatch; worker từ chối dispatch sang ngày khác hoặc quá sớm so với cửa sổ. Run network/smoke không chặn collector. Run chưa chạy của phiên cũ được hủy để giải phóng hàng đợi; run đang ghi không bị hủy. Một run của phiên hiện tại còn xếp hàng quá 15 phút được thử thay thế sau giờ bắt đầu thu, trong giới hạn hai lần thử. Việc vừa hủy run xếp hàng không bắt chờ thêm cooldown thất bại.

`queued`/`in_progress` chỉ là trạng thái GitHub. Guard ghi riêng `worker_start_verified=false` cho đến khi kiểm tra `/api/live/status` thấy relay mới, phiên/ngày đúng và đã kết nối. Sau 09:00/13:00, thiếu bằng chứng khởi động hoặc đã bắt đầu trễ sẽ trả trạng thái cần chú ý và mã thoát khác 0. Nhận quote của cả ba mã cũng chưa xác nhận đầy đủ mọi tick trong phiên.

Worker vẫn thu từ 08:50/12:45; mốc prewarm chỉ dành cho chuẩn bị runner. Báo cáo worker cập nhật heartbeat mỗi phút, ghi first connection, first quote từng mã, số gap theo nguyên nhân và thời lượng ngắt đã quan sát. Các lần nối lại để làm mới token vẫn được giữ là gap; không đổi `PARTIAL_CONNECTION_GAPS` thành kết quả đủ phiên.

DNSE được làm mới danh mục từ REST và lưu checkpoint danh mục mã hóa trước khi kích hoạt nếu chưa có catalog đã xuất bản của ngày hiện tại. Không kéo dài tuổi catalog bằng cách đổi ngày trên danh sách cũ. Vì vậy lỗi danh mục hết hạn bảy ngày được tránh bằng tải thật, không gỡ kiểm tra tuổi dữ liệu.

Ngày mới chưa đến 15:15 không được tải như một ngày hoàn chỉnh. Các lần kiểm tra sau 15:15 giải quyết trường hợp tác vụ cũ còn xuất bản tại mốc 15:15: lần này bị IgnoreNew thì lần 15:45/16:15 tiếp theo vẫn có thể bắt kịp. Không khẳng định chắc chắn chạy đúng 15:15 nếu máy tắt, tác vụ cũ còn chạy hoặc mạng lỗi.

## Điều kiện vận hành

Windows guard cần máy này bật, có mạng và người dùng hiện tại đăng nhập. Các bộ thu sau khi đã khởi động tiếp tục chạy trên runner GitHub đến hết cửa sổ phiên, độc lập với việc giữ cửa sổ terminal mở. Cơ chế này không giải quyết việc kích hoạt khi máy đã tắt trước giờ phiên; muốn độc lập hoàn toàn cần một bộ lập lịch bên ngoài luôn hoạt động. Không tạo dịch vụ trả phí hoặc đưa GitHub token sang bên thứ ba trong thay đổi này.

**Bộ thu local được người dùng chấp thuận ngày 25/11:** giữ máy bật, có mạng, không Sleep và đăng nhập từ 08:15 đến 15:10; cần chờ upload cuối phiên hoàn tất nếu sắp tắt máy. Khi cloud có kết nối mới đúng phiên và đã nhận dữ liệu sau giờ mở cửa, local không mở thêm bản thu. Nếu thiếu bằng chứng đó, local thu cùng ba mã bằng khóa SSI trên máy đến 11:35/15:10. Local tự giữ máy thức trong lúc đang thu, giữ nguyên đơn vị/clock/gap và lưu Parquet dưới `data/realtime/ssi/<ngày>/<run_id>`.

Local không gửi vào luồng relay đang do GitHub ghi. Mỗi 15 phút và cuối phiên, bản thu local được mã hóa bằng khóa khôi phục hiện có, upload lên Releases theo session `morning-local`/`afternoon-local`, kiểm tra giải mã và Parquet. Backend khôi phục các bản thu riêng qua chu kỳ checkpoint; đây là lịch sử dự phòng có thể trễ khoảng một chu kỳ upload cộng một chu kỳ recovery, không phải luồng trực tiếp 5 giây. Bản relay GitHub đang hoạt động vẫn được ưu tiên trên web; các bản thu không bị ghép thành một phiên đủ giả tạo.

Khi GitHub không truy cập được, Parquet vẫn tiếp tục lưu trên máy theo cờ bật local đã được chấp thuận. Bản upload lỗi giữ báo cáo riêng theo `run_id`, rồi được thử lại lúc task tiếp tục hoạt động, kể cả ngoài giờ thu. Repo báo cờ thu tắt thì không bắt đầu bản thu mới; `data/control/STOP_SSI_LOCAL` dừng riêng local. Sau hai lần thu kết thúc sớm trong cùng phiên, cần kiểm tra nguyên nhân trước khi thử thêm.

Task dùng Interactive/Limited, không lưu mật khẩu vào Task Scheduler. GitHub credential được đọc từ cơ chế Git đã có của người dùng; không nhúng token vào file task, command line, source hoặc log. Không có API giao dịch.

## Cài và kiểm tra

```powershell
.\.venv\Scripts\python.exe -m scripts.install_cloud_guard_task
.\.venv\Scripts\python.exe -m scripts.install_dnse_day_task
.\.venv\Scripts\python.exe -m scripts.install_cloud_guard_task --verify-only
.\.venv\Scripts\python.exe -m scripts.install_dnse_day_task --verify-only
.\.venv\Scripts\python.exe -m scripts.install_ssi_local_task --verify-only
```

Chạy kiểm tra ngay (không tạo bản trùng khi collector đang chạy):

```powershell
.\.venv\Scripts\python.exe -m scripts.scheduled_cloud_guard
```

Báo cáo hoạt động:

- `report/automation/cloud_dispatch_status.json`: kết quả kiểm tra/kích hoạt từng provider.
- `report/automation/cloud_dispatch_entry.json`: thời gian task thực sự bắt đầu/kết thúc và đường dẫn log.
- `report/automation/logs/cloud_guard_YYYY-MM-DD.log`: log an toàn của launcher chạy ẩn; không chứa khóa.
- `report/automation/ssi_local_task_registration.json`: lịch dự phòng SSI thực đã đăng ký.
- `report/automation/ssi_local_task_last.json`: kết quả lần gọi task local gần nhất.
- `report/automation/ssi-local/<ngày>-<phiên>.json`: heartbeat, phạm vi thu, chất lượng và kết quả checkpoint.
- `report/automation/ssi-local-archives/<run_id>.json`: từng bản thu và hàng đợi upload giữ qua những lần thử.
- `report/automation/cloud_dispatch_task_registration.json`: lịch Windows thực đã đăng ký.
- `report/automation/dnse_task_registration.json`: lịch cuối ngày và các mốc lặp.
- `report/dnse_ws_catalog_preparation.json`: ngày danh mục thật và tag checkpoint mã hóa.

Tạm dừng kích hoạt GitHub bằng cách tạo `data/control/STOP_GITHUB_DISPATCH`; không tự dừng các runner đã chạy. Tác vụ thu ngày DNSE có stop file `data/control/STOP_DNSE_HISTORY` riêng. Xóa stop file chỉ khi muốn cho phép tiếp tục.

## Khôi phục ngày 18/11

- SSI dispatch 13:31: run 44251912701, web nhận quote/trade thật cho ACB/FPT/HPG.
- Catalog DNSE làm mới ngày 18/11, 1.517 mã, đã xuất bản mã hóa.
- DNSE dispatch 13:35: run 44252492913. Trạng thái chạy không tự chứng minh đã thu đủ dữ liệu; cần kiểm tra checkpoint/kết quả cuối phiên riêng.
- Chạy thử task Windows lúc 13:41: LastTaskResult=0, cả hai provider COLLECTOR_ALREADY_ACTIVE, không tạo run trùng.
- Bộ kiểm thử liên quan: 75/75 đạt tại máy.
- Phần phiên sáng và đầu phiên chiều bị lỡ không được tuyên bố đã phục hồi realtime. Lịch tải REST cuối ngày là dữ liệu lịch sử riêng, tùy API hỗ trợ và không phục hồi receipt/gap WebSocket đã bỏ lỡ.

## Nghiệm thu dữ liệu theo ngày

Sau khi lịch Windows hoàn tất lượt tải/bù DNSE và cập nhật dashboard, nó chạy kiểm tra cho ngày giao dịch đóng gần nhất, kể cả khi có ngày tải lỗi. Kết quả ghi vào `report/automation/daily-delivery/YYYY-MM-DD.json`. Có thể kiểm tra lại bất kỳ ngày nào đã lưu bằng lệnh sau.

```powershell
.\.venv\Scripts\python.exe -m scripts.audit_daily_delivery --date 2026-11-25
```

- DNSE đối chiếu số quan sát gốc, dòng liên kết và snapshot Parquet. Báo cáo xác nhận checkpoint đã giải mã và đối chiếu từng mã trên dashboard với bản chiếu, gồm mã có quan sát, mã thiếu và một số replay mở trực tiếp.
- SSI ghi riêng phiên sáng, phiên chiều và nguồn GitHub/Windows. Mỗi checkpoint được tải, giải mã và kiểm tra các dòng Parquet trước khi đối chiếu số quote/trade và mở lại bản thu trên web.
- `processing_state` phản ánh kết quả chuyển dữ liệu. `market_coverage` ghi tình trạng phiên, kể cả khi bản thu partial chuyển lên web thành công. Mã `NO_DATA_RETURNED` là thiếu quan sát từ nguồn. Phiên chưa đóng hoặc đang phục hồi sẽ có trạng thái chờ.
