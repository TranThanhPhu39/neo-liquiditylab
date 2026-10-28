"""Versioned unit declarations linked to reviewed external evidence."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse


class UnitsNotVerified(ValueError):
    pass


@dataclass(frozen=True)
class UnitContract:
    provider: str
    upstream_source: str
    provider_version: str
    price_multiplier: Decimal
    volume_multiplier: Decimal
    contract_hash: str
    payload: dict


def load_unit_contract(path):
    path = Path(path)
    record = json.loads(path.read_text(encoding="utf-8-sig"))
    if record.get("verification_status") != "VERIFIED":
        raise UnitsNotVerified("Hồ sơ đơn vị chưa được xác minh; không chuyển raw sang dữ liệu tài chính.")
    if (record.get("provider"),record.get("upstream_source"),record.get("provider_version")) != ("vnstock","kbs","5.2.1"):
        raise UnitsNotVerified("Hồ sơ không khớp provider/source/version đang hỗ trợ")
    try:
        price,volume = (Decimal(str(record.get(key))) for key in ("price_multiplier_to_vnd","volume_multiplier_to_shares"))
        if not all(x.is_finite() and x>0 for x in (price,volume)):
            raise ValueError()
        verified_at = datetime.fromisoformat(record["verified_at"])
        if verified_at.tzinfo is None or verified_at>datetime.now(timezone.utc):
            raise ValueError()
    except (InvalidOperation,ValueError,KeyError,TypeError):
        raise UnitsNotVerified("Hệ số phải dương hữu hạn và verified_at cần timezone hợp lệ") from None
    if record.get("evidence_type") not in ("official_versioned_contract","contemporaneous_board_comparison"):
        raise UnitsNotVerified("Cần bằng chứng hợp đồng nguồn hoặc đối chiếu bảng giá cùng thời điểm")
    if not isinstance(record.get("review_note"),str) or not record["review_note"].strip():
        raise UnitsNotVerified("Thiếu ghi chú thẩm định cách xác định hệ số")
    evidence = record.get("evidence",{})
    if not isinstance(evidence,dict) or urlparse(str(evidence.get("source_url",""))).scheme != "https":
        raise UnitsNotVerified("Cần URL HTTPS nguồn bằng chứng")
    reference = evidence.get("local_path")
    if not reference:
        raise UnitsNotVerified("Thiếu bản bằng chứng cục bộ")
    artifact = (path.parent/str(reference)).resolve()
    if not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest()!=evidence.get("sha256"):
        raise UnitsNotVerified("Bằng chứng thiếu hoặc checksum không khớp")
    # This checks completeness/integrity, not the truth of an external document.
    payload = {key:record[key] for key in ("provider","upstream_source","provider_version","verification_status",
        "verified_at","evidence_type","review_note")}
    payload.update(price_multiplier_to_vnd=str(price),volume_multiplier_to_shares=str(volume),
                   evidence={key:evidence.get(key) for key in ("source_url","local_path","sha256")})
    canonical = json.dumps(payload,sort_keys=True,ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return UnitContract("vnstock","kbs","5.2.1",price,volume,digest,payload)
