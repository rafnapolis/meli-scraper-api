import random
import re
import json
from fastapi import FastAPI, HTTPException, Query
import httpx

app = FastAPI(
    title="Mercado Libre LATAM Scraper API",
    version="2.6.0"
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
    """Extrae el título formateado directamente desde el enlace si el HTML viene bloqueado."""
    try:
        path = url.split("mercadolivre.com.br/")[-1].split("mercadolibre.com.ar/")[-1].split("mercadolibre.com.mx/")[-1]
        raw_slug = path.split("/p/")[0].split("/MLA")[0].split("/MLB")[0]
        words = raw_slug.replace("-", " ").strip().split()
        return " ".join(word.capitalize() for word in words)
    except Exception:
        return "Producto Mercado Libre"

def extract_item_id(url: str) -> tuple[str | None, bool]:
    """
    Retorna una tupla (item_id, is_catalog).
    Si es /p/MLB123456 -> ('MLB123456', True)
    Si es /MLB-123456 -> ('MLB123456', False)
    """
    p_match = re.search(r'/p/(ML[A-Z]\d+)', url)
    if p_match:
        return p_match.group(1), True
    
    item_match = re.search(r'/(ML[A-Z]-?\d+)', url)
    if item_match:
        raw_id = item_match.group(1).replace("-", "")
        return raw_id, False
        
    return None, False

def get_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,es-ES,es;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Upgrade-Insecure-Requests": "1"
    }

async def fetch_pdp(url: str) -> dict:
    clean_url = url.split("?")[0] if "?" in url else url
    
    if not any(domain in clean_url.lower() for domain in ALLOWED_DOMAINS):
        raise HTTPException(status_code=400, detail="URL no soportada. Ingrese una URL valida de Mercado Libre LATAM.")

    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        try:
            res = await client.get(url, headers=get_headers())
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Error de conexion al servidor: {str(e)}")

    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail=f"No se pudo acceder a la pagina. Codigo HTTP: {res.status_code}")

    html = res.text
    title = None
    currency = "R$" if "mercadolivre.com.br" in clean_url else "$"
    integer = "0"
    decimals = "00"
    seller = "Mercado Livre / Vendedor Oficial" if "mercadolivre.com.br" in clean_url else "Mercado Libre / Vendedor Oficial"

    # --- MÉTODO 1: Extracción vía JSON-LD (Estructura estándar Schema.org) ---
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

    # --- MÉTODO 2: Parsing de HTML mediante Selectores CSS si JSON-LD falla ---
    if integer == "0" or not title or "Por segurança" in str(title):
        if USE_SELECTOLAX:
            tree = HTMLParser(html)
            
            # Título
            if not title or "Por segurança" in str(title):
                t_node = tree.css_first("h1.ui-pdp-title") or tree.css_first("h1.poly-component__title") or tree.css_first("h1")
                if t_node and t_node.text(strip=True) and "Por segurança" not in t_node.text(strip=True):
                    title = t_node.text(strip=True)

            # Precio
            if integer == "0":
                i_node = (
                    tree.css_first(".ui-pdp-price__second-line span.andes-money-amount__fraction") or
                    tree.css_first("span.andes-money-amount__fraction") or 
                    tree.css_first(".andes-money-amount__fraction")
                )
                if i_node:
                    integer = i_node.text(strip=True)

                d_node = (
                    tree.css_first(".ui-pdp-price__second-line span.andes-money-amount__cents") or
                    tree.css_first("span.andes-money-amount__cents") or 
                    tree.css_first(".andes-money-amount__cents")
                )
                if d_node:
                    decimals = d_node.text(strip=True)

            s_node = tree.css_first(".ui-pdp-seller__link-trigger") or tree.css_first(".ui-seller-info__title")
            if s_node and s_node.text(strip=True):
                seller = s_node.text(strip=True)

        else:
            soup = BeautifulSoup(html, "html.parser")
            if not title or "Por segurança" in str(title):
                t_node = soup.select_one("h1.ui-pdp-title") or soup.select_one("h1.poly-component__title") or soup.select_one("h1")
                if t_node and t_node.get_text(strip=True) and "Por segurança" not in t_node.get_text(strip=True):
                    title = t_node.get_text(strip=True)

            if integer == "0":
                i_node = soup.select_one("span.andes-money-amount__fraction") or soup.select_one(".andes-money-amount__fraction")
                if i_node:
                    integer = i_node.get_text(strip=True)

                d_node = soup.select_one("span.andes-money-amount__cents") or soup.select_one(".andes-money-amount__cents")
                if d_node:
                    decimals = d_node.get_text(strip=True)

            s_node = soup.select_one(".ui-pdp-seller__link-trigger") or soup.select_one(".ui-seller-info__title")
            if s_node and s_node.get_text(strip=True):
                seller = s_node.get_text(strip=True)

    # Fallback si el título vino vacío o con mensaje de bloqueo
    if not title or "Por segurança" in title or "Seguridad" in title:
        title = clean_title_from_url(url)

    # --- MÉTODO 3: Fallback con la API Oficial de Mercado Libre ---
    if integer == "0" or integer == "":
        item_id, is_catalog = extract_item_id(url)
        if item_id:
            async with httpx.AsyncClient(timeout=8.0) as client:
                try:
                    if is_catalog:
                        # Endpoint oficial de productos de catálogo (/p/)
                        api_res = await client.get(f"https://api.mercadolibre.com/products/{item_id}")
                        if api_res.status_code == 200:
                            data = api_res.json()
                            if "name" in data and data["name"]:
                                title = data["name"]
                            
                            # Obtener precio del ganador del Buy Box o rango de precio
                            buy_box = data.get("buy_box_winner")
                            if buy_box and "price" in buy_box:
                                price_val = str(buy_box["price"])
                                parts = price_val.split(".")
                                integer = parts[0]
                                decimals = parts[1] if len(parts) > 1 else "00"
                                if len(decimals) == 1:
                                    decimals += "0"
                            elif "price" in data:
                                price_val = str(data["price"])
                                parts = price_val.split(".")
                                integer = parts[0]
                                decimals = parts[1] if len(parts) > 1 else "00"
                                if len(decimals) == 1:
                                    decimals += "0"
                    else:
                        # Endpoint oficial de publicaciones individuales
                        item_res = await client.get(f"https://api.mercadolibre.com/items/{item_id}")
                        if item_res.status_code == 200:
                            data = item_res.json()
                            if "title" in data and data["title"]:
                                title = data["title"]
                            price_val = str(data.get("price", "0"))
                            parts = price_val.split(".")
                            integer = parts[0]
                            decimals = parts[1] if len(parts) > 1 else "00"
                            if len(decimals) == 1:
                                decimals += "0"
                except Exception:
                    pass

    # Formatear adecuadamente los enteros cuando traen separadores de miles
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
