import httpx
import random
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from typing import Dict, Any

app = FastAPI(
    title="MELI Scraper API",
    description="API profesional para extracción de datos de Mercado Libre (MX/BR)",
    version="1.0.0"
)

# Lista de User-Agents reales para rotación
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Mobile/15E148 Safari/604.1"
]

def get_headers() -> Dict[str, str]:
    """Genera headers aleatorios para emular un navegador real."""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,/ ;q=0.8",
        "Referer": "https://www.google.com/"
    }

async def scrape_meli_product(url: str) -> Dict[str, Any]:
    """Lógica central de extracción asíncrona."""
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        try:
            response = await client.get(url, headers=get_headers())
            
            if response.status_code == 404:
                raise HTTPException(status_code=404, detail="Producto no encontrado")
            if response.status_code != 200:
                raise HTTPException(status_code=400, detail=f"Error de conexión con Mercado Libre: {response.status_code}")

            soup = BeautifulSoup(response.text, "html.parser")

            # Extracción con selectores robustos (clases comunes de MELI)
            title_element = soup.find("h1", class_="ui-pdp-title")
            price_fraction = soup.find("span", class_="andes-money-amount__fraction")
            price_cents = soup.find("span", class_="andes-money-amount__cents")
            currency = soup.find("span", class_="andes-money-amount__currency-symbol")
            condition = soup.find("span", class_="ui-pdp-subtitle")

            if not title_element:
                raise ValueError("No se pudo parsear la estructura del producto")

            return {
                "title": title_element.get_text().strip(),
                "price": {
                    "currency": currency.get_text().strip() if currency else None,
                    "integer": price_fraction.get_text().replace(".", "").strip() if price_fraction else "0",
                    "decimals": price_cents.get_text().strip() if price_cents else "00"
                },
                "condition": condition.get_text().split("|")[0].strip() if condition else "No especificado",
                "url": url
            }

        except httpx.RequestError as exc:
            raise HTTPException(status_code=400, detail=f"Error de red: {exc}")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Error procesando la URL: {str(e)}")

@app.get("/v1/mx/scrape")
async def scrape_mexico(url: str = Query(..., description="URL de Mercado Libre México")):
    if "mercadolibre.com.mx" not in url:
        raise HTTPException(status_code=400, detail="La URL debe pertenecer a mercadolibre.com.mx")
    return await scrape_meli_product(url)

@app.get("/v1/br/scrape")
async def scrape_brasil(url: str = Query(..., description="URL de Mercado Libre Brasil")):
    if "mercadolivre.com.br" not in url:
        raise HTTPException(status_code=400, detail="La URL debe pertenecer a mercadolivre.com.br")
    return await scrape_meli_product(url)

@app.get("/")
def health_check():
    return {"status": "online", "message": "MELI Scraper API is running"}
