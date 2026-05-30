from __future__ import annotations

from src.products.models import IntentKind, ProductConfig
from src.risk.models import ProductCode


def test_gold_config_defaults() -> None:
    c = ProductConfig.gold_ai()
    assert c.product == ProductCode.GOLD_AI
    assert c.symbols == ("XAUUSD",)
    assert c.lot == 0.03
    assert c.max_positions == 1
    assert c.trading_hours.enable_friday_close is True


def test_multi_cfd_config_defaults() -> None:
    c = ProductConfig.multi_cfd_ai()
    assert c.product == ProductCode.MULTI_CFD_AI
    assert "EURUSD" in c.symbols
    assert "XAUUSD" not in c.symbols
    assert c.lot == 0.02
    assert c.max_positions == 3
    assert c.trading_hours.enable_friday_close is False


def test_intent_kinds() -> None:
    assert IntentKind.OPEN.value == "open"
    assert IntentKind.CLOSE.value == "close"
    assert IntentKind.MODIFY_SL.value == "modify_sl"
