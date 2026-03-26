import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import List, Dict, Set, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

BASE = "https://www.olx.pl"

HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.olx.pl/",
}

MONTHS_PL = {
    "stycznia": "01",
    "lutego": "02",
    "marca": "03",
    "kwietnia": "04",
    "maja": "05",
    "czerwca": "06",
    "lipca": "07",
    "sierpnia": "08",
    "września": "09",
    "wreśnia": "09",
    "pazdziernika": "10",
    "października": "10",
    "listopada": "11",
    "grudnia": "12",
}


def http_get(url: str, retries: int = 3, timeout: int = 15) -> Optional[str]:
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200 and "<html" in r.text.lower():
                return r.text
            time.sleep(1.0 + 0.3 * i)
        except requests.RequestException as e:
            logger.warning(f"Request attempt {i + 1} failed: {e}")
            time.sleep(1.0 + 0.3 * i)
    return None


def is_featured(card: BeautifulSoup) -> bool:
    lab = card.select_one(".css-144z9p2, .css-10iz5lf, [class*='highlight']")
    if lab and "wyróżnione" in lab.get_text(strip=True).lower():
        return True
    if "wyróżnione" in card.get_text(" ", strip=True).lower():
        return True
    return False


def plus2h_display(loc_date_text: str) -> str:
    t = (loc_date_text or "").strip()
    rest = t.split(" - ", 1)[1].strip() if " - " in t else t
    rest = rest.replace(",", " ")
    rest_low = rest.lower()
    now = datetime.now()

    m = re.search(r"dzisiaj\s*o\s*(\d{1,2}):(\d{2})", rest_low)
    if m:
        h, minute = int(m.group(1)), int(m.group(2))
        dt = now.replace(hour=h, minute=minute, second=0, microsecond=0) + timedelta(
            hours=2
        )
        return dt.strftime("%H:%M %d.%m.%Y")

    m = re.search(r"wczoraj\s*o\s*(\d{1,2}):(\d{2})", rest_low)
    if m:
        h, minute = int(m.group(1)), int(m.group(2))
        dt = (now - timedelta(days=1)).replace(
            hour=h, minute=minute, second=0, microsecond=0
        ) + timedelta(hours=2)
        return dt.strftime("%H:%M %d.%m.%Y")

    m = re.search(r"(\d{1,2}):(\d{2})\s+(\d{1,2})\.(\d{1,2})\.(\d{4})", rest)
    if m:
        h, mi, d, mo, y = map(int, m.groups())
        dt = datetime(y, mo, d, h, mi) + timedelta(hours=2)
        return dt.strftime("%H:%M %d.%m.%Y")

    m = re.search(r"(\d{1,2})\s+([A-Za-ząćęłńóśźż]+)\s+(\d{4})", rest_low)
    if m:
        d = int(m.group(1))
        mon_name = m.group(2)
        y = int(m.group(3))
        if mon_name in MONTHS_PL:
            mo = int(MONTHS_PL[mon_name])
            dt = datetime(y, mo, d) + timedelta(hours=2)
            return dt.strftime("%H:%M %d.%m.%Y")

    m = re.search(r"(\d{1,2}):(\d{2})", rest)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        dt = now.replace(hour=h, minute=mi, second=0, microsecond=0) + timedelta(
            hours=2
        )
        return dt.strftime("%H:%M %d.%m.%Y")

    return rest


def parse_cards(html: str, limit: int) -> List[Dict]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select('div[data-cy="l-card"][data-testid="l-card"]')
    results: List[Dict] = []

    for card in cards:
        if is_featured(card):
            continue

        a = card.select_one("a[href]")
        if not a:
            continue
        raw_url = (a.get("href") or "").strip()
        url = urljoin(BASE, raw_url)

        ad_id = (card.get("id") or url).strip()

        title_tag = card.select_one('[data-cy="ad-card-title"] h4') or card.select_one(
            "h4"
        )
        title = title_tag.get_text(strip=True) if title_tag else "Ogłoszenie"

        price_tag = card.select_one('[data-testid="ad-price"]')
        price = price_tag.get_text(" ", strip=True) if price_tag else "—"

        loc_date = card.select_one('[data-testid="location-date"]')
        loc_date_text = loc_date.get_text(" ", strip=True) if loc_date else ""
        location = loc_date_text.split(" - ")[0] if " - " in loc_date_text else "—"

        img = card.select_one("img")
        thumb_raw = (img.get("src") or img.get("data-src")) if img else None
        thumb = urljoin(BASE, thumb_raw) if thumb_raw else None

        display_time = plus2h_display(loc_date_text)

        results.append(
            {
                "id": ad_id,
                "url": url,
                "title": title,
                "price": price,
                "location": location or "—",
                "display_time": display_time,
                "thumb": thumb,
            }
        )

        if len(results) >= limit:
            break

    return results


def clamp(s: Optional[str], n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"


def send_discord(webhook: str, item: Dict) -> bool:
    title = clamp(item.get("title"), 256)
    price = clamp(item.get("price"), 256)
    location = clamp(item.get("location"), 1024)
    tdisp = clamp(item.get("display_time"), 1024)

    content = clamp(f"[{price}] {title}", 1900)

    embed = {
        "title": title,
        "url": item["url"],
        "fields": [
            {"name": "Price", "value": price or "—", "inline": True},
            {"name": "Location", "value": location or "—", "inline": True},
            {"name": "Time", "value": tdisp or "—", "inline": False},
        ],
    }

    thumb = item.get("thumb")
    if isinstance(thumb, str) and thumb.startswith(("http://", "https://")):
        embed["thumbnail"] = {"url": thumb}

    payload = {"content": content, "embeds": [embed]}

    try:
        r = requests.post(webhook, headers={"User-Agent": UA}, json=payload, timeout=15)
        if r.status_code not in (200, 204):
            logger.error(f"Discord webhook HTTP {r.status_code}: {r.text[:300]}")
            return False
        return True
    except Exception as e:
        logger.error(f"Discord post failed: {e}")
        return False


def scrape_and_notify(
    url: str, webhook: str, num_items: int, seen_ids: Set[str]
) -> Tuple[List[Dict], List[Dict], Optional[str]]:
    """
    Returns: (all_items, new_items, error_message)
    """
    html = http_get(url)
    if not html:
        return [], [], "Failed to fetch HTML (blocked/empty)"

    cards = parse_cards(html, num_items)
    if not cards:
        return [], [], None  # No items found but not an error

    new_items = [c for c in cards if c["id"] not in seen_ids]

    return cards, new_items, None


def post_to_discord(items: List[Dict], webhook: str) -> Tuple[int, List[str]]:
    """
    Returns: (posted_count, errors)
    """
    posted = 0
    errors = []

    for item in items:
        if send_discord(webhook, item):
            posted += 1
            time.sleep(0.5)  # Rate limiting
        else:
            errors.append(f"Failed to post: {item.get('title', 'Unknown')}")

    return posted, errors


def parse_ad_details(ad_url: str) -> Dict:
    """
    Fetch and parse full ad details including all images and description.
    Returns dict with: description, image_urls[], seller_name
    """
    html = http_get(ad_url, retries=2, timeout=20)
    if not html:
        return {"description": "", "image_urls": [], "seller_name": ""}

    soup = BeautifulSoup(html, "html.parser")

    # Description - multiple possible selectors
    description = ""
    desc_selectors = [
        '[data-cy="ad_description"]',
        '[data-testid="ad-description"]',
        ".css-bgzo2k",
        '[class*="description"]',
        "#textContent",
    ]
    for sel in desc_selectors:
        elem = soup.select_one(sel)
        if elem:
            description = elem.get_text(strip=True)
            break

    # All images - try to find gallery images
    image_urls = []

    # Method 1: Look for swiper slides or gallery items
    gallery_selectors = [
        '[data-cy="ad-page-ad-photos"] img',
        '[data-testid="ad-photos"] img',
        ".swiper-slide img",
        '[class*="gallery"] img',
        '[class*="photo"] img',
        ".css-1bmylhj img",  # common OLX container
        ".css-1q5q8yg img",
    ]
    for sel in gallery_selectors:
        imgs = soup.select(sel)
        for img in imgs:
            src = img.get("src") or img.get("data-src") or img.get("data-lazy")
            if src and src.startswith("http"):
                # Remove compression params to get full resolution
                src = re.sub(r";s=\d+x\d+", "", src)
                src = src.replace(";q=70", ";q=95")
                if src not in image_urls:
                    image_urls.append(src)

    # Also fix the thumbnail from listing (main_image_url)
    # Find first image to use as main thumbnail
    main_thumb = image_urls[0] if image_urls else ""

    # Method 2: Look for all large images in the page
    if not image_urls:
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src")
            if src and "olxcdn.com" in src and "image" in src:
                # Filter for larger images only
                if src not in image_urls:
                    image_urls.append(src)

    # Method 3: Look in JSON-LD or other scripts
    if not image_urls:
        scripts = soup.find_all("script", type="application/ld+json")
        for script in scripts:
            try:
                data = json.loads(script.string or "{}")
                if isinstance(data, dict) and "image" in data:
                    imgs = data["image"]
                    if isinstance(imgs, list):
                        for img in imgs:
                            if img not in image_urls:
                                image_urls.append(img)
                    elif isinstance(imgs, str) and imgs not in image_urls:
                        image_urls.append(imgs)
            except:
                pass

    # Seller name
    seller_name = ""
    seller_selectors = [
        '[data-cy="seller-name"]',
        '[data-testid="seller-name"]',
        '[data-cy="seller_card"] h4',
        ".css-1o7o7b2",
        '[class*="seller"] h4',
        'a[href*="/uzytkownik/"]',
    ]
    for sel in seller_selectors:
        elem = soup.select_one(sel)
        if elem:
            seller_name = elem.get_text(strip=True)
            break

    return {
        "description": description,
        "image_urls": image_urls,
        "seller_name": seller_name,
    }


def scrape_ads_with_details(
    url: str, num_items: int, existing_ids: Set[str]
) -> Tuple[List[Dict], List[Dict], Optional[str]]:
    """
    Enhanced scraper that fetches full ad details for new items.
    Returns: (all_items, new_items_with_details, error_message)
    """
    html = http_get(url)
    if not html:
        return [], [], "Failed to fetch HTML (blocked/empty)"

    cards = parse_cards(html, num_items)
    if not cards:
        return [], [], None

    # Find new items
    new_items = [c for c in cards if c["id"] not in existing_ids]

    # Fetch details for new items only
    new_items_with_details = []
    for item in new_items:
        logger.info(f"Fetching details for: {item['title'][:50]}...")
        details = parse_ad_details(item["url"])
        item["description"] = details["description"]
        item["image_urls"] = details["image_urls"]
        item["seller_name"] = details["seller_name"]
        # Update thumbnail to first gallery image (better quality)
        if details["image_urls"]:
            item["thumb"] = details["image_urls"][0]
        new_items_with_details.append(item)
        time.sleep(0.5)  # Be nice to the server

    return cards, new_items_with_details, None
