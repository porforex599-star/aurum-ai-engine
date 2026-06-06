from src.products.gold_ai import GoldAIProduct
from src.products.models import (
    CloseIntent,
    IntentKind,
    ModifySLIntent,
    OpenPosition,
    ProductConfig,
    TradeIntent,
    TradingHours,
)
from src.products.multi_cfd_ai import MultiCfdAIProduct
from src.products.position_manager import PositionManager

__all__ = [
    "CloseIntent",
    "GoldAIProduct",
    "IntentKind",
    "ModifySLIntent",
    "MultiCfdAIProduct",
    "OpenPosition",
    "PositionManager",
    "ProductConfig",
    "TradeIntent",
    "TradingHours",
]
