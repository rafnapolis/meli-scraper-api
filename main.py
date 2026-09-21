import random
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
import httpx
from selectolax.parser import HTMLParser

app = FastAPI(
    title="Mercado Libre LATAM Scraper API",
    description="API de web scraping en tiempo real para Mercado Libre en Latinoamérica.",
    version="2.1.0",
)

ALLOWED_DOMAINS = [
    "mercadolibre.com.mx", "mercadolivre.com.br", "mercadolibre.com.ar",
    "mercadolibre.cl", "mercadolibre.com.co", "mercadolibre.com.pe", "mercadolibre.com.uy"
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.40 Mobile Safari/537.36"
]

def get_random_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,pt-BR;q=0.8",
    }

async def parse_pdp_full(url: str) -> dict:
    if not any(domain in url.lower() for domain in ALLOWED_DOMAINS):
        raise HTTPException(status_code=400, detail="URL no soportada.")

    async with httpx.AsyncClient(follow_redirects=True, timeout=12.0) as client:
        try:
            response = await client.get(url, headers=get_random_headers())
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Error de conexión: {str(exc)}")

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error al acceder a Mercado Libre.")

    tree = HTMLParser(response.text)

    # 1. Product Name
    title_node = tree.css_first("h1.ui-pdp-title")
    title = title_node.text(strip=True) if title_node else None

    # 2. Selling Price & Currency
    currency_node = tree.css_first(".andes-money-amount__currency-symbol")
    integer_node = tree.css_first(".andes-money-amount__fraction")
    decimal_node = tree.css_first(".andes-money-amount__cents")
    
    currency = currency_node.text(strip=True) if currency_node else "$"
    integer_price = integer_node.text(strip=True) if integer_node else "0"
    decimals = decimal_node.text(strip=True) if decimal_node else "00"
    selling_price = f"{currency} {integer_price}.{decimals}"

    # 3. List Price (Precio original antes de descuento)
    original_price_node = tree.css_first("s.andes-money-amount .andes-money-amount__fraction")
    list_price = f"{currency} {original_price_node.text(strip=True)}" if original_price_node else selling_price

    # 4. Discount Percentage
    discount_node = tree.css_first("span.ui-pdp-price__second-line__discount")
    discount = discount_node.text(strip=True) if discount_node else "0%"

    # 5. Installments / EMI
    installments_node = tree.css_first("p.ui-pdp-color--GREEN") or tree.css_first(".ui-pdp-media__title")
    installments = installments_node.text(strip=True) if installments_node else None

    # 6. Rating & Reviews Count
    rating_node = tree.css_first("span.ui-pdp-review__rating")
    reviews_count_node = tree.css_first("span.ui-pdp-review__amount")
    rating = rating_node.text(strip=True) if rating_node else None
    review_count = reviews_count_node.text(strip=True) if reviews_count_node else "0"

    # 7. Seller Name
    seller_node = tree.css_first(".ui-pdp-seller__link-trigger") or tree.css_first("button.ui-pdp-seller__link-trigger")
    seller_name = seller_node.text(strip=True) if seller_node else None

    # 8. Category & Brand
    brand_node = tree.css_first("tr.andes-table__row th.andes-table__header")
    brand = brand_node.text(strip=True) if brand_node else None
    
    categories = [node.text(strip=True) for node in tree.css("a.andes-breadcrumb__link")]

    # 9. Description
    desc_node = tree.css_first("p.ui-pdp-description__content")
    description = desc_node.text(strip=True) if desc_node else None

    return {
        "product_name": title,
        "brand": brand,
        "category": categories,
        "list_price": list_price,
        "selling_price": selling_price,
        "discount": discount,
        "installments": installments,
        "product_rating": rating,
        "review_count": review_count,
        "seller_name": seller_name,
        "description": description,
        "url": url
    }

@app.get("/v1/latam/scrape", summary="PDP Full Scraper")
async def scrape_pdp(url: str = Query(...)):
    return await parse_pdp_full(url)
