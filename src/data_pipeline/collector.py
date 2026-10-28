import logging
import time

from src.data_providers.base import NotConfigured, ProviderError

logger = logging.getLogger(__name__)


def collect_once(provider, storage, symbols, retries=0, sleep=time.sleep):
    info = provider.get_provider_info()
    run_id = storage.start_run(info["provider"], symbols, min_interval=180 if info["provider"] == "vnstock" else 0)
    try:
        for attempt in range(retries + 1):
            try:
                snapshots = provider.get_snapshots(symbols)
                if not snapshots:
                    raise ProviderError("NO DATA")
                break
            except NotConfigured:
                raise
            except (ProviderError, TimeoutError) as error:
                # Retry timeout/transport only, never entitlement, schema or rate errors.
                if attempt == retries or not (isinstance(error, TimeoutError) or "TIMEOUT" in str(error)):
                    raise
                sleep((180 if info["provider"] == "vnstock" else 1) * 2 ** attempt)
        inserted = sum(storage.insert(snapshot, run_id) for snapshot in snapshots)
        missing = set(symbols) - {s.symbol for s in snapshots}
        status = "PARTIAL" if missing or any(s.data_quality_status != "VALID" for s in snapshots) else "SUCCESS"
        storage.finish_run(run_id, status, inserted, "MISSING_SYMBOLS" if missing else None)
        logger.info("Collection provider=%s status=%s inserted=%d", info["provider"], status, inserted)
        return {"run_id": run_id, "inserted": inserted, "status": status, "missing_symbols": sorted(missing)}
    except BaseException as error:
        storage.finish_run(run_id, "FAILED", error=type(error).__name__)
        logger.warning("Collection failed provider=%s category=%s", info["provider"], type(error).__name__)
        raise
