from types import MappingProxyType

import pytest

from cfa.agent.routing import DEFAULT_ROUTING, Intent, ModelAlias, choose_alias


@pytest.mark.parametrize(
    ("intent", "alias"),
    [
        (Intent.ORDERS, ModelAlias.FAST),
        (Intent.BILLING, ModelAlias.FAST),
        (Intent.SERVICE, ModelAlias.FAST),
        (Intent.TROUBLESHOOTING, ModelAlias.REASONING),
        (Intent.OTHER, ModelAlias.FAST),
    ],
)
def test_default_routing(intent: Intent, alias: ModelAlias) -> None:
    assert choose_alias(intent) is alias


def test_every_intent_is_routed() -> None:
    assert set(DEFAULT_ROUTING) == set(Intent)


def test_unknown_intent_falls_back_to_fast() -> None:
    assert choose_alias(Intent.BILLING, MappingProxyType({})) is ModelAlias.FAST
