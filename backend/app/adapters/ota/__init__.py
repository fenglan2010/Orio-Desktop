from app.adapters.ota.base import OTAAdapter
from app.adapters.ota.ctrip_mock import CtripMockAdapter
from app.adapters.ota.official_write import OfficialWriteAdapter, build_write_receipt, get_write_adapter

__all__ = ["OTAAdapter", "CtripMockAdapter", "OfficialWriteAdapter", "build_write_receipt", "get_write_adapter"]
