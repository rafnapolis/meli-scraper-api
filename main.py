import random
from fastapi import FastAPI, HTTPException, Query
import httpx

app = FastAPI(
    title="Mercado Libre LATAM Scraper API",
    description="API de web scraping en tiempo real para Mercado Libre en Latinoamérica.",
    version="2.2.0"
)

# Intentar cargar selectolax, o usar BeautifulSoup como respaldo seguro
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
        "Accept-Language": "es-ES,es;q=0.9,pt-BR;q=0.8,pt;q=0.7,en;q=0.6",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }

async def fetch_pdp(url: str) -> dict:
    # Validar que sea un dominio oficial de Mercado Libre
    if not any(domain in url.lower() for domain in ALLOWED_DOMAINS):
        raise HTTPException(status_code=400, detail="URL no soportada. Ingrese una URL valida de Mercado Libre LATAM.")

    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        try:
            res = await client.get(url, headers=get_headers())
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Error de conexion al servidor: {str(e)}")

    if res.status_code != 200:
        raise HTTPException(status_code=res.status_code, detail=f"No se pudo acceder a la pagina. Codigo HTTP: {res.status_code}")

    html = res.text
    
    if USE_SELECTOLAX:
        tree = HTMLParser(html)
        
        # Selectores Múltiples para Título (Soporta publicaciones estándar y páginas /p/ de catálogo)
        t_node = (
            tree.css_first("h1.ui-pdp-title") or 
            tree.css_first("h1.poly-component__title") or 
            tree.css_first(".ui-pdp-container__row--header h1") or
            tree.css_first("h1")
        )
        title = t_node.text(strip=True) if t_node else "Sin titulo disponible"
        
        # Selectores Múltiples para Precio y Moneda
        c_node = tree.css_first(".andes-money-amount__currency-symbol")
        i_node = (
            tree.css_first("span.ui-pdp-price__second-line .andes-money-amount__fraction") or 
            tree.css_first("span.andes-money-amount__fraction") or 
            tree.css_first(".andes-money-amount__fraction")
        )
        d_node = (
            tree.css_first("span.ui-pdp-price__second-line .andes-money-amount__cents") or 
            tree.css_first("span.andes-money-amount__cents") or 
            tree.css_first(".andes-money-amount__cents")
        )
        
        currency = c_node.text(strip=True) if c_node else "$"
        integer = i_node.text(strip=True) if i_node else "0"
        decimals = d_node.text(strip=True) if d_node else "00"
        
        # Selectores Múltiples para Vendedor
        s_node = (
            tree.css_first(".ui-pdp-seller__link-trigger") or 
            tree.css_first("button.ui-pdp-seller__link-trigger") or 
            tree.css_first(".ui-seller-info__title") or 
            tree.css_first(".ui-pdp-seller__header__title")
        )
        seller = s_node.text(strip=True) if s_node else "Mercado Libre / Vendedor No Especificado"
        
        # Extracción de Condición y Rating
        cond_node = tree.css_first("span.ui-pdp-subtitle")
        condition = cond_node.text(strip=True) if cond_node else "Nuevo"
        
        rate_node = tree.css_first("span.ui-pdp-review__rating")
        rating = rate_node.text(strip=True) if rate_node else "N/D"

    else:
        soup = BeautifulSoup(html, "html.parser")
        
        t_node = (
            soup.select_one("h1.ui-pdp-title") or 
            soup.select_one("h1.poly-component__title") or 
            soup.select_one("h1")
        )
        title = t_node.get_text(strip=True) if t_node else "Sin titulo disponible"
        
        c_node = soup.select_one(".andes-money-amount__currency-symbol")
        i_node = (
            soup.select_one("span.ui-pdp-price__second-line .andes-money-amount__fraction") or 
            soup.select_one("span.andes-money-amount__fraction") or 
            soup.select_one(".andes-money-amount__fraction")
        )
        d_node = (
            soup.select_one("span.ui-pdp-price__second-line .andes-money-amount__cents") or 
            soup.select_one("span.andes-money-amount__cents") or 
            soup.select_one(".andes-money-amount__cents")
        )
        
        currency = c_node.get_text(strip=True) if c_node else "$"
        integer = i_node.get_text(strip=True) if i_node else "0"
        decimals = d_node.get_text(strip=True) if d_node else "00"
        
        s_node = (
            soup.select_one(".ui-pdp-seller__link-trigger") or 
            soup.select_one("button.ui-pdp-seller__link-trigger") or 
            soup.select_one(".ui-seller-info__title")
        )
        seller = s_node.get_text(strip=True) if s_node else "Mercado Libre / Vendedor No Especificado"
        
        cond_node = soup.select_one("span.ui-pdp-subtitle")
        condition = cond_node.get_text(strip=True) if cond_node else "Nuevo"
        
        rate_node = soup.select_one("span.ui-pdp-review__rating")
        rating = rate_node.get_text(strip=True) if rate_node else "N/D"

    return {
        "product_name": title,
        "selling_price": f"{currency} {integer}.{decimals}",
        "price_details": {
            "currency": currency,
            "integer": integer,
            "decimals": decimals
        },
        "condition": condition,
        "rating": rating,
        "seller_name": seller,
        "url": url
    }

# ------------------------------------------------------------------
# RUTAS DE LA API
# ------------------------------------------------------------------

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
