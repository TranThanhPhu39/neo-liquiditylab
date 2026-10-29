from decimal import Decimal

from src.data_pipeline.validation import validate
from src.models.execution import ExecutionResult, Fill


def rate(value, name="rate", minimum=Decimal(0), maximum=Decimal(1)):
    value = Decimal(str(value))
    if not value.is_finite() or not minimum <= value <= maximum:
        raise ValueError(f"{name} ngoài khoảng [{minimum}, {maximum}]")
    return value


def simulate_execution(snapshot, requested_quantity, side="sell", fee_rate=0, sell_tax_rate=0, benchmark="best"):
    if snapshot.provider=="dnse":
        if any(issue.startswith('INVALID_DNSE:') for issue in snapshot.issues):
            raise ValueError("Snapshot DNSE chứa giá hoặc khối lượng không hợp lệ")
        if snapshot.unit_verification_status not in ("EMPIRICALLY_VERIFIED","OFFICIALLY_VERIFIED"):
            raise ValueError("Đơn vị DNSE của mã này mới áp theo schema, chưa được xác minh trực tiếp")
        if snapshot.market_session not in ("DNSE_CONTINUOUS_AM","DNSE_CONTINUOUS_PM"):
            raise ValueError("Snapshot DNSE nằm ngoài cửa sổ khớp lệnh liên tục đã phân loại")
    if isinstance(requested_quantity, bool) or not isinstance(requested_quantity, int) or requested_quantity <= 0:
        raise ValueError("Số lượng phải là số nguyên dương")
    if side not in ("sell", "buy") or benchmark not in ("best", "mid"):
        raise ValueError("Chiều lệnh hoặc benchmark không hợp lệ")
    fee, tax = rate(fee_rate, "fee"), rate(sell_tax_rate, "tax")
    if fee + tax > 1:
        raise ValueError("Tổng phí và thuế không được vượt 100%")
    snapshot = validate(snapshot)
    if snapshot.data_quality_status == "INVALID":
        raise ValueError("Snapshot không hợp lệ: " + ", ".join(snapshot.issues))
    levels = snapshot.bids if side == "sell" else snapshot.asks
    remaining, fills = requested_quantity, []
    for level in levels:
        if level.usable and level.volume and remaining:
            quantity = min(remaining, level.volume)
            fills.append(Fill(level.level, level.price, quantity))
            remaining -= quantity
    executed = requested_quantity - remaining
    gross = sum((f.price * f.quantity for f in fills), Decimal(0))
    best = levels[0].price if levels[0].usable else None
    benchmark_price = best
    if benchmark == "mid":
        b, a = snapshot.bids[0], snapshot.asks[0]
        benchmark_price = (b.price + a.price) / 2 if b.usable and a.usable else None
    benchmark_value = benchmark_price * executed if benchmark_price is not None and executed else None
    cost = ((benchmark_value - gross) if side == "sell" else (gross - benchmark_value)) if benchmark_value else None
    fees = gross * fee
    sell_tax = gross * tax if side == "sell" else Decimal(0)
    net = gross - fees - sell_tax if side == "sell" else gross + fees
    warnings = list(snapshot.issues)
    if remaining:
        warnings.append("Chi phí và proceeds chỉ tính trên phần được bao phủ; phần còn lại chưa định giá.")
    if snapshot.source_timestamp is None:
        warnings.append("Không có timestamp nguồn; thời điểm nhận không chứng minh độ mới của giá.")
    return ExecutionResult(snapshot.symbol, side, requested_quantity, executed, remaining,
        Decimal(executed) / requested_quantity, gross, gross / executed if executed else None,
        benchmark_value, cost, cost / benchmark_value * 10000 if benchmark_value else None,
        fees, sell_tax, net, tuple(fills), benchmark, tuple(warnings))
