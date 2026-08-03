from datetime import datetime
from decimal import Decimal

from app.collectors.base import (
    AuthenticationRequiredError,
    CollectorHealth,
    FetchError,
    NormalizedItem,
    ParseError,
    ParsedItem,
    RawFetchResult,
    SourceCollector,
)
from app.domain.enums import FulfillmentType, RegionSource, SupportedEventType


def _make_parsed_item(url: str, price: Decimal | None = Decimal("1000")) -> ParsedItem:
    return ParsedItem(
        raw_title=f"item from {url}",
        price=price,
        image_urls=[],
        event_type=SupportedEventType.LOTTERY,
        start_at=None,
        deadline_at=None,
        announce_at=None,
        purchase_limit_at=None,
        apply_url=None,
        product_url=url,
        shop_name=None,
    )


class DummySourceCollector(SourceCollector):
    """テスト専用のダミーCollector。fail_url/fail_modeで意図的にエラーを発生させる。"""

    source_name = "dummy_source"
    supported_event_types = [SupportedEventType.LOTTERY]
    target_urls = ["https://example.com/a", "https://example.com/b"]

    def __init__(self, fail_url: str | None = None, fail_mode: str | None = None) -> None:
        super().__init__()
        self.fail_url = fail_url
        self.fail_mode = fail_mode  # "fetch" / "parse" / "auth"

    async def fetch(self, target_url: str) -> RawFetchResult:
        if target_url == self.fail_url and self.fail_mode == "fetch":
            raise FetchError(f"boom: {target_url}")
        if target_url == self.fail_url and self.fail_mode == "auth":
            raise AuthenticationRequiredError(f"login required: {target_url}")
        return RawFetchResult(url=target_url, status_code=200, html="<html></html>", fetched_at=datetime.now())

    def parse(self, raw: RawFetchResult) -> list[ParsedItem]:
        if raw.url == self.fail_url and self.fail_mode == "parse":
            raise ParseError(f"boom: {raw.url}")
        return [_make_parsed_item(raw.url)]

    def normalize(self, items: list[ParsedItem]) -> list[NormalizedItem]:
        return [
            NormalizedItem(
                product_name=item.raw_title,
                identifiers={},
                category_hint=None,
                brand_hint=None,
                parsed=item,
                confidence_hint="B",
            )
            for item in items
        ]

    def rate_limit(self) -> float:
        return 0.0  # テストを高速化


async def test_run_success_returns_ok_health_and_updates_last_success_at():
    collector = DummySourceCollector()

    result = await collector.run()

    assert result.source_name == "dummy_source"
    assert result.success_count == 2
    assert result.error_count == 0
    assert result.errors == []
    assert result.health == CollectorHealth.OK
    assert collector.last_success_at == result.finished_at


async def test_run_fetch_error_is_captured_without_crashing_other_urls():
    collector = DummySourceCollector(fail_url="https://example.com/a", fail_mode="fetch")

    result = await collector.run()

    assert result.success_count == 1
    assert result.error_count == 1
    assert "fetch failed for https://example.com/a" in result.errors[0]
    assert result.health == CollectorHealth.DEGRADED


async def test_run_authentication_required_error_is_captured_like_fetch_error():
    collector = DummySourceCollector(fail_url="https://example.com/a", fail_mode="auth")

    result = await collector.run()

    assert result.success_count == 1
    assert result.error_count == 1
    assert "fetch failed for https://example.com/a" in result.errors[0]


async def test_run_parse_error_is_captured_without_crashing_other_urls():
    collector = DummySourceCollector(fail_url="https://example.com/b", fail_mode="parse")

    result = await collector.run()

    assert result.success_count == 1
    assert result.error_count == 1
    assert "parse failed for https://example.com/b" in result.errors[0]


async def test_run_all_failures_marks_health_failing_and_does_not_advance_last_success():
    collector = DummySourceCollector(fail_url="https://example.com/a", fail_mode="fetch")
    collector.target_urls = ["https://example.com/a"]

    result = await collector.run()

    assert result.success_count == 0
    assert result.health == CollectorHealth.FAILING
    assert collector.last_success_at is None


async def test_run_validation_error_excludes_item_from_success_count():
    collector = DummySourceCollector()

    async def fetch_missing_price(target_url: str) -> RawFetchResult:
        return RawFetchResult(url=target_url, status_code=200, html="<html></html>", fetched_at=datetime.now())

    def parse_missing_price(raw: RawFetchResult) -> list[ParsedItem]:
        return [_make_parsed_item(raw.url, price=None)]

    collector.fetch = fetch_missing_price  # type: ignore[method-assign]
    collector.parse = parse_missing_price  # type: ignore[method-assign]

    result = await collector.run()

    assert result.success_count == 0
    assert result.error_count == 2
    assert all("price is missing" in err for err in result.errors)


def test_normalized_item_default_fallback_values_match_spec():
    """実装仕様書5章: 判定できない場合の安全側フォールバック。"""
    item = NormalizedItem(
        product_name="foo",
        identifiers={},
        category_hint=None,
        brand_hint=None,
        parsed=_make_parsed_item("https://example.com"),
        confidence_hint="B",
    )

    assert item.fulfillment_type == FulfillmentType.ONLINE_SHIPPING
    assert item.region_source == RegionSource.UNKNOWN
    assert item.region_override is None
    assert item.region_display_text is None


def test_validate_flags_missing_product_name_and_price():
    collector = DummySourceCollector()
    item = NormalizedItem(
        product_name="",
        identifiers={},
        category_hint=None,
        brand_hint=None,
        parsed=_make_parsed_item("https://example.com", price=None),
        confidence_hint="D",
    )

    errors = collector.validate(item)

    assert "product_name is empty" in errors
    assert "price is missing" in errors
