import csv
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

PRODUCTS_FILE = Path("products.csv")
STATE_FILE = Path("prices_state.json")
HISTORY_FILE = Path("price_history.csv")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
USE_BROWSER = os.getenv("USE_BROWSER", "true").lower() in {"1", "true", "yes"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
}


@dataclass
class Product:
    name: str
    url: str
    target_price: Optional[float] = None


@dataclass
class PriceResult:
    name: str
    url: str
    price: float
    currency: str = "EUR"


def parse_price(text: str) -> Optional[float]:
    if not text:
        return None
    text = text.replace("\xa0", " ")
    match = re.search(r"(\d{1,4}(?:[\.\s]\d{3})*(?:,\d{1,2})|\d{1,4}(?:\.\d{1,2})?)\s*€?", text)
    if not match:
        return None
    value = match.group(1).replace(" ", "")
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


def load_products() -> list[Product]:
    if not PRODUCTS_FILE.exists():
        raise FileNotFoundError("products.csv not found")
    products: list[Product] = []
    with PRODUCTS_FILE.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = (row.get("name") or "").strip()
            url = (row.get("url") or "").strip()
            target_raw = (row.get("target_price") or "").strip()
            if not name or not url or url.startswith("#"):
                continue
            products.append(Product(name=name, url=url, target_price=parse_price(target_raw)))
    if not products:
        raise ValueError("products.csv 里没有有效商品。请至少填写 name,url")
    return products


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def append_history(result: PriceResult) -> None:
    exists = HISTORY_FILE.exists()
    with HISTORY_FILE.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(["timestamp_utc", "name", "url", "price", "currency"])
        writer.writerow([
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            result.name,
            result.url,
            f"{result.price:.2f}",
            result.currency,
        ])


def price_from_jsonld(soup: BeautifulSoup) -> Optional[float]:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except Exception:
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                offers = item.get("offers")
                if isinstance(offers, dict):
                    price = offers.get("price") or offers.get("lowPrice")
                    print(f"{name} 当前价格: {price}")
                    parsed = parse_price(str(price))
                    if parsed is not None:
                        return parsed
                stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
    return None


def price_from_meta(soup: BeautifulSoup) -> Optional[float]:
    selectors = [
        {"property": "product:price:amount"},
        {"property": "og:price:amount"},
        {"itemprop": "price"},
        {"name": "price"},
    ]
    for attrs in selectors:
        tag = soup.find(attrs=attrs)
        if tag:
            parsed = parse_price(tag.get("content") or tag.get_text(" "))
            if parsed is not None:
                return parsed
    return None


def price_from_visible_text(soup: BeautifulSoup) -> Optional[float]:
    candidate_selectors = [
        "[class*=price]",
        "[class*=Price]",
        ".product-price",
        ".price",
        "span",
        "div",
    ]
    prices: list[float] = []
    for selector in candidate_selectors:
        for tag in soup.select(selector)[:300]:
            text = tag.get_text(" ", strip=True)
            if "€" in text:
                parsed = parse_price(text)
                if parsed is not None and 0.5 <= parsed <= 1000:
                    prices.append(parsed)
        if prices:
            # 电商页常同时有原价和优惠价，通常较低的是现售价。
            return min(prices)
    return None


def extract_price(html: str) -> Optional[float]:
    soup = BeautifulSoup(html, "html.parser")
    return price_from_jsonld(soup) or price_from_meta(soup) or price_from_visible_text(soup)


def fetch_html_requests(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def fetch_html_browser(url: str) -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale="es-ES", user_agent=HEADERS["User-Agent"])
        try:
            page.goto(url, wait_until="networkidle", timeout=60000)
        except PlaywrightTimeoutError:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        html = page.content()
        browser.close()
        return html


def get_price(product: Product) -> PriceResult:
    html = ""
    # 先用轻量 requests，失败或抓不到再用浏览器。
    try:
        html = fetch_html_requests(product.url)
        price = extract_price(html)
        if price is not None:
            return PriceResult(product.name, product.url, price)
    except Exception as exc:
        print(f"requests failed for {product.name}: {exc}")

    if USE_BROWSER:
        html = fetch_html_browser(product.url)
        price = extract_price(html)
        if price is not None:
            return PriceResult(product.name, product.url, price)

    raise RuntimeError(f"无法识别价格：{product.name} | {product.url}")


def send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets not set; skip notification.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": False}, timeout=30)
    r.raise_for_status()


def main() -> None:
    products = load_products()
    state = load_state()
    notifications: list[str] = []
    failures: list[str] = []

    for product in products:
        try:
            result = get_price(product)
            append_history(result)
            old_price = state.get(product.url, {}).get("price")
            state[product.url] = {
                "name": product.name,
                "price": result.price,
                "currency": result.currency,
                "checked_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            print(f"{product.name}: {result.price:.2f} €")

            if old_price is None:
                notifications.append(f"首次记录：{product.name}\n当前价：{result.price:.2f} €\n{product.url}")
            elif result.price < float(old_price):
                notifications.append(
                    f"降价啦：{product.name}\n旧价：{float(old_price):.2f} € → 新价：{result.price:.2f} €\n{product.url}"
                )
            elif product.target_price is not None and result.price <= product.target_price:
                notifications.append(
                    f"达到目标价：{product.name}\n当前价：{result.price:.2f} € ≤ 目标价：{product.target_price:.2f} €\n{product.url}"
                )
            time.sleep(2)
        except Exception as exc:
            print(f"FAILED {product.name}: {exc}")
            failures.append(f"{product.name}: {exc}")

    save_state(state)

    if notifications:
        send_telegram("\n\n".join(notifications))
    if failures:
        send_telegram("Druni 价格监控部分失败：\n" + "\n".join(failures[:5]))


if __name__ == "__main__":
    main()
