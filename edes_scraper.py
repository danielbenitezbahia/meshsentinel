"""Scraper de la pagina de cortes/obras programadas de EDES (la
distribuidora de electricidad de Bahía Blanca y otras localidades).

La pagina (https://www.infoedes.com/obras-en-tu-barrio/) no guarda
historial propio: muestra solo la lista vigente al momento y se
sobreescribe a medida que cambia. Por eso este scraper guarda cada
corrida con su propia fecha/hora -- el archivo propio ES el historial.

Investigado a mano (2026-10-22): la pagina es WordPress server-renderizado
(el contenido ya viene en el HTML, no hace falta ejecutar JS), y el
/wp-json/ devuelve 401 -- no hay API limpia, hay que parsear el HTML.
El contenido real tiene esta forma (simplificada):

    <p><b>Jueves 22 de octubre</b></p>
    <ul>
      <li><strong>BAHIA BLANCA</strong>
        <br>– de 9 a 13hs, afecta al cuadrante comprendido por las
        calles: Paunero, Fitz Roy, Brickman, Av. Colon
        <br>– de 10 a 14hs, afecta al cuadrante comprendido por las
        calles: Misiones, Sta Fe, Luiggi, Berutti.
      </li>
    </ul>

Sin año en la fecha (se infiere de 'reference', normalmente hoy), y sin
ningun campo de localidad mas especifico que la ciudad (no hay barrio
aparte). Solo aparecio "BAHIA BLANCA" en la muestra -- falta ver con el
tiempo si otras localidades del sudoeste bonaerense (Coronel Dorrego,
Monte Hermoso, Tornquist, etc.) aparecen en otros dias.
"""
import html as html_module
import logging
import re
import sqlite3
import time
from datetime import date
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

URL = "https://www.infoedes.com/obras-en-tu-barrio/"
DB_PATH = "edes_outages.sqlite"

DIAS = r"Lunes|Martes|Mi[ée]rcoles|Jueves|Viernes|S[áa]bado|Domingo"
MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)
MESES_RE = "|".join(MESES)
MES_A_NUM = {mes: i + 1 for i, mes in enumerate(MESES)}

DAY_BLOCK_RE = re.compile(
    rf"<p>\s*<b>\s*(?:{DIAS})\s+(\d{{1,2}})\s+de\s+({MESES_RE})\s*</b>\s*</p>\s*<ul>(.*?)</ul>",
    re.IGNORECASE | re.DOTALL,
)
LI_RE = re.compile(r"<li>(.*?)</li>", re.IGNORECASE | re.DOTALL)
# La localidad vino siempre envuelta en <strong>, hasta que EDES cambió a
# <b> sin aviso (encontrado 2026-11-02: la página dejó de dar resultados
# de un día para el otro). Se acepta cualquiera de las dos para no romper
# de nuevo si vuelven a alternar.
LOCALITY_RE = re.compile(r"<(strong|b|em)>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
TAG_RE = re.compile(r"<[^>]+>")
# La frase despues del horario solía ser siempre "afecta al cuadrante
# comprendido por las calles: X" -- ahora puede ser "afecta a la zona de
# X", o no haber nada más que el horario. El horario es lo único que se
# exige; el resto (si hay) se limpia en _extract_streets().
SEGMENT_RE = re.compile(
    r"de\s+(\d{1,2})\s+a\s+(\d{1,2})\s*hs\.?,?\s*(.*)$",
    re.IGNORECASE,
)
AFECTA_PREFIX_RE = re.compile(
    r"^afecta\s+(?:al\s+cuadrante\s+comprendido\s+por\s+las\s+calles|a\s+la\s+zona\s+de)\s*:?\s*",
    re.IGNORECASE,
)


def _extract_streets(remainder: str) -> str:
    """Saca el conector ("afecta al cuadrante comprendido por las calles:",
    "afecta a la zona de") si está, para dejar solo la descripción. Si no
    hay conector conocido (formato nuevo a veces no pone nada), devuelve
    el texto tal cual -- puede quedar vacío, y es una fila válida igual."""
    return AFECTA_PREFIX_RE.sub("", remainder.strip()).strip()


def fetch_html() -> str:
    resp = requests.get(
        URL, timeout=20,
        headers={"User-Agent": "MeshBoardBot/1.0 (+https://sentinelmesh.ar)"},
    )
    resp.raise_for_status()
    return resp.text


def _clean(text: str) -> str:
    return html_module.unescape(TAG_RE.sub(" ", text)).strip()


def _resolve_year(day_num: int, month_num: int, reference: date) -> int:
    """La pagina no pone año -- se infiere del año de referencia (hoy),
    salvo que la fecha quede muy en el pasado (p.ej. estamos en enero y
    la pagina menciona diciembre), en cuyo caso se asume año siguiente."""
    year = reference.year
    try:
        candidate = date(year, month_num, day_num)
    except ValueError:
        return year
    if (reference - candidate).days > 180:
        return year + 1
    return year


def parse(raw_html: str, reference: Optional[date] = None) -> List[Dict]:
    """Devuelve una lista de dicts: outage_date, locality, time_start,
    time_end, streets, raw_line. Lista vacía si no hay nada publicado
    (o si la pagina cambió de formato -- no hay forma de distinguir
    ambos casos solo mirando el HTML, por eso run() loguea un warning
    cuando da cero)."""
    reference = reference or date.today()
    results = []

    for day_match in DAY_BLOCK_RE.finditer(raw_html):
        day_num = int(day_match.group(1))
        month_num = MES_A_NUM[day_match.group(2).lower()]
        year = _resolve_year(day_num, month_num, reference)
        try:
            outage_date = date(year, month_num, day_num)
        except ValueError:
            logger.warning("EDES: fecha invalida en el heading (dia=%s, mes=%s)", day_num, month_num)
            continue

        ul_content = day_match.group(3)

        for li_match in LI_RE.finditer(ul_content):
            li_content = li_match.group(1)
            locality_match = LOCALITY_RE.search(li_content)
            if not locality_match:
                continue
            locality = _clean(locality_match.group(2))
            if not locality:
                continue

            remainder = li_content[locality_match.end():]
            for piece in re.split(r"<br\s*/?>", remainder, flags=re.IGNORECASE):
                piece_text = _clean(piece)
                if not piece_text:
                    continue
                seg = SEGMENT_RE.search(piece_text)
                if not seg:
                    continue
                results.append({
                    "outage_date": outage_date.isoformat(),
                    "locality": locality,
                    "time_start": f"{int(seg.group(1)):02d}:00",
                    "time_end": f"{int(seg.group(2)):02d}:00",
                    "streets": _extract_streets(seg.group(3)),
                    "raw_line": piece_text,
                })

    return results


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
    CREATE TABLE IF NOT EXISTS outages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      scraped_at INTEGER NOT NULL,
      outage_date TEXT NOT NULL,
      locality TEXT NOT NULL,
      time_start TEXT NOT NULL,
      time_end TEXT NOT NULL,
      streets TEXT NOT NULL,
      raw_line TEXT NOT NULL
    )
    """)
    # Registro de CADA corrida (haya encontrado cortes o no), para poder
    # distinguir "no hay nada programado" de "el scraper dejó de
    # funcionar" -- antes eran indistinguibles porque outages.scraped_at
    # solo se graba si rows no está vacío (ver bug real: la página cambió
    # de formato el 2026-10-30 y tardamos 3 días en notarlo porque no
    # había ninguna señal de que el scraper hubiera dejado de encontrar
    # cosas, nada se veía distinto desde afuera).
    con.execute("""
    CREATE TABLE IF NOT EXISTS scrape_log (
      id               INTEGER PRIMARY KEY AUTOINCREMENT,
      ts               INTEGER NOT NULL,
      ok               INTEGER NOT NULL,
      day_blocks_found INTEGER NOT NULL,
      rows_found       INTEGER NOT NULL,
      error            TEXT
    )
    """)
    con.commit()
    con.close()


def _log_scrape_attempt(ok: bool, day_blocks_found: int, rows_found: int, error: Optional[str]) -> None:
    con = sqlite3.connect(DB_PATH)
    con.execute(
        "INSERT INTO scrape_log (ts, ok, day_blocks_found, rows_found, error) VALUES (?,?,?,?,?)",
        (int(time.time()), int(ok), day_blocks_found, rows_found, error),
    )
    con.commit()
    con.close()


def get_scraper_health() -> Dict:
    """Diagnóstico sin tener que mirar journalctl a mano. 'looks_broken'
    se prende cuando, en las últimas corridas, la página siguió
    publicando encabezados de día (DAY_BLOCK_RE matcheó) pero no se pudo
    extraer ningún corte de adentro -- la señal fuerte de que cambió el
    formato interno, a diferencia de un día real sin nada programado
    (ahí la página ni pone el encabezado, day_blocks_found da 0)."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    last = con.execute("SELECT * FROM scrape_log ORDER BY ts DESC LIMIT 1").fetchone()
    recent = con.execute(
        "SELECT day_blocks_found, rows_found FROM scrape_log ORDER BY ts DESC LIMIT 5"
    ).fetchall()
    con.close()

    if last is None:
        return {
            "last_run_ts": None, "last_run_ok": None, "last_day_blocks_found": None,
            "last_rows_found": None, "last_error": None, "looks_broken": False,
        }

    looks_broken = len(recent) >= 3 and all(
        r["day_blocks_found"] > 0 and r["rows_found"] == 0 for r in recent
    )
    return {
        "last_run_ts": last["ts"],
        "last_run_ok": bool(last["ok"]),
        "last_day_blocks_found": last["day_blocks_found"],
        "last_rows_found": last["rows_found"],
        "last_error": last["error"],
        "looks_broken": looks_broken,
    }


def store_scrape(rows: List[Dict], scraped_at: Optional[int] = None) -> int:
    """Guarda todas las filas de UNA corrida del scraper, todas con el
    mismo scraped_at. Devuelve cuantas se insertaron."""
    scraped_at = scraped_at if scraped_at is not None else int(time.time())
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executemany(
        """INSERT INTO outages
           (scraped_at, outage_date, locality, time_start, time_end, streets, raw_line)
           VALUES (?,?,?,?,?,?,?)""",
        [
            (scraped_at, r["outage_date"], r["locality"], r["time_start"],
             r["time_end"], r["streets"], r["raw_line"])
            for r in rows
        ],
    )
    con.commit()
    inserted = cur.rowcount
    con.close()
    return inserted


def get_latest_scrape_ts() -> Optional[int]:
    """Timestamp (unix) de la corrida mas reciente, o None si el scraper
    todavia no corrio nunca."""
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT MAX(scraped_at) FROM outages")
    latest = cur.fetchone()[0]
    con.close()
    return latest


def get_latest_outages() -> List[Dict]:
    """Las filas de la corrida mas reciente (para que lo use, mas
    adelante, lo que sea que se decida para publicarlo)."""
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("SELECT MAX(scraped_at) FROM outages")
    latest = cur.fetchone()[0]
    if latest is None:
        con.close()
        return []
    cur.execute(
        """SELECT outage_date, locality, time_start, time_end, streets, raw_line
           FROM outages WHERE scraped_at = ? ORDER BY outage_date, locality, time_start""",
        (latest,),
    )
    rows = cur.fetchall()
    con.close()
    return [
        {
            "outage_date": r[0], "locality": r[1], "time_start": r[2],
            "time_end": r[3], "streets": r[4], "raw_line": r[5],
        }
        for r in rows
    ]


def run() -> int:
    """Descarga, parsea y guarda una corrida. Devuelve la cantidad de
    cortes encontrados (0 es valido: puede no haber nada programado).

    Graba SIEMPRE un registro en scrape_log, haya o no cortes -- es lo que
    permite a get_scraper_health() distinguir "no hay nada programado" de
    "el scraper dejó de funcionar" sin tener que mirar logs a mano."""
    try:
        html_text = fetch_html()
        day_blocks_found = len(DAY_BLOCK_RE.findall(html_text))
        rows = parse(html_text)
    except Exception as exc:
        logger.exception("EDES scraper: fetch/parse falló")
        _log_scrape_attempt(ok=False, day_blocks_found=0, rows_found=0, error=str(exc))
        raise

    if day_blocks_found > 0 and not rows:
        logger.error(
            "EDES scraper: la página tiene %d encabezado(s) de día pero no se "
            "pudo extraer ningún corte de adentro -- probablemente cambió el "
            "formato del <li> otra vez. Bajar la página a mano y comparar "
            "contra LOCALITY_RE/SEGMENT_RE en edes_scraper.py.",
            day_blocks_found,
        )
    elif not rows:
        logger.info("EDES scraper: sin cortes programados (página sin encabezados de día).")

    store_scrape(rows)
    _log_scrape_attempt(ok=True, day_blocks_found=day_blocks_found, rows_found=len(rows), error=None)
    logger.info(
        "EDES scraper: %d corte(s) guardado(s), %d encabezado(s) de día en la página.",
        len(rows), day_blocks_found,
    )
    return len(rows)
