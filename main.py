import random
import re
from fastapi import FastAPI, HTTPException, Query
import httpx

app = FastAPI(
    title="Mercado Libre LATAM Scraper API",
    description="API de web scraping en tiempo real para Mercado Libre en Latinoamérica.",
    version="2.3.0"
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
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3.1 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.40 Mobile Safari/537.36"
]

def get_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,es-ES,es;q=0.8,en;q=0.7",
        "Cache-Control": "max-age=0",
        "Sec-Ch-Ua": '"Google Chrome";v="123", "Not:A-Brand";v="8"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
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
        
        # 1. Extracción de Título (Prioridad: h1 -> meta title -> title tag)
        t_node = (
            tree.css_first("h1.ui-pdp-title") or 
            tree.css_first("h1.poly-component__title") or 
            tree.css_first("h1.ui-search-item__title") or
            tree.css_first("h1")
        )
        if t_node and t_node.text(strip=True):
            title = t_node.text(strip=True)
        else:
            meta_t = tree.css_first("meta[name='title']") or tree.css_first("meta[property='og:title']")
            if meta_t and meta_t.attributes.get("content"):
                title = meta_t.attributes.get("content").split("|")[0].strip()
            else:
                head_t = tree.css_first("title")
                if head_t:
                    title = head_t.text(strip=True).split("|")[0].strip()

        # 2. Extracción de Moneda y Precio
        c_node = tree.css_first(".andes-money-amount__currency-symbol")
        if c_node and c_node.text(strip=True):
            currency = c_node.text(strip=True)
            
        i_node = (
            tree.css_first("span.andes-money-amount__fraction") or 
            tree.css_first(".andes-money-amount__fraction") or
            tree.css_first("meta[itemprop='price']")
        )
        if i_node:
            if i_node.tag == "meta":
                integer = i_node.attributes.get("content", "0").split(".")[0]
            else:
                integer = i_node.text(strip=True)

        d_node = (
            tree.css_first("span.andes-money-amount__cents") or 
            tree.css_first(".andes-money-amount__cents")
        )
        if d_node:
            decimals = d_node.text(strip=True)

        # 3. Vendedor
        s_node = (
            tree.css_first(".ui-pdp-seller__link-trigger") or 
            tree.css_first("button.ui-pdp-seller__link-trigger") or 
            tree.css_first(".ui-seller-info__title")
        )
        if s_node and s_node.text(strip=True):
            seller = s_node.text(strip=True)

    else:
        soup = BeautifulSoup(html, "html.parser")
        
        t_node = (
            soup.select_one("h1.ui-pdp-title") or 
            soup.select_one("h1.poly-component__title") or 
            soup.select_one("h1")
        )
        if t_node and t_node.get_text(strip=True):
            title = t_node.get_text(strip=True)
        else:
            meta_t = soup.select_one("meta[name='title']") or soup.select_one("meta[property='og:title']")
            if meta_t and meta_t.get("content"):
                title = meta_t.get("content").split("|")[0].strip()
            else:
                head_t = soup.select_one("title")
                if head_t:
                    title = head_t.get_text(strip=True).split("|")[0].strip()

        c_node = soup.select_one(".andes-money-amount__currency-symbol")
        if c_node:
            currency = c_node.get_text(strip=True)

        i_node = (
            soup.select_one("span.andes-money-amount__fraction") or 
            soup.select_one(".andes-money-amount__fraction")
        )
        if i_node:
            integer = i_node.get_text(strip=True)

        d_node = soup.select_one("span.andes-money-amount__cents")
        if d_node:
            decimals = d_node.get_text(strip=True)

        s_node = soup.select_one(".ui-pdp-seller__link-trigger")
        if s_node:
            seller = s_node.get_text(strip=True)

    if not title:
        title = "Produto Mercado Livre" if "mercadolivre.com.br" in clean_url else "Producto Mercado Libre"

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
