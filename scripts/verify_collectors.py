#!/usr/bin/env python3
"""Collector実データ検証用スクリプト(read-only)。

Phase 0で実装した一番くじ/駿河屋/ポケモンセンターオンラインの各Collectorは、
すべてFixture(Markdown変換済みテキスト)をもとにしたテキストパターンベースの
parse()実装であり、実サイトの生HTMLに対しては未検証(各Collectorのモジュール
docstring参照)。本スクリプトは、本番のConoHa VPS環境で実際にfetch()を呼び、
parse()の結果を人間が確認できるJSON形式で標準出力に出すための検証専用ツールで
ある。

【read-onlyであることの保証】
このスクリプトはapp.collectors配下のCollectorクラスのみをimportし、
app.pipeline / app.db 配下は一切importしない。そのためDBへの書き込みは
構造的に発生しない(ingest_normalized_item()等のパイプライン関数を呼ばない)。

使い方:
    # 一番くじ(1kuji.comトップページ)のみ検証
    python scripts/verify_collectors.py --ichiban-kuji

    # 一番くじ+bandaispirits.co.jpの商品詳細ページ(prd_idは1kuji.comの結果を見て
    # 手動で指定する。両ドメインの対応関係は未確認のため自動連携はしない)
    python scripts/verify_collectors.py --ichiban-kuji --bandaispirits-prd-id 12345

    # 駿河屋(検索クエリ・カテゴリは任意に指定可能)
    python scripts/verify_collectors.py --suruga-ya --suruga-ya-query "一番くじ"

    # ポケモンセンターオンライン(商品コードは事前に商品ページURLから確認しておく)
    python scripts/verify_collectors.py --pokemon-center-code 9900000006082

    # on-line.1kuji.comへのfetch()が実際にFetchErrorになることの確認
    # (ネットワークアクセスは発生しない。ガードがfetch()冒頭にあるため)
    python scripts/verify_collectors.py --assert-online-1kuji-blocked

    # まとめて実行(引数が必要なものは指定した場合のみ実行される)
    python scripts/verify_collectors.py --all --suruga-ya-query "一番くじ" \\
        --pokemon-center-code 9900000006082

結果はJSON配列として標準出力に出力する(`> result.json`でファイル保存推奨)。
どのCollectorのどの処理(fetch/parse/normalize)で失敗したかはstderrにも
ログを出し、1つのCollectorの失敗が他のCollectorの検証を止めないようにする。
"""

import argparse
import asyncio
import dataclasses
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from app.collectors.base import AuthenticationRequiredError, FetchError, ParseError
from app.collectors.markets.suruga_ya import CATEGORY_HOBBY_ALL, SurugaYaCollector
from app.collectors.sources.ichiban_kuji import IchibanKujiCollector
from app.collectors.sources.pokemon_center_online import PokemonCenterOnlineCollector


def _json_default(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    raise TypeError(f"{type(obj).__name__} is not JSON serializable")


def _log(message: str) -> None:
    print(f"[verify_collectors] {message}", file=sys.stderr)


async def verify_ichiban_kuji() -> dict:
    """1kuji.comトップページに対してfetch()→parse()を実行する。"""
    collector = IchibanKujiCollector()
    target_url = collector.target_urls[0]
    result: dict = {"collector": "ichiban_kuji", "step": "top_page", "target_url": target_url}

    _log(f"ichiban_kuji: fetch() {target_url}")
    try:
        raw = await collector.fetch(target_url)
    except (FetchError, AuthenticationRequiredError) as exc:
        _log(f"ichiban_kuji: fetch()で失敗: {exc}")
        result.update(ok=False, failed_step="fetch", error=f"{type(exc).__name__}: {exc}")
        return result

    result["status_code"] = raw.status_code

    _log("ichiban_kuji: parse()")
    try:
        items = collector.parse(raw)
    except ParseError as exc:
        _log(f"ichiban_kuji: parse()で失敗: {exc}")
        result.update(ok=False, failed_step="parse", error=f"{type(exc).__name__}: {exc}")
        return result

    result.update(ok=True, item_count=len(items), items=items)
    return result


async def verify_ichiban_kuji_bandaispirits(prd_id: str) -> dict:
    """bandaispirits.co.jpの商品詳細ページ(prd_id指定)を検証する。"""
    collector = IchibanKujiCollector()
    result: dict = {"collector": "ichiban_kuji", "step": "bandaispirits_detail", "prd_id": prd_id}

    _log(f"ichiban_kuji: fetch_bandaispirits_detail(prd_id={prd_id})")
    try:
        item = await collector.fetch_bandaispirits_detail(prd_id)
    except (FetchError, AuthenticationRequiredError) as exc:
        _log(f"ichiban_kuji(bandaispirits): fetch()で失敗: {exc}")
        result.update(ok=False, failed_step="fetch", error=f"{type(exc).__name__}: {exc}")
        return result
    except ParseError as exc:
        _log(f"ichiban_kuji(bandaispirits): parse()で失敗: {exc}")
        result.update(ok=False, failed_step="parse", error=f"{type(exc).__name__}: {exc}")
        return result

    result.update(ok=True, item=item)
    return result


async def verify_suruga_ya(query: str, category: str) -> dict:
    """駿河屋買取検索(search_buy)に対してsearch()→parse_observations()を実行する。"""
    collector = SurugaYaCollector(category=category)
    result: dict = {"collector": "suruga_ya", "query": query, "category": category}

    _log(f"suruga_ya: search(query={query!r}, category={category!r})")
    try:
        raw = await collector.search(query, identifiers={})
    except FetchError as exc:
        _log(f"suruga_ya: search()(fetch)で失敗: {exc}")
        result.update(ok=False, failed_step="search", error=f"{type(exc).__name__}: {exc}")
        return result

    result["status_code"] = raw.status_code
    result["fetched_url"] = raw.url

    _log("suruga_ya: parse_observations()")
    try:
        observations = collector.parse_observations(raw)
    except ParseError as exc:
        _log(f"suruga_ya: parse_observations()で失敗: {exc}")
        result.update(ok=False, failed_step="parse_observations", error=f"{type(exc).__name__}: {exc}")
        return result

    result.update(ok=True, observation_count=len(observations), observations=observations)
    return result


async def verify_pokemon_center(product_code: str) -> dict:
    """ポケモンセンターオンラインの個別商品ページに対してfetch()→parse()を実行する。"""
    collector = PokemonCenterOnlineCollector()
    url = f"https://www.pokemoncenter-online.com/{product_code}.html"
    result: dict = {"collector": "pokemon_center_online", "product_code": product_code, "target_url": url}

    _log(f"pokemon_center_online: fetch() {url}")
    try:
        raw = await collector.fetch(url)
    except (FetchError, AuthenticationRequiredError) as exc:
        _log(f"pokemon_center_online: fetch()で失敗: {exc}")
        result.update(ok=False, failed_step="fetch", error=f"{type(exc).__name__}: {exc}")
        return result

    result["status_code"] = raw.status_code

    _log("pokemon_center_online: parse()")
    try:
        items = collector.parse(raw)
    except ParseError as exc:
        _log(f"pokemon_center_online: parse()で失敗: {exc}")
        result.update(ok=False, failed_step="parse", error=f"{type(exc).__name__}: {exc}")
        return result

    result.update(ok=True, item_count=len(items), items=items)

    _log("pokemon_center_online: normalize()")
    try:
        normalized = collector.normalize(items)
    except Exception as exc:  # noqa: BLE001 - 検証用途のため想定外例外も内容を出して継続する
        _log(f"pokemon_center_online: normalize()で失敗: {exc}")
        result.update(normalize_ok=False, normalize_error=f"{type(exc).__name__}: {exc}")
        return result

    result.update(normalize_ok=True, normalized=normalized)
    return result


async def verify_online_1kuji_blocked() -> dict:
    """CLAUDE.mdタスク4着手前チェックリスト: on-line.1kuji.comへのfetch()が
    実際にFetchErrorになることを確認する。fetch()冒頭のガードで即座に例外を送出する
    実装のため、このチェック自体はネットワークアクセスを発生させない。"""
    collector = IchibanKujiCollector()
    result: dict = {"collector": "ichiban_kuji", "step": "assert_online_1kuji_blocked"}

    _log("ichiban_kuji: fetch('https://on-line.1kuji.com/...') がFetchErrorになることを確認")
    try:
        await collector.fetch("https://on-line.1kuji.com/some/apply/path")
    except FetchError as exc:
        result.update(ok=True, note=f"想定通りFetchErrorが送出された: {exc}")
        return result

    result.update(ok=False, note="FetchErrorが送出されなかった(要調査。実際にリクエストが発生した可能性あり)")
    return result


def _print_summary(results: list[dict]) -> None:
    _log("=== 検証結果サマリ ===")
    for r in results:
        ok = r.get("ok")
        label = f"{r['collector']}/{r.get('step', r.get('product_code', r.get('query', '')))}"
        if ok is True:
            _log(f"OK   {label}")
        elif ok is False:
            _log(f"NG   {label} (failed_step={r.get('failed_step')}, error={r.get('error')})")
        else:
            _log(f"SKIP {label}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="指定済みの引数がある検証をすべて実行する")
    parser.add_argument("--ichiban-kuji", action="store_true", help="1kuji.comトップページを検証する")
    parser.add_argument("--bandaispirits-prd-id", metavar="PRD_ID", help="bandaispirits.co.jpの商品詳細ページを検証する")
    parser.add_argument("--suruga-ya", action="store_true", help="駿河屋買取検索を検証する")
    parser.add_argument("--suruga-ya-query", default="一番くじ", metavar="QUERY")
    parser.add_argument("--suruga-ya-category", default=CATEGORY_HOBBY_ALL, metavar="CATEGORY_ID")
    parser.add_argument("--pokemon-center-code", metavar="PRODUCT_CODE", help="ポケモンセンターオンラインの商品コード(13桁)")
    parser.add_argument(
        "--assert-online-1kuji-blocked",
        action="store_true",
        help="on-line.1kuji.comへのfetch()がFetchErrorになることを確認する",
    )
    args = parser.parse_args()

    if not any(
        [
            args.all,
            args.ichiban_kuji,
            args.bandaispirits_prd_id,
            args.suruga_ya,
            args.pokemon_center_code,
            args.assert_online_1kuji_blocked,
        ]
    ):
        parser.print_help(file=sys.stderr)
        sys.exit(1)

    results: list[dict] = []

    if args.ichiban_kuji or args.all:
        results.append(await verify_ichiban_kuji())

    if args.bandaispirits_prd_id:
        results.append(await verify_ichiban_kuji_bandaispirits(args.bandaispirits_prd_id))

    if args.suruga_ya or args.all:
        results.append(await verify_suruga_ya(args.suruga_ya_query, args.suruga_ya_category))

    if args.pokemon_center_code:
        results.append(await verify_pokemon_center(args.pokemon_center_code))
    elif args.all:
        _log("pokemon_center_online: --pokemon-center-codeが未指定のためスキップ")

    if args.assert_online_1kuji_blocked or args.all:
        results.append(await verify_online_1kuji_blocked())

    _print_summary(results)
    print(json.dumps(results, ensure_ascii=False, indent=2, default=_json_default))

    if any(r.get("ok") is False for r in results):
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
