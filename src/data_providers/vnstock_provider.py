from datetime import datetime, timezone
import json
import os
import subprocess
import sys
import time

from config.settings import ROOT
from src.data_pipeline.normalization import normalize
from src.data_providers.base import MarketDataProvider, NotConfigured, ProviderError


class VnstockProvider(MarketDataProvider):
    def __init__(self, *, access_confirmed=None, volume_unit=None, price_unit=None,
                 timeout=None, transport=None, min_interval=180):
        self.access_confirmed = access_confirmed if access_confirmed is not None else os.getenv("VNSTOCK_ACCESS_CONFIRMED", "false").lower() == "true"
        self.volume_unit = volume_unit or os.getenv("VNSTOCK_VOLUME_UNIT", "")
        self.price_unit = price_unit or os.getenv("VNSTOCK_PRICE_UNIT", "VND")
        self.timeout = timeout or float(os.getenv("VNSTOCK_TIMEOUT_SECONDS", "30"))
        self.transport = transport or self._request
        self.min_interval = max(180, min_interval)
        self._last_attempt = None

    def _request(self, symbols):
        try:
            process = subprocess.run([sys.executable, "-m", "src.data_providers.vnstock_worker"],
                input=json.dumps({"symbols": symbols}), capture_output=True, text=True,
                timeout=self.timeout, cwd=ROOT)
        except subprocess.TimeoutExpired:
            raise ProviderError("TIMEOUT: Vnstock; tiến trình request đã được dừng") from None
        try:
            response = json.loads(process.stdout)
        except (ValueError, TypeError):
            raise ProviderError("Vnstock worker không trả payload hợp lệ; không ghi log SDK thô") from None
        if process.returncode or response.get("error"):
            category = response.get("error", "WORKER_FAILED")
            if category == "AUTHENTICATION_REQUIRED":
                raise NotConfigured("Cần đăng nhập Vnstock trước khi chạy collector tự động; cấp Khách chỉ dùng thăm dò lần đầu.")
            raise ProviderError(f"Vnstock request thất bại ({category}); kiểm tra cài đặt/quyền/hạn mức")
        return response

    def get_snapshots(self, symbols):
        if not self.access_confirmed:
            raise NotConfigured("Cần xác minh quyền, điều khoản và hạn mức Vnstock rồi đặt VNSTOCK_ACCESS_CONFIRMED=true")
        if self.volume_unit != "shares" or self.price_unit != "VND":
            raise NotConfigured("Chưa xác minh đơn vị nguồn. Cần VNSTOCK_VOLUME_UNIT=shares và VNSTOCK_PRICE_UNIT=VND")
        if self._last_attempt is not None:
            delay = self.min_interval - (time.monotonic() - self._last_attempt)
            if delay > 0:
                raise ProviderError(f"LOCAL_RATE_LIMIT: thử lại sau {delay:.0f} giây")
        self._last_attempt = time.monotonic()
        response = self.transport(list(symbols))
        if response.get("version") != "5.2.1":
            raise ProviderError("Phiên bản SDK chưa được kiểm chứng; yêu cầu vnstock==5.2.1")
        rows = response.get("rows", [])
        if not rows:
            raise ProviderError("NO DATA: Vnstock trả bảng rỗng")
        if len({r.get("symbol") for r in rows}) != len(rows):
            raise ProviderError("Vnstock trả trùng mã trong cùng response")
        received = datetime.now(timezone.utc)
        snapshots = []
        for row in rows:
            if row.get("symbol") not in symbols:
                continue
            # quote() has no verified market timestamp: NEVER stamp request time as quote time.
            row = dict(row, last_price=row.get("close_price"), market_session="UNKNOWN")
            snapshots.append(normalize(row, provider="vnstock", upstream_source="kbs",
                provider_version=response["version"], collected_at=received,
                price_unit=self.price_unit, volume_unit=self.volume_unit))
        if not snapshots:
            raise ProviderError("NO DATA: response không chứa mã được yêu cầu")
        return snapshots

    def get_provider_info(self):
        return {"provider": "vnstock", "label": "VNSTOCK DATA — REST SNAPSHOTS", "version": "5.2.1 (required)"}

    def health_check(self):
        from importlib.metadata import version, PackageNotFoundError
        try:
            installed = version("vnstock")
        except PackageNotFoundError:
            installed = None
        return {**self.get_provider_info(), "ready": self.access_confirmed and self.volume_unit == "shares"
                and self.price_unit == "VND" and installed == "5.2.1", "installed_version":installed,
                "authentication_verified":False, "network_verified": False}
