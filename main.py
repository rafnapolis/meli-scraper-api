import random
import re
import json
from fastapi import FastAPI, HTTPException, Query
import httpx

app = FastAPI(
    title="Mercado Libre LATAM Scraper API",
    version="2.8.0"
)

try:
    from selectolax.parser import HTMLParser
    USE_SELECTOLAX = True
except ImportError:
    from bs4 import BeautifulSoup
    USE_SELECTOLAX = False

ALLOWED_DOMAINS = [
    "mercadolibre.com.mx", "mercadolivre.com.br", "mercadolibre.com.ar",
    "mercadolibre.cl", "mercadolibre.com.co", "mercadolibre.com.pe", 
    "mercadolibre.com.uy", "mercadolibre.com.ec", "mercadolibre.com.ve"
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
]

def clean_title_from_url(url: str) -> str:
    try:
        path = url.split("mercadolivre.com.br/")[-1].split("mercadolibre.com.ar/")[-1].split("mercadolibre.com.mx/")[-1]
        raw_slug = path.split("/p/")[0].split("/MLA")[0].split("/MLB")[0]
        words = raw_slug.replace("-", " ").strip().split()
        return " ".join(word.capitalize() for word in words)
    except Exception:
        return "Producto Mercado Libre"

def extract_item_id(url: str) -> tuple[str | None, str]:
    """
    Retorna (id, site_id) ej: ('MLA53301314', 'MLA')
    """
    p_match = re.search(r'/(p/)?(ML[A-Z])[-_]?(\d+)', url)
    if p_match:
        site_prefix = p_match.group(2)
        number_id = p_match.group(3)
        return f"{site_prefix}{number_id}", site_prefix
    return None, "MLA"

def get_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "es-AR,es-ES,es;q=0.9,pt-BR;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Upgrade-Insecure-Requests": "1"
    }

async def fetch_pdp(url: str) -> dict:
    clean_url = url.split("?")[0] if "?" in url else url
    
    if not any(domain in clean_url.lower() for domain in ALLOWED_DOMAINS):
        raise HTTPException(status_code=400, detail="URL no soportada. Ingrese una URL valida de Mercado Libre LATAM.")

    html = ""
    res_status = 200

    async with httpx.AsyncClient(follow_redirects=True, timeout=12.0) as client:
        try:
            res = await client.get(url, headers=get_headers())
            res_status = res.status_code
            if res_status == 200:
                html = res.text
        except Exception:
            res_status = 502

    title = None
    currency = "R$" if "mercadolivre.com.br" in clean_url else "$"
    integer = "0"
    decimals = "00"
    seller = "Mercado Livre / Vendedor Oficial" if "mercadolivre.com.br" in clean_url else "Mercado Libre / Vendedor Oficial"

    # 1. SCRAPING DIRECTO SI LA PÁGINA RESPONDIÓ SIN CAPTCHA
    if res_status == 200 and html and "completá este paso" not in html and "Por segurança" not in html:
        try:
            json_ld_matches = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
            for json_str in json_ld_matches:
                data = json.loads(json_str.strip())
                if isinstance(data, list):
                    data = data[0] if len(data) > 0 else {}
                
                if data.get("@type") == "Product" or "offers" in data:
                    if "name" in data and data["name"]:
                        title = data["name"]
                    
                    offers = data.get("offers", {})
                    if isinstance(offers, list) and len(offers) > 0:
                        offers = offers[0]
                    
                    price_val = str(offers.get("price", "0"))
                    if price_val != "0" and price_val != "":
                        parts = price_val.split(".")
                        integer = parts[0]
                        decimals = parts[1] if len(parts) > 1 else "00"
                        if len(decimals) == 1:
                            decimals += "0"
                    
                    if offers.get("priceCurrency"):
                        curr_code = offers.get("priceCurrency")
                        currency = "R$" if curr_code == "BRL" else ("$" if curr_code in ["ARS", "MXN", "CLP", "COP"] else curr_code)
                    break
        except Exception:
            pass

        if integer == "0" or not title:
            if USE_SELECTOLAX:
                tree = HTMLParser(html)
                t_node = tree.css_first("h1.ui-pdp-title") or tree.css_first("h1.poly-component__title") or tree.css_first("h1")
                if t_node and t_node.text(strip=True):
                    title = t_node.text(strip=True)

                i_node = tree.css_first("span.andes-money-amount__fraction") or tree.css_first(".andes-money-amount__fraction")
                if i_node:
                    integer = i_node.text(strip=True)

                d_node = tree.css_first("span.andes-money-amount__cents") or tree.css_first(".andes-money-amount__cents")
                if d_node:
                    decimals = d_node.text(strip=True)

                s_node = tree.css_first(".ui-pdp-seller__link-trigger") or tree.css_first(".ui-seller-info__title")
                if s_node and s_node.text(strip=True):
                    seller = s_node.text(strip=True)

    # 2. RESPALDO ANTI-BLOQUEO: CONSULTA A LA API OFICIAL DE MERCADO LIBRE
    if integer == "0" or integer == "" or not title or "completá este paso" in str(title) or "Por segurança" in str(title):
        item_id, site_id = extract_item_id(url)
        clean_title = clean_title_from_url(url)
        if not title or "completá este paso" in str(title) or "Por segurança" in str(title):
            title = clean_title

        if item_id:
            async with httpx.AsyncClient(timeout=8.0) as client:
                try:
                    # A. Probar con API de productos de catálogo (/products/...)
                    prod_res = await client.get(f"https://api.mercadolibre.com/products/{item_id}")
                    if prod_res.status_code == 200:
                        p_data = prod_res.json()
                        if "name" in p_data and p_data["name"]:
                            title = p_data["name"]
                        buy_box = p_data.get("buy_box_winner")
                        if buy_box and "price" in buy_box:
                            price_val = str(buy_box["price"])
                            parts = price_val.split(".")
                            integer = parts[0]
                            decimals = parts[1] if len(parts) > 1 else "00"
                            if len(decimals) == 1:
                                decimals += "0"
                    else:
                        # B. Probar con API de ítems (/items/...)
                        item_res = await client.get(f"https://api.mercadolibre.com/items/{item_id}")
                        if item_res.status_code == 200:
                            i_data = item_res.json()
                            if "title" in i_data and i_data["title"]:
                                title = i_data["title"]
                            price_val = str(i_data.get("price", "0"))
                            parts = price_val.split(".")
                            integer = parts[0]
                            decimals = parts[1] if len(parts) > 1 else "00"
                            if len(decimals) == 1:
                                decimals += "0"
                        else:
                            # C. Búsqueda por palabras clave del título en la API del sitio correspondiente
                            search_q = clean_title.replace(" ", "%20")
                            search_res = await client.get(f"https://api.mercadolibre.com/sites/{site_id}/search?q={search_q}&limit=1")
                            if search_res.status_code == 200:
                                s_data = search_res.json()
                                results = s_data.get("results", [])
                                if results:
                                    first_item = results[0]
                                    if not title or title == "Producto Mercado Libre":
                                        title = first_item.get("title", title)
                                    price_val = str(first_item.get("price", "0"))
                                    parts = price_val.split(".")
                                    integer = parts[0]
                                    decimals = parts[1] if len(parts) > 1 else "00"
                                    if len(decimals) == 1:
                                        decimals += "0"
                except Exception:
                    pass

    integer = integer.replace(".", "").replace(",", "")

    return {
        "product_name": title,
        "selling_price": f"{currency} {integer}.{decimals}",
        "price_details": {
            "currency": currency,
            "integer": integer,
            "decimals": decimals
        },
        "condition": "Nuevo",
        "seller_name": seller,
        "url": url
    }

@app.get("/")
async def root():
    return {"status": "ok", "message": "Mercado Libre Scraper API esta activa y funcionando."}

@app.get("/v1/latam/scrape", summary="Scraper Universal LATAM")
async def scrape_latam(url: str = Query(..., description="URL completa del producto de Mercado Libre")):
    return await fetch_pdp(url)

@app.get("/v1/mx/scrape", summary="Mercado Libre Mexico")
async def scrape_mx(url: str = Query(...)):
    return await fetch_pdp(url)

@app.get("/v1/br/scrape", summary="Mercado Livre Brasil")
async def scrape_br(url: str = Query(...)):
    return await fetch_pdp(url)

@app.get("/v1/ar/scrape", summary="Mercado Libre Argentina")
async def scrape_ar(url: str = Query(...)):
    return await fetch_pdp(url)
