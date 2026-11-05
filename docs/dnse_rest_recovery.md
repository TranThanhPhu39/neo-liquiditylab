# Phục hồi lịch GitHub đến trễ bằng REST

Workflow DNSE giữ cron 08:52 và 12:52 giờ Việt Nam. Cron GitHub có thể trễ hoặc chưa tạo run; code chỉ phục hồi sau khi đã có runner thực thi. Không khẳng định sửa được bộ lập lịch của GitHub.

## Hành vi

| Tình huống | Xử lý |
| --- | --- |
| Đến trong cửa sổ chuẩn bị trước phiên | Chờ và thu WS như trước |
| Đến giữa phiên | Thu WS phần còn lại, sau phiên thử REST để bù lịch sử |
| Lịch sáng đến sau 11:31 | REST 00:00–11:30 của ngày hiện tại, không chuyển thành phiên chiều |
| Lịch chiều đến sau 15:06 | Đợi đến 15:10, REST 00:00–15:10 của ngày hiện tại |
| Quá sớm hoặc cuối tuần | Báo lỗi, không gọi nguồn dữ liệu |
| Chọn mode backfill thủ công | Chọn morning hoặc day; chỉ chạy khi phiên đã đóng, không gọi WS |

Ngày nghỉ lễ chưa có lịch riêng. Job đến sau nửa đêm không biết ngày lịch bị lỡ từ API hiện có; không tự đoán và tải ngày trước. Chế độ backfill của workflow hiện chỉ nhận ngày hiện tại; ngày cũ tiếp tục dùng `scripts.update_dnse_day --date YYYY-MM-DD` trên máy.

## Lưu và tiếp tục

REST dùng lại `scripts.collect_dnse_history`: GET danh mục ST thuộc HOSE/HNX/UPCOM, GET quotes G1, phân trang theo token, kiểm tra mã/sàn/board/thời gian và lưu raw cùng thời điểm nhận thật. Không gọi tài khoản hoặc giao dịch. Nguồn trả rỗng giữ `NO_DATA_RETURNED`, không sinh snapshot số 0.

Namespace Releases mới **dnse-rest-history**, partition `YYYY-MM-DD-morning` hoặc `YYYY-MM-DD-day`, tách WS. AES-256-GCM dùng `LIQUIDITYLAB_STATE_KEY` sẵn có; không đưa API key, signature, raw plaintext hoặc DB plaintext vào artifact/Pages. Báo cáo công khai chỉ gồm trạng thái, số lượng, nhãn nguồn và mã lỗi đã lọc. Tên tag/ngày và manifest kích thước/hash vẫn công khai như checkpoint cũ.

Mỗi lượt tải tối đa 6.000 request quote sau khi lấy danh mục, 90 phút vòng tải; retry và request đang thực hiện có thể làm quá ngưỡng thời gian một khoảng ngắn. Quota/rate gate, dung lượng đĩa và STOP vẫn được giữ. Có checkpoint sau khoảng 5 phút khi tiến độ cập nhật và khi kết thúc; nếu runner bị kill, phần chưa checkpoint có thể mất. GitHub timeout tăng từ 195 lên 355 phút để có chỗ cho WS, tải bù và lưu/audit. Không tự xóa Releases cũ.

Khôi phục checkpoint gần nhất trước khi tiếp tục: đã tải trang nào giữ nguyên trang đó, không bắt đầu lại mã hoàn tất. Tải hoàn tất chỉ được ghi nhận khi toàn danh mục đã hết trang, checksum/chuỗi trang và SQLite audit đạt, và checkpoint cuối đã lưu. Điều này không độc lập chứng nhận nguồn DNSE trả đủ mọi sự kiện thực tế.

## Trạng thái

- `REST_BACKFILL_COMPLETED`: mode backfill thủ công hoàn tất lịch sử REST.
- `RECOVERED_REST_HISTORY_REALTIME_MISSED`: tải bù hoàn tất sau lượt realtime trễ; `realtime_window_missed=true`, `realtime_recovered=false`. Giữ kết quả WS riêng nếu đã thu một phần.
- `FAILED_REST_BACKFILL`: tải bù chưa hoàn tất hoặc audit/lưu lỗi. Job trả exit code 1; xem `collection_state`, `error_code`, `checkpoint_error` và `checkpoint`.
- Các trạng thái bỏ qua, tạm dừng, dừng và không nhận diện không được coi là thành công. Summary dùng `if: always()` nên lỗi vẫn có báo cáo.

REST trên GitHub từng lỗi trước TLS; fallback sẽ thử thực sự và báo lỗi nếu vẫn không kết nối. Không tự đổi IP, proxy, bỏ TLS hoặc chuyển sang dữ liệu giả. Chưa có bằng chứng để coi việc tải bù từ runner đã chạy thành công toàn thị trường.

## Dùng trên máy

```powershell
.\.venv\Scripts\python.exe -m scripts.sync_dnse_rest_history --date 2026-11-05 --session day
.\.venv\Scripts\python.exe -m scripts.update_dnse_day --date 2026-11-05
```

Chọn `--session morning` cho cả hai lệnh nếu lấy phiên sáng. Lệnh khôi phục từ chối ghi đè DB hoặc universe hiện có, kiểm tra ngày/phiên/board và tạo lại universe từ DB. Kho tải dở vẫn có thể khôi phục để `update_dnse_day` chạy tiếp; kho đủ sẽ được dùng lại không gọi thu mới. Giữ bằng chứng đơn vị và khóa giải mã cục bộ. REST raw này chưa tự chuẩn hóa, không mở khóa tính toán, không tự cập nhật Pages.

Trên Actions: Run workflow → mode `backfill`, chọn backfill_session, giữ rest_max_requests=6000 để tiếp tục tải; chọn 1 để thử hữu hạn. Giới hạn này đếm request quote, ngoài request danh mục và retry đã được ghi nhận.
