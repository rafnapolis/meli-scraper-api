import random
from fastapi import FastAPI, HTTPException, Query
import httpx

app = FastAPI(
    title="Mercado Libre LATAM Scraper API",
    version="2.1.0"
)

# Intentar usar selectolax, si no está instalado usa BeautifulSoup/fallback
try:
    from selectolax.parser import HTMLParser
    USE_SELECTOLAX = True
except ImportError:
    from bs4 import BeautifulSoup
    USE_SELECTOLAX = False

ALLOWED_DOMAINS = [
    "mercadolibre.com.mx", "mercadolivre.com.br", "mercadolibre.com.ar",
    "mercadolibre.cl", "mercadolibre.com.co", "mercadolibre.com.pe", "mercadolibre.com.uy"
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.6312.40 Mobile Safari/537.36"
]

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,pt-BR;q=0.8",
    }

async def fetch_pdp(url: str) -> dict:
    if not any(domain in url.lower() for domain in ALLOWED_DOMAINS):
        raise HTTPException(status_code=400, detail="URL no soportada. Ingrese una URL valida de Mercado Libre.")

    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        try:
            res = await client.get(url, headers=get_headers())
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Error de conexion: {str(e)}")

    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail="No se pudo obtener el producto.")

    html = res.text
    
    if USE_SELECTOLAX:
        tree = HTMLParser(html)
        t_node = tree.css_first("h1.ui-pdp-title")
        title = t_node.text(strip=True) if t_node else "Sin titulo"
        
        c_node = tree.css_first(".andes-money-amount__currency-symbol")
        i_node = tree.css_first(".andes-money-amount__fraction")
        d_node = tree.css_first(".andes-money-amount__cents")
        
        currency = c_node.text(strip=True) if c_node else "$"
        integer = i_node.text(strip=True) if i_node else "0"
        decimals = d_node.text(strip=True) if d_node else "00"
        
        s_node = tree.css_first(".ui-pdp-seller__link-trigger")
        seller = s_node.text(strip=True) if s_node else "N/D"
    else:
        soup = BeautifulSoup(html, "html.parser")
        t_node = soup.select_one("h1.ui-pdp-title")
        title = t_node.get_text(strip=True) if t_node else "Sin titulo"
        
        c_node = soup.select_one(".andes-money-amount__currency-symbol")
        i_node = soup.select_one(".andes-money-amount__fraction")
        d_node = soup.select_one(".andes-money-amount__cents")
        
        currency = c_node.get_text(strip=True) if c_node else "$"
        integer = i_node.get_text(strip=True) if i_node else "0"
        decimals = d_node.get_text(strip=True) if d_node else "00"
        
        s_node = soup.select_one(".ui-pdp-seller__link-trigger")
        seller = s_node.get_text(strip=True) if s_node else "N/D"

    return {
        "product_name": title,
        "selling_price": f"{currency} {integer}.{decimals}",
        "price_details": {
            "currency": currency,
            "integer": integer,
            "decimals": decimals
        },
        "seller_name": seller,
        "url": url
    }

# Endpoint Raiz para que no de 404 al abrir el dominio directamente
@app.get("/")
async def root():
    return {"status": "ok", "message": "Mercado Libre Scraper API esta activa."}

# Endpoint Universal LATAM
@app.get("/v1/latam/scrape")
async def scrape_latam(url: str = Query(...)):
    return await fetch_pdp(url)

# Endpoints por Pais
@app.get("/v1/mx/scrape")
async def scrape_mx(url: str = Query(...)):
    return await fetch_pdp(url)

@app.get("/v1/br/scrape")
async def scrape_br(url: str = Query(...)):
    return await fetch_pdp(url)
