from dataclasses import dataclass
from decimal import Decimal
import csv
import io


@dataclass(frozen=True)
class Position:
    symbol: str
    quantity: int

    def __post_init__(self):
        object.__setattr__(self, "symbol", str(self.symbol).strip().upper())
        if not self.symbol or not self.symbol.isalnum():
            raise ValueError("Mã cổ phiếu không hợp lệ")
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int) or self.quantity <= 0:
            raise ValueError("Số lượng danh mục phải là số nguyên dương")


def parse_portfolio(text: str) -> list[Position]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    if not reader.fieldnames or not {"symbol", "quantity"} <= set(reader.fieldnames):
        raise ValueError("CSV cần hai cột symbol,quantity")
    positions = []
    for row in reader:
        value = Decimal(row["quantity"])
        if not value.is_finite() or value != value.to_integral_value():
            raise ValueError("Số lượng phải nguyên và hữu hạn")
        positions.append(Position(row["symbol"], int(value)))
    if not positions or len({p.symbol for p in positions}) != len(positions):
        raise ValueError("Danh mục rỗng hoặc trùng mã")
    return positions
