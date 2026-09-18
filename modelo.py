"""
Normalizacion de los registros crudos de A3 a un modelo estable.

La API devuelve 30 campos con nombres inconsistentes (monto_Adjudicado,
montoaLicitar, sistema_Adjudicacion...). Este modulo los traduce a un
registro limpio, tipado y con la categoria derivada, que es lo que
consume el resto del bot.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

# --------------------------------------------------------------------------
# Categorias
# --------------------------------------------------------------------------
# El campo "tipo" de la API es texto libre y viene con dos guiones distintos:
# "Privada - FF" usa guion comun (U+002D) y "Privada – FF MEDIANO IMPACTO"
# usa raya (U+2013). Tambien varian mayusculas y espacios dobles. Por eso
# la categorizacion trabaja sobre una version normalizada del texto.

CAT_ON_CORP = "ON Corporativa"
CAT_ON_PYME = "ON PyME CNV"
CAT_ON_SOST = "ON Sostenible (VS/SVS)"
CAT_FF = "Fideicomiso Financiero"
CAT_PUBLICA = "Deuda Publica"
CAT_OTRO = "Otro"

# Orden de evaluacion: de lo mas especifico a lo mas general.
_REGLAS_CATEGORIA: list[tuple[str, str]] = [
    (r"\bon\b.*\bvs/svs\b|\bsvs\b", CAT_ON_SOST),
    (r"\bon/vcp\b|\bon pyme\b|\bbajo impacto\b|\bmediano impacto\b", CAT_ON_PYME),
    (r"\bff\b|fideicomiso", CAT_FF),
    (r"\bon\b", CAT_ON_CORP),
    (r"publica|p[uú]blica", CAT_PUBLICA),
]


# --------------------------------------------------------------------------
# Moneda
# --------------------------------------------------------------------------
# A3 trae la moneda en dos campos ("ARS PESOS", "USD", "UVA"). La CNV no trae
# ninguno: sus avisos son texto, y a veces el titulo dice la moneda y a veces
# no. De ahi que exista la clase "" (desconocida), que no es lo mismo que
# "pesos": es "todavia no se sabe". El bot la trata aparte a proposito.
MONEDA_USD = "USD"              # hard dollar: se suscribe y se paga en dolares
MONEDA_USD_LINKED = "USD linked"  # atada al dolar oficial, se paga en pesos
MONEDA_ARS = "ARS"
MONEDA_UVA = "UVA"
MONEDA_DESCONOCIDA = ""

MONEDAS_CONOCIDAS = (MONEDA_USD, MONEDA_USD_LINKED, MONEDA_ARS, MONEDA_UVA)

# El orden importa: "dolar linked" tiene que ganarle a "dolar", y "UVA" a
# "pesos" (una UVA se integra en pesos y muchas veces el texto dice las dos).
_REGLAS_MONEDA: list[tuple[str, str]] = [
    (r"linked|vinculad[oa] al d[oó]lar|dolar[- ]?linked", MONEDA_USD_LINKED),
    (r"\buva\b|unidad(?:es)? de valor adquisitivo", MONEDA_UVA),
    (r"\busd\b|u\$s|\bd[oó]lar(?:es)?\b|hard dollar|\bdai\b", MONEDA_USD),
    (r"\bars\b|\bpesos?\b|moneda nacional", MONEDA_ARS),
]


def clase_moneda(*textos: Any) -> str:
    """Clasifica en USD / USD linked / ARS / UVA, o "" si no se puede decir.

    Se le pasan los campos en orden de confianza: primero los de A3, que son
    un codigo, y recien despues el titulo, que es prosa. El primero que
    resuelve gana, para que un titulo que menciona el dolar de pasada no
    contradiga el campo de la licitacion.
    """
    for texto in textos:
        t = normalizar_texto(str(texto or ""))
        if not t:
            continue
        for patron, clase in _REGLAS_MONEDA:
            if re.search(patron, t):
                return clase
    return MONEDA_DESCONOCIDA

# Categorias que son obligaciones negociables, en el sentido que le importa
# a un bot de "licitaciones de ON".
CATEGORIAS_ON = frozenset({CAT_ON_CORP, CAT_ON_PYME, CAT_ON_SOST})

TODAS_LAS_CATEGORIAS = (
    CAT_ON_CORP, CAT_ON_PYME, CAT_ON_SOST, CAT_FF, CAT_PUBLICA, CAT_OTRO,
)


def _sin_acentos(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def normalizar_texto(texto: Any) -> str:
    """Minusculas, sin acentos, guiones unificados, espacios colapsados."""
    if texto is None:
        return ""
    t = str(texto)
    # Unifica raya, guion largo y guion comun.
    t = t.replace("–", "-").replace("—", "-").replace("−", "-")
    t = _sin_acentos(t).lower()
    return re.sub(r"\s+", " ", t).strip()


def categorizar(tipo: Any, titulo: Any = "") -> str:
    """Deriva la categoria del campo tipo, con el titulo como desempate."""
    base = normalizar_texto(tipo)
    for patron, categoria in _REGLAS_CATEGORIA:
        if re.search(patron, base):
            # Un FF de mediano impacto matchea "mediano impacto" antes que
            # "ff", asi que se corrige aca: si el tipo nombra un FF, es FF.
            if categoria == CAT_ON_PYME and re.search(r"\bff\b|fideicomiso", base):
                return CAT_FF
            return categoria

    titulo_norm = normalizar_texto(titulo)
    for patron, categoria in _REGLAS_CATEGORIA:
        if re.search(patron, titulo_norm):
            return categoria
    return CAT_OTRO


# --------------------------------------------------------------------------
# Parseo de campos
# --------------------------------------------------------------------------

# La API usa 0001-01-01 como "sin fecha".
_FECHA_NULA = "0001-01-01"


def parse_fecha(valor: Any) -> datetime | None:
    """Parsea las fechas ISO de la API. Devuelve None para las nulas.

    Ojo: la API mezcla dos formatos. Los timestamps reales vienen con Z
    ("2026-09-10T13:00:00Z") y los nulos vienen sin zona
    ("0001-01-01T00:00:00"). Siempre se devuelve un datetime aware en UTC.
    """
    if not valor or not isinstance(valor, str):
        return None
    if valor.startswith(_FECHA_NULA):
        return None
    try:
        dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_monto(valor: Any) -> float | None:
    """Los montos vienen como numero, pero algunos campos llegan como texto
    con separadores de miles. Se aceptan las dos formas."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float)):
        return float(valor) or None
    texto = str(valor).strip()
    if not texto or texto == "-":
        return None
    # Formato argentino: punto de miles, coma decimal.
    texto = re.sub(r"[^\d,.\-]", "", texto)
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto) or None
    except ValueError:
        return None


def limpiar(valor: Any) -> str:
    """Los strings de la API vienen con espacios de sobra y dobles."""
    if valor is None:
        return ""
    return re.sub(r"\s+", " ", str(valor)).strip()


def extraer_tasa(valor_corte: Any) -> float | None:
    """Saca el numero de la tasa/margen de corte para poder graficarlo.

    El campo valor_Corte es texto libre y viene en formas muy distintas:
        "TASA DE CORTE: 6.00%"
        "MARGEN DE CORTE: -0.10%"
        "MARGEN DE CORTE VDFA: % / MARGEN DE CORTE VDFB: % / PRECIO..."
        "Ninguno"
    Se toma el primer porcentaje con numero. Si hay varios tramos (tipico de
    un FF con VDFA/VDFB/CP) se toma el primero, que es el tramo senior.
    Devuelve None si no hay ningun numero, que es el caso mas comun en las
    licitaciones que todavia no cerraron.
    """
    if not valor_corte:
        return None
    texto = str(valor_corte).replace(",", ".")
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*%", texto)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def es_desierta(valor_corte: Any) -> bool:
    """Una licitacion desierta no recibio ofertas aceptables.

    La API no tiene un campo para esto: lo escribe en valor_Corte como el
    texto "DESIERTA". Es informacion valiosa -- una ON que queda desierta
    dice algo del apetito del mercado -- y ademas hay que reconocerla como
    resultado, porque si no el bot se queda esperando para siempre un monto
    adjudicado que nunca va a llegar.
    """
    return "desierta" in normalizar_texto(valor_corte)


def normalizar(reg: dict) -> dict:
    """Convierte un registro crudo de la API al modelo del bot."""
    titulo = limpiar(reg.get("titulo"))
    tipo = limpiar(reg.get("tipo"))

    fuente = reg.get("_fuente", "a3")
    ident = reg.get("id")

    registro = {
        "id": ident,
        "fuente": fuente,
        # Clave primaria del bot. El id solo es unico dentro de su fuente: A3
        # usa enteros negativos y la CNV numeros de documento, asi que sin el
        # prefijo dos registros distintos podrian pisarse en el estado.
        "clave": f"{fuente}:{ident}",
        "enlace": reg.get("_enlace") or "",
        "titulo": titulo,
        "emisor": limpiar(reg.get("emisor")),
        "tipo": tipo,
        "categoria": categorizar(tipo, titulo),
        "estado": reg.get("_estado_real", ""),
        "estado_cod": reg.get("_estado_cod", ""),
        "moneda": limpiar(reg.get("moneda")),
        "moneda_monto": limpiar(reg.get("monedaMonto")),
        # Clase normalizada, que es sobre la que filtra el bot. Los avisos de
        # la CNV no traen campo de moneda, asi que para esos el titulo y las
        # observaciones son lo unico que hay; si tampoco dicen nada queda "".
        "moneda_clase": clase_moneda(
            reg.get("monedaMonto"), reg.get("moneda"), titulo,
            reg.get("observaciones"),
        ),
        "monto_licitar": parse_monto(reg.get("montoaLicitar")),
        "monto_adjudicado": parse_monto(reg.get("monto_Adjudicado")),
        "ampliable_hasta": limpiar(reg.get("ampliableHasta")),
        "valor_corte": limpiar(reg.get("valor_Corte")),
        "tasa_corte": extraer_tasa(reg.get("valor_Corte")),
        "desierta": es_desierta(reg.get("valor_Corte")),
        "variable_licitar": limpiar(reg.get("variableLicitar")),
        "sistema_adjudicacion": limpiar(reg.get("sistema_Adjudicacion")),
        "duration": limpiar(reg.get("duration")),
        "plazo_especie": limpiar(reg.get("plazoEspecie")),
        "industria": limpiar(reg.get("industria")),
        "colocador": limpiar(reg.get("colocador")),
        "liquidador": limpiar(reg.get("liquidador")),
        "rueda": limpiar(reg.get("rueda")),
        "modalidad": limpiar(reg.get("modalidad")),
        "observaciones": limpiar(reg.get("observaciones")),
        "fecha_inicio": parse_fecha(reg.get("fechaInicio")),
        "fecha_fin": parse_fecha(reg.get("fechaFin")),
        "fecha_liquidacion": parse_fecha(reg.get("fechaLiquidacion")),
        "fecha_vencimiento": parse_fecha(reg.get("fechaVencimientoEspecie"))
                             or parse_fecha(reg.get("fechaVencimiento")),
        "fecha_modificacion": parse_fecha(reg.get("fechaModificacion")),
        "consultado": reg.get("_consultado", ""),
    }
    # Plazo en anios hasta el vencimiento, contado desde la liquidacion (o
    # desde el inicio de la rueda si no hay). Es lo que necesitan la curva del
    # dashboard y la calculadora de bonos, y conviene que las dos cuenten
    # igual.
    registro["plazo_anios"] = _plazo_anios(registro)
    return registro


def _plazo_anios(reg: dict) -> float | None:
    venc = reg.get("fecha_vencimiento")
    desde = reg.get("fecha_liquidacion") or reg.get("fecha_inicio")
    if not venc or not desde:
        return None
    anios = (venc - desde).days / 365.25
    return round(anios, 4) if anios > 0 else None


def normalizar_todos(registros: list[dict]) -> list[dict]:
    salida = [normalizar(r) for r in registros if r.get("id") is not None]
    # Mas reciente primero: la fecha de inicio es el orden natural del mercado.
    salida.sort(
        key=lambda r: (r["fecha_inicio"] or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )
    return salida
