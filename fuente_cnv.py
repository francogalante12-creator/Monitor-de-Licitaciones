"""
Cliente de los Hechos Relevantes de la CNV (Autopista de la Informacion
Financiera).

Para que sirve
--------------
A3 muestra la licitacion el dia que abre. La CNV publica el aviso antes -
tipicamente uno a tres dias habiles -, asi que esta fuente es la que da
tiempo a reaccionar. A cambio, no trae monto, tasa ni fechas de licitacion:
es un feed documental, no una grilla de mercado.

Por eso los registros de aca salen con estado "Anunciada" y los campos
numericos vacios. Cuando la ON efectivamente sale a licitar, A3 la emite
como un registro propio: van a convivir dos entradas de la misma emision,
una anticipando y otra con los datos duros. Es a proposito.

De donde sale
-------------
    https://www.cnv.gov.ar/SitioWeb/HechosRelevantes

HTML renderizado en el servidor, sin API. Cinco tablas (Empresas, FCI,
Agentes y Mercados, Fideicomisos Financieros, Agentes PIC) con las columnas
Fecha / Entidad / Descripcion / Documento, ordenadas de mas nuevo a mas
viejo, y un enlace por fila a aif2.cnv.gov.ar/Presentations/publicview/<GUID>
con la presentacion completa.

Notas de implementacion
-----------------------
1. No hay paginacion ni filtro de fechas en la URL: la pagina muestra las
   ultimas publicaciones y nada mas. Igual que con A3, el historico largo lo
   guarda el bot.
2. El parseo NO se apoya en ids ni clases de CSS, que es lo primero que
   cambia cuando el organismo retoca el sitio. Recorre las tablas, lee los
   encabezados y mapea las columnas por nombre. Si no encuentra ninguna
   tabla reconocible, falla con un mensaje claro en vez de devolver una lista
   vacia -- un feed vacio y un parser roto se ven igual desde afuera, y
   confundirlos significa dejar de avisar sin que nadie se entere.
3. El feed trae de todo (designaciones de gerentes, actas, garantias). Solo
   se emiten las publicaciones que parecen de emision de deuda, segun
   PATRONES_EMISION.

Para verificar que anda en una red que llegue a la CNV:

    python fuente_cnv.py --diagnostico
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any

import red

log = logging.getLogger(__name__)

URL_HECHOS = "https://www.cnv.gov.ar/SitioWeb/HechosRelevantes"

HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Publicaciones que interesan: emision o colocacion de deuda. Se aplica sobre
# la descripcion, que viene toda en mayusculas y sin acentos consistentes, asi
# que se compara sobre texto normalizado.
PATRONES_EMISION = (
    r"aviso de suscripcion",
    r"aviso de resultado",
    r"suplemento de prospecto",
    r"obligacion(?:es)? negociable",
    r"\bon\b.*(?:clase|serie)",
    r"valores de corto plazo|\bvcp\b",
    r"emision de (?:deuda|obligaciones)",
    r"colocacion de (?:obligaciones|valores)",
    r"programa global de (?:obligaciones|valores)",
    r"valores fiduciarios",
    r"fideicomiso financiero",
)

# Publicaciones que mencionan ON pero NO son una emision nueva. Sin esto, cada
# recompra o rescate de YPF entraria como si fuera una colocacion.
# Verificados contra el feed real del 14/09/2026: son los titulos que mencionan
# ON pero no son una colocacion nueva.
PATRONES_EXCLUIR = (
    r"ofertas? de compra",          # plural incluido: el sitio usa las dos formas
    r"\brescate\b",                 # "EVENTUAL RESCATE Y PRECANCELACION", no solo
                                    # "rescate anticipado": se colaba como emision
    r"precancelacion",
    r"recompras? de",               # "INFORMA RECOMPRAS DE ONS"
    r"\brecompra\b",
    r"\bcanje\b",
    r"pago de (?:renta|amortizacion|servicios|intereses|dividendos)",
    r"avisos? de pago",
    r"cancelacion (?:total|anticipada)",
    r"\bcedears?\b",                # los CEDEAR de bancos llenan el feed y no son ON
    r"anuncio de dividendos",
)

MESES = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "set": 9, "oct": 10, "nov": 11, "dic": 12,
}

# La CNV publica en hora de Buenos Aires. Offset fijo: Argentina no aplica
# horario de verano desde 2009.
TZ_AR = timezone(timedelta(hours=-3), "ART")


class FuenteNoDisponible(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Parseo del HTML
# ---------------------------------------------------------------------------

class _TablaParser(HTMLParser):
    """Extrae todas las tablas como listas de filas de celdas.

    Cada celda queda como {"texto": str, "href": str|None}. Se usa la stdlib a
    proposito: una dependencia menos que instalar en la maquina donde esto
    termine corriendo.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tablas: list[list[list[dict]]] = []
        self._pila_tablas: list[list[list[dict]]] = []
        self._fila: list[dict] | None = None
        self._celda: dict | None = None
        self._profundidad_celda = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        at = dict(attrs)
        if tag == "table":
            self._pila_tablas.append([])
        elif tag == "tr" and self._pila_tablas:
            self._fila = []
        elif tag in ("td", "th") and self._fila is not None:
            self._celda = {"texto": "", "href": None}
            self._profundidad_celda = 1
        elif tag == "a" and self._celda is not None and at.get("href"):
            if not self._celda["href"]:
                self._celda["href"] = at["href"]
        elif tag in ("br", "p", "div") and self._celda is not None:
            self._celda["texto"] += " "

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._pila_tablas:
            self.tablas.append(self._pila_tablas.pop())
        elif tag == "tr" and self._fila is not None:
            if self._fila and self._pila_tablas:
                self._pila_tablas[-1].append(self._fila)
            self._fila = None
        elif tag in ("td", "th") and self._celda is not None:
            self._celda["texto"] = re.sub(r"\s+", " ", self._celda["texto"]).strip()
            if self._fila is not None:
                self._fila.append(self._celda)
            self._celda = None

    def handle_data(self, data: str) -> None:
        if self._celda is not None:
            self._celda["texto"] += data


def _sin_acentos(texto: str) -> str:
    import unicodedata
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def normalizar(texto: Any) -> str:
    return re.sub(r"\s+", " ", _sin_acentos(str(texto or "")).lower()).strip()


def parse_fecha_cnv(texto: str) -> datetime | None:
    """'9 sep. 2026 22:53' -> datetime UTC.

    Se aceptan variantes con y sin punto en el mes, con y sin hora, y el
    formato numerico 09/09/2026 por si el sitio cambia de estilo.
    """
    t = normalizar(texto).replace(".", "")

    m = re.search(r"(\d{1,2})\s+([a-z]{3,10})\s+(\d{4})(?:\s+(\d{1,2}):(\d{2}))?", t)
    if m:
        dia, mes_txt, anio = int(m.group(1)), m.group(2)[:3], int(m.group(3))
        mes = MESES.get(mes_txt)
        if mes:
            hora = int(m.group(4)) if m.group(4) else 0
            minuto = int(m.group(5)) if m.group(5) else 0
            try:
                local = datetime(anio, mes, dia, hora, minuto, tzinfo=TZ_AR)
            except ValueError:
                return None
            return local.astimezone(timezone.utc)

    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})(?:\s+(\d{1,2}):(\d{2}))?", t)
    if m:
        try:
            local = datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)),
                             int(m.group(4) or 0), int(m.group(5) or 0), tzinfo=TZ_AR)
        except ValueError:
            return None
        return local.astimezone(timezone.utc)

    return None


def _indice_columnas(filas: list[list[dict]]) -> dict[str, int] | None:
    """Encuentra la fila de encabezados y mapea nombre logico -> indice.

    Devuelve None si la tabla no parece la de hechos relevantes.
    """
    # Ojo con el matcheo por substring: "id" esta adentro de "entidad", asi que
    # la columna Documento terminaba apuntando a Entidad. Se compara por
    # palabra completa y no se reusa una columna ya asignada.
    # Las cinco tablas del feed no usan los mismos encabezados. Verificado
    # contra el sitio real el 14/09/2026:
    #   Empresas / FCI / Agentes  FECHA | ENTIDAD | DESCRIPCION | DOCUMENTO
    #   Fideicomisos              FECHA | FIDEICOMISO | FIDUCIARIO | DESCRIPCION
    #   PIC                       FECHA PRESENTACION | CATEGORIA | RAZON SOCIAL | DESCRIPCION
    # Ojo con las dos ultimas: la descripcion esta en la columna 3, no en la 2,
    # y la columna 2 es otra cosa (el fiduciario, la razon social). Mapear por
    # posicion da vuelta los campos sin que se note.
    alias = {
        "fecha": ("fecha",),
        "entidad": ("entidad", "denominacion", "emisora", "empresa", "sociedad",
                    "razon social", "fideicomiso", "fiduciante"),
        "descripcion": ("descripcion", "asunto", "detalle", "titulo"),
        "documento": ("documento", "nro", "numero", "num"),
    }

    for fila in filas[:3]:                       # el encabezado esta arriba
        textos = [normalizar(c["texto"]) for c in fila]
        if not any(textos):
            continue

        mapa: dict[str, int] = {}
        usados: set[int] = set()

        # Primera pasada: coincidencia exacta del encabezado.
        for logico, opciones in alias.items():
            for i, t in enumerate(textos):
                if i not in usados and t in opciones:
                    mapa[logico] = i
                    usados.add(i)
                    break

        # Segunda pasada: el encabezado contiene la palabra ("nro documento").
        for logico, opciones in alias.items():
            if logico in mapa:
                continue
            for i, t in enumerate(textos):
                if i in usados:
                    continue
                if any(re.search(rf"\b{re.escape(o)}\b", t) for o in opciones):
                    mapa[logico] = i
                    usados.add(i)
                    break

        # Fecha y entidad son imprescindibles; sin ellas no es esta tabla.
        if "fecha" in mapa and "entidad" in mapa:
            return mapa

    # Fallback por forma. La CNV parte el feed en cinco tablas (Empresas, FCI,
    # Agentes, Fideicomisos, PIC) y no todas traen fila de encabezados: dos de
    # las cinco quedaban afuera, 152 filas sin leer. Si la mayoria de las filas
    # arranca con algo que parsea como fecha, es una tabla de datos y se mapea
    # por posicion, que es el orden que usa el sitio en las cinco.
    return _indice_por_forma(filas)


def _indice_por_forma(filas: list[list[dict]]) -> dict[str, int] | None:
    candidatas = [f for f in filas if len(f) >= 3]
    if len(candidatas) < 3:
        return None

    con_fecha = sum(1 for f in candidatas if parse_fecha_cnv(f[0]["texto"]))
    if con_fecha < max(3, len(candidatas) * 0.6):
        return None

    # La descripcion no siempre esta en la columna 2: en la tabla de
    # fideicomisos la 2 es el fiduciario y la descripcion esta en la 3. Sin
    # encabezados hay que decidirlo por el contenido, y lo que las distingue es
    # el largo: los nombres de entidad son cortos y repetidos, la descripcion
    # es una frase.
    ancho = min(len(f) for f in candidatas)
    if ancho < 3:
        return None
    largos = {
        i: sorted(len(f[i]["texto"]) for f in candidatas)[len(candidatas) // 2]
        for i in range(2, ancho)
    }
    col_desc = max(largos, key=lambda i: largos[i])
    mapa = {"fecha": 0, "entidad": 1, "descripcion": col_desc}

    # Si alguna columna es un numero en casi todas las filas, es el numero de
    # documento, que es lo que le da al registro un id estable entre corridas.
    for i in range(2, ancho):
        if i == col_desc:
            continue
        numericas = sum(1 for f in candidatas
                        if re.fullmatch(r"\d{4,}", f[i]["texto"].strip()))
        if numericas >= len(candidatas) * 0.8:
            mapa["documento"] = i
            break

    log.info("Tabla sin encabezados reconocida por su forma (%d filas con fecha "
             "de %d; descripcion en la columna %d).",
             con_fecha, len(candidatas), col_desc)
    return mapa


def parsear_hechos(html: str) -> list[dict]:
    """Saca las publicaciones de todas las tablas reconocibles del HTML."""
    parser = _TablaParser()
    parser.feed(html)

    salida: list[dict] = []
    tablas_reconocidas = 0

    for filas in parser.tablas:
        if len(filas) < 2:
            continue
        mapa = _indice_columnas(filas)
        if not mapa:
            continue
        tablas_reconocidas += 1

        ancho_min = max(mapa.values())
        for fila in filas:
            if len(fila) <= ancho_min:
                continue
            fecha_txt = fila[mapa["fecha"]]["texto"]
            fecha = parse_fecha_cnv(fecha_txt)
            if fecha is None:               # es el encabezado, o una fila rara
                continue

            entidad = fila[mapa["entidad"]]["texto"]
            desc = fila[mapa["descripcion"]]["texto"] if "descripcion" in mapa else ""
            doc = fila[mapa["documento"]]["texto"] if "documento" in mapa else ""

            enlace = next((c["href"] for c in fila if c.get("href")), None)

            if not entidad and not desc:
                continue

            salida.append({
                "fecha": fecha,
                "entidad": entidad,
                "descripcion": desc,
                "documento": doc.strip(),
                "enlace": enlace,
            })

    if tablas_reconocidas == 0:
        raise FuenteNoDisponible(
            "No encontre ninguna tabla de hechos relevantes en el HTML de la "
            "CNV. Probablemente cambio la estructura de la pagina. Correr "
            "'python fuente_cnv.py --diagnostico' para ver que llego."
        )

    return salida


# ---------------------------------------------------------------------------
# Filtro y traduccion al modelo del bot
# ---------------------------------------------------------------------------

def es_de_emision(desc: str, entidad: str = "") -> bool:
    texto = normalizar(f"{desc} {entidad}")
    if any(re.search(p, texto) for p in PATRONES_EXCLUIR):
        return False

    # Lo que decide es la descripcion, no el nombre de la entidad. En la tabla
    # de fideicomisos TODAS las entidades se llaman "X FIDEICOMISO FINANCIERO",
    # asi que matchear sobre el nombre daba por emision las 51 filas, fueran lo
    # que fueran. La entidad solo cuenta cuando no hay descripcion que mirar.
    base = normalizar(desc) or normalizar(entidad)
    return any(re.search(p, base) for p in PATRONES_EMISION)


def _id_estable(hecho: dict) -> str:
    """El numero de documento de la CNV es unico y estable. Si faltara, se
    cae a una clave derivada de la fecha y la entidad."""
    doc = re.sub(r"\D", "", hecho.get("documento") or "")
    if doc:
        return doc
    base = normalizar(f"{hecho['fecha']:%Y%m%d%H%M}-{hecho['entidad']}")
    return re.sub(r"[^a-z0-9]+", "-", base)[:60]


def a_registro(hecho: dict) -> dict:
    """Traduce una publicacion de la CNV a un registro crudo con la misma
    forma que los de A3, para que el resto del bot no note la diferencia."""
    desc = hecho["descripcion"]
    titulo = desc if desc else hecho["entidad"]

    return {
        "id": _id_estable(hecho),
        "titulo": titulo[:300],
        "emisor": hecho["entidad"],
        # El tipo se deja explicito para que categorizar() lo reconozca: si la
        # descripcion nombra un fideicomiso va a FF, si nombra una ON va a ON.
        "tipo": _tipo_desde_texto(desc),
        "moneda": "", "monedaMonto": "",
        "montoaLicitar": None, "monto_Adjudicado": None,
        "valor_Corte": "", "variableLicitar": "", "sistema_Adjudicacion": "",
        "ampliableHasta": "", "colocador": "", "liquidador": "", "rueda": "",
        "modalidad": "", "industria": "", "duration": "", "plazoEspecie": "",
        "observaciones": desc,
        "comentario": "",
        "existeArchivo": 1 if hecho.get("enlace") else 0,
        "archivos": [hecho["enlace"]] if hecho.get("enlace") else [],
        "fechaInicio": hecho["fecha"].isoformat().replace("+00:00", "Z"),
        "fechaFin": None,
        "fechaLiquidacion": None,
        "fechaVencimiento": "0001-01-01T00:00:00",
        "fechaVencimientoEspecie": "0001-01-01T00:00:00",
        "fechaModificacion": hecho["fecha"].isoformat().replace("+00:00", "Z"),
        "estado": "Anunciada",
        "_estado_cod": "N",
        "_estado_real": "Anunciada",
        "_fuente": "cnv",
        "_enlace": hecho.get("enlace"),
        "_consultado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _tipo_desde_texto(desc: str) -> str:
    """Arma un 'tipo' con el vocabulario que ya entiende modelo.categorizar()."""
    t = normalizar(desc)
    if re.search(r"fideicomiso|valores fiduciarios|\bff\b", t):
        return "CNV - FF"
    if re.search(r"\bvs/svs\b|sostenib|vinculad[oa] a la sostenibilidad", t):
        return "CNV - ON VS/SVS"
    if re.search(r"pyme|bajo impacto|mediano impacto|\bvcp\b", t):
        return "CNV - ON/VCP PYME"
    return "CNV - ON"


# ---------------------------------------------------------------------------
# Descarga
# ---------------------------------------------------------------------------

def _bajar(url: str) -> str:
    """Baja el HTML. Los reintentos y el diagnostico de red viven en red.py."""
    try:
        return red.get(url, headers=HEADERS, intentos=3)
    except red.ErrorDeRed as exc:
        raise FuenteNoDisponible(str(exc)) from exc


def traer_licitaciones(**_) -> list[dict]:
    """Interfaz que espera el registro de fuentes: devuelve registros crudos."""
    html = _bajar(URL_HECHOS)
    hechos = parsear_hechos(html)

    relevantes = [h for h in hechos if es_de_emision(h["descripcion"], h["entidad"])]
    log.info(
        "CNV: %d publicaciones leidas, %d de emision de deuda.",
        len(hechos), len(relevantes),
    )
    return [a_registro(h) for h in relevantes]


# ---------------------------------------------------------------------------
# Diagnostico
# ---------------------------------------------------------------------------

def diagnostico() -> int:
    """Corre la fuente mostrando cada paso, para validarla contra el sitio real."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print(f"0. Red: {red.proxy_visible()}")
    print(f"1. Bajando {URL_HECHOS} ...")
    try:
        html = _bajar(URL_HECHOS)
    except FuenteNoDisponible as exc:
        print(f"\n   FALLO: {exc}\n")
        print("   Chequeo de conectividad para ubicar donde se corta:")
        for linea in red.chequear_conectividad(URL_HECHOS):
            print(f"     {linea}")
        return 2
    print(f"   OK, {len(html):,} caracteres.")

    parser = _TablaParser()
    parser.feed(html)
    print(f"2. Tablas encontradas en el HTML: {len(parser.tablas)}")
    for i, filas in enumerate(parser.tablas):
        mapa = _indice_columnas(filas) if len(filas) >= 2 else None
        marca = "reconocida" if mapa else "IGNORADA"
        print(f"   tabla {i}: {len(filas)} filas, {marca}"
              + (f", columnas {mapa}" if mapa else ""))
        # Los encabezados de cada tabla, para entender por que una queda afuera.
        if filas:
            cabecera = [c["texto"][:22] for c in filas[0][:5]]
            print(f"      1a fila: {cabecera}")
            if not mapa and len(filas) > 1:
                print(f"      2a fila: {[c['texto'][:22] for c in filas[1][:5]]}")

    try:
        hechos = parsear_hechos(html)
    except FuenteNoDisponible as exc:
        print(f"3. FALLO: {exc}")
        return 1
    print(f"3. Publicaciones parseadas: {len(hechos)}")
    for h in hechos[:5]:
        print(f"   {h['fecha']:%d/%m/%Y %H:%M}  {h['entidad'][:34]:34s}  "
              f"{h['descripcion'][:56]}")

    relevantes = [h for h in hechos if es_de_emision(h["descripcion"], h["entidad"])]
    descartadas = [h for h in hechos if h not in relevantes]

    print(f"4. PASARON el filtro de emision: {len(relevantes)} de {len(hechos)}")
    for h in relevantes[:15]:
        print(f"   {h['fecha']:%d/%m}  {h['entidad'][:30]:30s}  "
              f"{h['descripcion'][:70]}")

    # Esto es lo que hay que mirar para calibrar: si entre las descartadas
    # aparecen avisos de suscripcion o suplementos de prospecto, el filtro
    # esta dejando pasar de largo lo que importa.
    print(f"\n5. NO pasaron el filtro: {len(descartadas)}")
    print("   (si aca abajo ves avisos de suscripcion, suplementos de prospecto")
    print("    o emisiones de ON, el filtro esta quedando corto)")
    for h in descartadas[:40]:
        print(f"   {h['fecha']:%d/%m}  {h['entidad'][:28]:28s}  "
              f"{h['descripcion'][:72]}")
    if len(descartadas) > 40:
        print(f"   ... y {len(descartadas) - 40} mas")

    print(f"\n6. Registros para el bot: {len(traer_licitaciones())}")
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Fuente CNV / AIF")
    p.add_argument("--diagnostico", action="store_true",
                   help="corre la fuente paso a paso contra el sitio real")
    args = p.parse_args()
    sys.exit(diagnostico() if args.diagnostico else p.print_help() or 0)
