import random
import re
from fastapi import FastAPI, HTTPException, Query
import httpx

app = FastAPI(
    title="Mercado Libre LATAM Scraper API",
    version="2.5.0"
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
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
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

def extract_item_id(url: str) -> str | None:
    """Extrae el ID del producto (ej: MLB58353028 o MLA26219803) para consultar la API directa."""
    match = re.search(r'/(ML[A-Z]\d+)', url)
    if match:
        return match.group(1)
    match_p = re.search(r'/p/(ML[A-Z]\d+)', url)
    if match_p:
        return match_p.group(1)
    return None

def get_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
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

    if USE_SELECTOLAX:
        tree = HTMLParser(html)
        
        # 1. Título
        t_node = tree.css_first("h1.ui-pdp-title") or tree.css_first("h1.poly-component__title") or tree.css_first("h1")
        if t_node and t_node.text(strip=True) and "Por segurança" not in t_node.text(strip=True):
            title = t_node.text(strip=True)
        else:
            meta_t = tree.css_first("meta[name='title']") or tree.css_first("meta[property='og:title']")
            if meta_t and meta_t.attributes.get("content") and "Por segurança" not in meta_t.attributes.get("content"):
                title = meta_t.attributes.get("content").split("|")[0].strip()

        # 2. Precio desde HTML
        i_node = tree.css_first("span.andes-money-amount__fraction") or tree.css_first(".andes-money-amount__fraction")
        if i_node:
            integer = i_node.text(strip=True)

        d_node = tree.css_first("span.andes-money-amount__cents") or tree.css_first(".andes-money-amount__cents")
        if d_node:
            decimals = d_node.text(strip=True)

        s_node = tree.css_first(".ui-pdp-seller__link-trigger") or tree.css_first(".ui-seller-info__title")
        if s_node and s_node.text(strip=True):
            seller = s_node.text(strip=True)

    # Fallback si el título viene vació o bloqueado
    if not title or "Por segurança" in title or "Seguridad" in title:
        title = clean_title_from_url(url)

    # Fallback de Precio mediante API Pública si el HTML devolvió 0 por bloqueo anti-bot
    if integer == "0" or integer == "":
        item_id = extract_item_id(url)
        if item_id:
            async with httpx.AsyncClient(timeout=5.0) as client:
                try:
                    # Intento consulta a API directa de productos o catálogo
                    api_res = await client.get(f"https://api.mercadolibre.com/products/{item_id}")
                    if api_res.status_code == 200:
                        data = api_res.json()
                        if "name" in data and data["name"]:
                            title = data["name"]
                        if "buy_box_winner" in data and data["buy_box_winner"]:
                            price_val = str(data["buy_box_winner"].get("price", "0"))
                            parts = price_val.split(".")
                            integer = parts[0]
                            decimals = parts[1] if len(parts) > 1 else "00"
                    else:
                        # Intento con endpoint alternativo de publicaciones
                        item_res = await client.get(f"https://api.mercadolibre.com/items/{item_id}")
                        if item_res.status_code == 200:
                            data = item_res.json()
                            price_val = str(data.get("price", "0"))
                            parts = price_val.split(".")
                            integer = parts[0]
                            decimals = parts[1] if len(parts) > 1 else "00"
                except Exception:
                    pass

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
