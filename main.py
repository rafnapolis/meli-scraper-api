import os
import time
import random
import re
import json
import base64
import urllib.parse
import logging
from fastapi import FastAPI, HTTPException, Query
import httpx
from collections import OrderedDict

# Configurar logging básico para monitoreo en Render/RapidAPI
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Mercado Libre LATAM Scraper API",
    version="2.15.0"
)

_START_TIME = time.time()

# --- Credenciales de la API oficial (configurar como Secret Env Vars en Render) ---
ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "")
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "")
ML_REDIRECT_URI = os.getenv("ML_REDIRECT_URI", "")

# Token de aplicacion (client_credentials) con refresh automatico
_ml_app_token: dict = {"token": None, "expires_at": 0.0}


def ml_headers() -> dict:
    """Headers para api.mercadolibre.com; usa Bearer token si hay credenciales."""
    h = {"Accept": "application/json"}
    tok = _ml_app_token["token"]
    if tok and _ml_app_token["expires_at"] > time.time():
        h["Authorization"] = f"Bearer {tok}"
    return h


async def get_ml_app_token(force: bool = False) -> str | None:
    """Obtiene access_token de app publica via client_credentials."""
    if not ML_CLIENT_ID or not ML_CLIENT_SECRET:
        return None
    if not force and _ml_app_token["token"] and _ml_app_token["expires_at"] > time.time() + 60:
        return _ml_app_token["token"]
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.post(
                "https://api.mercadolibre.com/oauth/token",
                json={
                    "grant_type": "client_credentials",
                    "client_id": ML_CLIENT_ID,
                    "client_secret": ML_CLIENT_SECRET,
                },
            )
            if res.status_code == 200:
                data = res.json()
                _ml_app_token["token"] = data.get("access_token")
                _ml_app_token["expires_at"] = time.time() + int(data.get("expires_in", 21600)) - 300
                return _ml_app_token["token"]
    except httpx.HTTPError as e:
        logger.warning(f"Error HTTP obteniendo token ML: {e}")
    except Exception as e:
        logger.error(f"Error inesperado obteniendo token ML: {e}")
    return None


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

CURRENCY_SYMBOLS = {
    "BRL": "R$", "ARS": "AR$", "MXN": "MX$", "CLP": "CLP$",
    "COP": "COL$", "PEN": "S/", "UYU": "$U", "VES": "Bs.", "USD": "US$"
}


def clean_title_from_url(url: str) -> str:
    """Extrae el título formateado directamente desde el enlace si el HTML viene bloqueado."""
    try:
        path = re.split(r"mercadol(?:ibre|ivre)\.[a-z.]+/", url)[-1]
        raw_slug = re.split(r"/p/|/ML[A-Z]", path)[0]
        words = raw_slug.replace("-", " ").strip().split()
        return " ".join(word.capitalize() for word in words)
    except Exception:
        return "Producto Mercado Libre"


def extract_item_id(url: str) -> tuple[str | None, bool]:
    """
    Retorna una tupla (item_id, is_catalog).
    Versión mejorada: busca el patrón ML[PAIS] en cualquier parte de la URL.
    """
    # 1. Buscar si es catálogo (/p/MLA...)
    p_match = re.search(r'/p/(ML[A-Z]{2}-?\d+)', url)
    if p_match:
        return p_match.group(1).replace("-", ""), True

    # 2. Buscar CUALQUIER ID de publicación en la URL (ignora tracking params)
    item_match = re.search(r'(ML[A-Z]{2}-?\d+)', url)
    if item_match:
        return item_match.group(1).replace("-", ""), False

    return None, False


def get_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,es-ES,es;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Upgrade-Insecure-Requests": "1"
    }


def set_price_from_number(price_val, currency_code: str | None = None) -> tuple[str, str, str]:
    """Convierte un precio numérico (float/int/str) en (integer, decimals, currency_symbol)."""
    price_str = str(price_val)
    if "." in price_str:
        integer, decimals = price_str.split(".", 1)
        decimals = (decimals + "00")[:2]
    else:
        integer, decimals = price_str, "00"
    symbol = CURRENCY_SYMBOLS.get(currency_code or "", "")
    return integer, decimals, symbol


def normalize_integer_decimals(integer: str, decimals: str) -> tuple[str, str]:
    """Normaliza el par (entero, decimales) sin destruir valores."""
    integer = str(integer).strip()
    decimals = str(decimals).strip()

    if "." in integer:
        head, tail = integer.rsplit(".", 1)
        if tail.isdigit() and len(tail) <= 2:
            integer, decimals = head, (tail + "00")[:2]
        else:
            integer = integer.replace(".", "")
    integer = integer.replace(",", "").replace(" ", "")

    if not decimals or decimals == "None":
        decimals = "00"
    decimals = re.sub(r"\D", "", decimals)
    decimals = (decimals + "00")[:2]
    return integer, decimals


def parse_localized_price(text: str, default_currency: str) -> tuple[str, str, str]:
    """Parsea un precio formateado localmente, ej: '1.234,56' (BR/AR) o '1,234.56'."""
    text = text.strip()
    symbol = default_currency
    curr_match = re.match(r'^\s*(R\$|US\$|\$\$?|S\/|Bs\.?|CLP\$|COL\$|AR\$|MX\$)\s*', text)
    if curr_match:
        symbol = curr_match.group(1)
        text = text[curr_match.end():]

    nums = re.findall(r'[\d.,]+', text)
    if not nums:
        return "0", "00", symbol
    num = nums[0]

    integer, decimals = num, "00"
    if "," in num and "." in num:
        if num.rfind(",") > num.rfind("."):
            integer, decimals = num.rsplit(",", 1)
            integer = integer.replace(".", "")
        else:
            integer, decimals = num.rsplit(".", 1)
            integer = integer.replace(",", "")
    elif "," in num:
        tail = num.rsplit(",", 1)
        if len(tail[1]) == 2:
            integer, decimals = tail[0].replace(".", "").replace(",", ""), tail[1]
        else:
            integer = num.replace(",", "")
    elif "." in num:
        tail = num.rsplit(".", 1)
        if len(tail[1]) == 3:
            integer = num.replace(".", "")
        else:
            integer, decimals = tail[0], (tail[1] + "00")[:2]
    decimals = (decimals + "00")[:2]
    return integer, decimals, symbol


def extract_price_from_state_scripts(html: str) -> dict | None:
    """Busca el estado inicial de la PDP embebido en <script>."""
    candidates = re.findall(r'"price"\s*:\s*\{[^{}]*?"amount"\s*:\s*([\d.]+)', html)
    if candidates:
        return {"amount": candidates[0]}

    m = re.search(r'"buyBoxPrice"[^0-9]{0,80}([0-9]+(?:\.[0-9]{1,2})?)', html)
    if m:
        return {"amount": m.group(1)}

    m = re.search(r'window\.__PRELOADED_STATE__\s*=\s*(\{.*?\});', html, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1))
            stack = [data]
            while stack:
                node = stack.pop()
                if isinstance(node, dict):
                    if node.get("id") in ("AND_MONEY", "UI_ANDIS") and "content" in node:
                        txt = re.sub(r'<[^>]+>', '', str(node["content"]))
                        found = re.search(r'([\d.,]+\s*R?\$|R?\$\s*[\d.,]+|[A-Z]{3}\s*\$?\s*[\d.,]+)', txt)
                        if found:
                            return {"localized": found.group(1)}
                    stack.extend(node.values())
                elif isinstance(node, list):
                    stack.extend(node)
        except Exception:
            pass
    return None


def extract_price_from_deep_scripts(html: str) -> dict | None:
    """
    NUEVO: Busca precios en variables de JavaScript de forma más agresiva.
    Captura regular_price, price, y estructuras de buybox que ML usa actualmente.
    """
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    for script in scripts:
        # Patrón 1: "regular_price": 1234.56 o "price": 1234.56
        match_price = re.search(r'"(?:regular_price|price)"\s*:\s*([\d.]+)', script)
        if match_price:
            val = match_price.group(1)
            if len(val) > 3: # Un precio real suele tener más de 3 dígitos o decimales
                return {"amount": val}
        
        # Patrón 2: Estructura de buybox de ML ("listing": {"price": ...})
        match_buybox = re.search(r'"buybox"\s*:\s*\{[^}]*?"price"\s*:\s*([\d.]+)', script)
        if match_buybox:
            return {"amount": match_buybox.group(1)}
            
    return None


# --- CACHE EN MEMORIA ---
CACHE_TTL_SECONDS = 6 * 3600
CACHE_MAX_ITEMS = 500
_price_cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()


def cache_get(key: str) -> dict | None:
    entry = _price_cache.get(key)
    if not entry:
        return None
    ts, value = entry
    if time.time() - ts > CACHE_TTL_SECONDS:
        _price_cache.pop(key, None)
        return None
    _price_cache.move_to_end(key)
    return value


def cache_set(key: str, value: dict) -> None:
    _price_cache[key] = (time.time(), value)
    _price_cache.move_to_end(key)
    while len(_price_cache) > CACHE_MAX_ITEMS:
        _price_cache.popitem(last=False)


def _extract_domain(url: str) -> str | None:
    for d in ALLOWED_DOMAINS:
        if d in url.lower():
            return d
    return None


def _site_id_from_url(url: str) -> str | None:
    """Deriva el site_id (MLA, MLB, MLM...) desde el dominio o el prefijo del MLID."""
    m = re.search(r'ML([A-Z]{2})', url)
    if m:
        return "ML" + m.group(1)
    domain_map = {
        "mercadolibre.com.mx": "MLM", "mercadolivre.com.br": "MLB",
        "mercadolibre.com.ar": "MLA", "mercadolibre.cl": "MLC",
        "mercadolibre.com.co": "MCO", "mercadolibre.com.pe": "MPE",
        "mercadolibre.com.uy": "MLU",  # FIX: Código oficial de Uruguay es MLU
        "mercadolibre.com.ec": "MEC",
        "mercadolibre.com.ve": "MLV",
    }
    for d, s in domain_map.items():
        if d in url.lower():
            return s
    return None


def _catalog_to_item_ids(data: dict) -> list[str]:
    """IDs de publicaciones reales dentro de un producto de catalogo (/p/)."""
    ids: list[str] = []
    candidates = json.dumps(data)[:200000]
    # FIX: eliminado espacio extra en el regex que rompía la coincidencia
    for m in re.finditer(r'"(?:item_id|id)"\s*:\s*"? ?(ML[A-Z]-?\d+)"?', candidates):
        raw = m.group(1).replace("-", "")
        if raw not in ids:
            ids.append(raw)
    
    perm = data.get("permalink") or ""
    for s in (data.get("sellers_states") or []):
        p = (s.get("seller") or {}).get("permalink") or s.get("permalink") or ""
        if p:
            perm = p
            break
    for m in re.finditer(r'(ML[A-Z]-?\d+)', perm):
        raw = m.group(1).replace("-", "")
        if raw not in ids:
            ids.append(raw)
    return ids[:5]


async def fetch_from_official_api(url: str) -> dict | None:
    """Ultimo recurso 100% gratis: API oficial de Mercado Libre."""
    item_id, is_catalog = extract_item_id(url)
    if not item_id:
        return None

    currency = "R$" if "mercadolivre.com.br" in url else "$"
    seller = "Mercado Livre / Vendedor Oficial" if "mercadolivre.com.br" in url else "Mercado Libre / Vendedor Oficial"

    tok = await get_ml_app_token()

    async with httpx.AsyncClient(timeout=8.0, headers=ml_headers()) as client:
        try:
            endpoint = f"https://api.mercadolibre.com/products/{item_id}" if is_catalog \
                else f"https://api.mercadolibre.com/items/{item_id}"
            api_res = await client.get(endpoint)

            if api_res.status_code == 403 and not tok and is_catalog:
                site = _site_id_from_url(url)
                dres = None
                if site:
                    dres = await client.get(f"https://api.mercadolibre.com/sites/{site}/domains/{item_id}")
                if dres and dres.status_code == 200:
                    ddata = dres.json()
                    real_ids = [i.get("id") for i in (ddata.get("automatic_by_item") or []) if i.get("id")]
                    if not real_ids:
                        real_ids = _catalog_to_item_ids(ddata)
                    for rid in real_ids[:3]:
                        it = await client.get(f"https://api.mercadolibre.com/items/{rid}")
                        if it.status_code == 200:
                            api_res = it
                            is_catalog = False
                            break

                if api_res.status_code != 200:
                    wid_match = re.search(r'[?&#]wid=(ML[A-Z]-?\d+)', url)
                    if wid_match:
                        wid = wid_match.group(1).replace("-", "")
                        it = await client.get(f"https://api.mercadolibre.com/items/{wid}")
                        if it.status_code == 200:
                            api_res = it
                            is_catalog = False

                if api_res.status_code != 200:
                    return None

            if api_res.status_code != 200:
                return None
            data = api_res.json()

            title = data.get("name") or data.get("title") or clean_title_from_url(url)
            price_raw, curr_code = None, data.get("currency_id")

            if is_catalog:
                buy_box = data.get("buy_box_winner")
                if buy_box and buy_box.get("price") is not None:
                    price_raw = buy_box["price"]
                elif data.get("price") is not None:
                    price_raw = data["price"]

                if price_raw is None:
                    states = data.get("sellers_states") or []
                    perm = None
                    for s in states:
                        perm = (s.get("seller") or {}).get("permalink") or s.get("permalink")
                        if perm:
                            break
                    if not perm:
                        perm = data.get("permalink")
                    if perm:
                        # FIX: [A-Z] en lugar de [AZ] para coincidir con cualquier letra (MLB, MLM, etc.)
                        m = re.search(r'(ML[A-Z]-?\d+)', perm or "")
                        if m:
                            it = await client.get(f"https://api.mercadolibre.com/items/{m.group(1).replace('-', '')}")
                            if it.status_code == 200:
                                idata = it.json()
                                price_raw = idata.get("price")
                                curr_code = idata.get("currency_id") or curr_code
                                title = idata.get("title") or title
            else:
                price_raw = data.get("price")

            if price_raw is None or str(price_raw) in ("0", ""):
                return None

            integer, decimals, sym = set_price_from_number(price_raw, curr_code)
            if sym:
                currency = sym

            return {
                "product_name": title,
                "selling_price": f"{currency} {integer}.{decimals}",
                "price_details": {"currency": currency, "integer": integer, "decimals": decimals},
                "condition": data.get("condition", "Nuevo") or "Nuevo",
                "seller_name": seller,
                "url": url,
                "source": "official_api"
            }
        except httpx.HTTPError as e:
            logger.warning(f"Error HTTP en API oficial: {e}")
            return None
        except Exception as e:
            logger.error(f"Error inesperado en API oficial: {e}")
            return None


BLOCK_PATTERNS = (
    "Por segurança", "completá este paso", "Completa este paso",
    "captcha", "verificación", "no somos un robot", "Parece que eres un robot"
)


def looks_blocked(title, integer: str) -> bool:
    """Detecta si la PDP devolvio 200 pero con pantalla anti-bot/captcha."""
    t = str(title or "")
    return integer in ("0", "") and any(p.lower() in t.lower() for p in BLOCK_PATTERNS)


async def fetch_pdp(url: str) -> dict:
    clean_url = re.split(r'[?#]', url)[0]

    if not any(domain in clean_url.lower() for domain in ALLOWED_DOMAINS):
        raise HTTPException(status_code=400, detail="URL no soportada. Ingrese una URL valida de Mercado Libre LATAM.")

    cached = cache_get(clean_url)
    if cached:
        return {**cached, "cached": True}

    async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
        try:
            res = await client.get(clean_url, headers=get_headers())
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Error de conexion al servidor: {str(e)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error inesperado: {str(e)}")

    if res.status_code != 200:
        if res.status_code in (403, 429, 404, 503):
            fb = await fetch_from_official_api(clean_url)
            if fb:
                cache_set(clean_url, fb)
                return fb
        raise HTTPException(status_code=res.status_code, detail=f"No se pudo acceder a la pagina. Codigo HTTP: {res.status_code}")

    html = res.text
    title = None
    currency = "R$" if "mercadolivre.com.br" in clean_url else "$"
    integer = "0"
    decimals = "00"
    seller = "Mercado Livre / Vendedor Oficial" if "mercadolivre.com.br" in clean_url else "Mercado Libre / Vendedor Oficial"

    # --- MÉTODO 1: Extracción vía JSON-LD (Schema.org) ---
    try:
        json_ld_matches = re.findall(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.DOTALL)
        for json_str in json_ld_matches:
            json_str = json_str.strip().replace('\n', '').replace('\r', '')
            try:
                data = json.loads(json_str)
                if isinstance(data, list):
                    data = data[0] if len(data) > 0 else {}

                # FIX: Condición completada correctamente
                if data.get("@type") == "Product" or "offers" in data:
                    if "name" in data and data["name"]:
                        title = data["name"]

                    offers = data.get("offers", {})
                    if isinstance(offers, list) and len(offers) > 0:
                        offers = offers[0]

                    price_val = offers.get("price") or offers.get("lowPrice") or offers.get("highPrice")
                    if price_val is not None and str(price_val) not in ("0", ""):
                        integer, decimals = parse_localized_price(str(price_val), currency)[:2]

                    if offers.get("priceCurrency"):
                        currency = CURRENCY_SYMBOLS.get(offers["priceCurrency"], offers["priceCurrency"])
                    break
            except json.JSONDecodeError:
                continue # Ignorar bloques JSON mal formados y seguir con el siguiente
    except Exception as e:
        logger.warning(f"Error parseando JSON-LD: {e}")

    # --- MÉTODO 2: Parsing de HTML mediante Selectores CSS ---
    if integer == "0" or not title or "seguridad" in str(title).lower() or "segurança" in str(title).lower():
        if USE_SELECTOLAX:
            tree = HTMLParser(html)
            if not title or "seguridad" in str(title).lower():
                t_node = tree.css_first("h1.ui-pdp-title") or tree.css_first("h1.poly-component__title") or tree.css_first("h1")
                if t_node and t_node.text(strip=True) and "seguridad" not in t_node.text(strip=True).lower():
                    title = t_node.text(strip=True)

            if integer == "0":
                i_node = (
                    tree.css_first(".ui-pdp-price__second-line .andes-money-amount__fraction") or
                    tree.css_first(".andes-money-amount__fraction") or
                    tree.css_first("span.andes-money-amount__fraction")
                )
                if i_node:
                    integer = i_node.text(strip=True)

                d_node = (
                    tree.css_first(".ui-pdp-price__second-line .andes-money-amount__cents") or
                    tree.css_first(".andes-money-amount__cents") or
                    tree.css_first("span.andes-money-amount__cents")
                )
                if d_node:
                    decimals = d_node.text(strip=True).replace(",", "").replace(".", "")
        else:
            soup = BeautifulSoup(html, "html.parser")
            if not title or "seguridad" in str(title).lower():
                t_node = soup.select_one("h1.ui-pdp-title") or soup.select_one("h1.poly-component__title") or soup.select_one("h1")
                if t_node and t_node.get_text(strip=True) and "seguridad" not in t_node.get_text(strip=True).lower():
                    title = t_node.get_text(strip=True)

            if integer == "0":
                i_node = soup.select_one(".andes-money-amount__fraction") or soup.select_one("span.andes-money-amount__fraction")
                if i_node:
                    integer = i_node.get_text(strip=True)

                d_node = soup.select_one(".andes-money-amount__cents") or soup.select_one("span.andes-money-amount__cents")
                if d_node:
                    decimals = d_node.get_text(strip=True).replace(",", "").replace(".", "")

    # --- MÉTODO 2.5: Estado embebido de la PDP (scripts React/Redux) ---
    if integer == "0":
        state_price = extract_price_from_state_scripts(html)
        if not state_price:
            # Intentar con la nueva búsqueda profunda
            state_price = extract_price_from_deep_scripts(html)
            
        if state_price:
            if "amount" in state_price:
                integer, decimals, sym = set_price_from_number(state_price["amount"], None)
                if sym:
                    currency = sym
            elif "localized" in state_price:
                integer, decimals, currency = parse_localized_price(state_price["localized"], currency)

    # Fallback si el título vino vacío o con mensaje de bloqueo
    if not title or "seguridad" in str(title).lower() or "segurança" in str(title).lower():
        title = clean_title_from_url(clean_url)

    # --- MÉTODO 3: Fallback AGRESIVO con la API Oficial ---
    # Si el precio sigue siendo 0, o si el HTML contiene "Ver precio", 
    # la API oficial es la ÚNICA forma confiable de obtenerlo sin un navegador real.
    if integer in ("0", "") or (title and "ver precio" in str(title).lower()):
        fb = await fetch_from_official_api(url) # Pasa la URL original para capturar el 'wid' si existe
        if fb:
            cache_set(clean_url, fb)
            return fb

    warning = None
    if integer in ("0", ""):
        warning = "No se pudo obtener el precio: la pagina anti-bot de Meli bloqueo y la API oficial tampoco devolvio oferta."

    integer, decimals = normalize_integer_decimals(integer, decimals)

    result = {
        "product_name": title,
        "selling_price": f"{currency} {integer}.{decimals}",
        "price_details": {
            "currency": currency,
            "integer": integer,
            "decimals": decimals
        },
        "condition": "Nuevo",
        "seller_name": seller,
        "url": url,
        "source": "pdp_html",
        "warning": warning
    }

    if integer and integer != "0":
        cache_set(clean_url, result)

    return result


@app.get("/")
async def root():
    return {"status": "ok", "message": "Mercado Libre Scraper API esta activa y funcionando."}


_keepalive_log: dict = {"last_hit": 0.0, "total_hits": 0}


@app.get("/health")
async def health():
    _keepalive_log["last_hit"] = time.time()
    _keepalive_log["total_hits"] += 1
    return {
        "status": "healthy",
        "uptime_seconds": int(time.time() - _START_TIME),
        "keepalive_hits": _keepalive_log["total_hits"],
        "cache_size": len(_price_cache),
    }


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


@app.get("/v1/ml/status", summary="Estado del token de la API oficial")
async def ml_status():
    tok = _ml_app_token["token"]
    if ML_CLIENT_ID and (not tok or _ml_app_token["expires_at"] <= time.time()):
        tok = await get_ml_app_token(force=True)
    return {
        "credentials_configured": bool(ML_CLIENT_ID and ML_CLIENT_SECRET),
        "app_token_active": bool(tok),
        "token_prefix": (tok[:12] + "...") if tok else None,
        "expires_in_seconds": max(0, int(_ml_app_token["expires_at"] - time.time())) if tok else 0,
    }


# FIX: Corregida la firma de la función y el uso de variables
def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


# Guardamos temporalmente el state/verifier mientras el usuario autoriza en Meli
_oauth_pending: dict = {}


@app.get("/v1/ml/auth/login", summary="Iniciar autorizacion de usuario (opcional)")
async def ml_auth_login():
    """Genera la URL para que un vendedor/usuario autorice tu app (scope read_only)."""
    if not ML_CLIENT_ID or not ML_REDIRECT_URI:
        raise HTTPException(status_code=400, detail="Faltan ML_CLIENT_ID / ML_REDIRECT_URI")
    import secrets
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(__import__("hashlib").sha256(verifier.encode()).digest())
    state = _b64url(secrets.token_bytes(16))
    
    # FIX: Usar el valor real de 'state' como clave, no la cadena literal "state"
    _oauth_pending[state] = {"verifier": verifier, "state": state}
    
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": ML_CLIENT_ID,
        "redirect_uri": ML_REDIRECT_URI,
        "scope": "offline_access read_only",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return {"authorization_url": f"https://auth.mercadolibre.com/authorization?{params}"}


@app.get("/v1/ml/auth/callback", summary="Callback OAuth (lo llama Meli tras autorizar)")
async def ml_auth_callback(code: str = Query(...), state: str = Query("")):
    """Intercambia el code por un user token."""
    # FIX: .pop() para limpiar la memoria y evitar memory leaks, buscando por la clave correcta
    pending = _oauth_pending.pop(state, None)
    if not pending:
        raise HTTPException(status_code=400, detail="State invalido o expirado, reintenta /v1/ml/auth/login")
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            res = await client.post("https://api.mercadolibre.com/oauth/token", json={
                "grant_type": "authorization_code",
                "client_id": ML_CLIENT_ID,
                "client_secret": ML_CLIENT_SECRET,
                "code": code,
                "redirect_uri": ML_REDIRECT_URI,
                "code_verifier": pending["verifier"],
            })
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"Error de conexion con ML OAuth: {str(e)}")
            
    if res.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Error intercambiando code: {res.text}")
    
    data = res.json()
    return {
        "user_id": data.get("user_id"),
        "access_token": data.get("access_token"),
        "refresh_token": data.get("refresh_token"),
        "expires_in": data.get("expires_in"),
        "note": "Guarda access_token como env var ML_USER_TOKEN en Render y borra este mensaje.",
    }
