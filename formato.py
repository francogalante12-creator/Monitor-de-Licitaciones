"""Formateo de numeros, montos y fechas al uso argentino."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

# El mercado local opera en horario de Buenos Aires y la API publica todo en
# UTC. Se usa un offset fijo: Argentina no aplica horario de verano desde
# 2009, asi que no hace falta arrastrar zoneinfo (que en Windows exige el
# paquete tzdata y es una fuente clasica de fallas en produccion).
TZ_AR = timezone(timedelta(hours=-3), "ART")

# Monedas observadas en el campo monedaMonto de la API (10/09/2026):
# "ARS PESOS", "USD", "UVA", "USD LINK". Las dos ultimas son unidades de
# cuenta: USD LINK liquida en pesos al tipo de cambio, y UVA ajusta por CER.
SIMBOLOS = {
    "ARS PESOS": "$",
    "ARS": "$",
    "PESOS": "$",
    "USD": "US$",
    "USD MEP": "US$",
    "USD LINK": "US$ link",
    "DOLARES": "US$",
    "UVA": "UVA",
}


def formato_ar(numero: Any, decimales: int = 2) -> str:
    """1234567.89 -> '1.234.567,89' (punto de miles, coma decimal)."""
    if numero is None or numero == "":
        return "-"
    try:
        valor = float(numero)
    except (TypeError, ValueError):
        return str(numero)
    entero = f"{valor:,.{decimales}f}"
    # Se usa un marcador intermedio porque el swap directo pisa lo ya cambiado.
    return entero.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def monto_corto(valor: Any, moneda: str = "") -> str:
    """Montos legibles de un vistazo: '$ 5,34 MM', 'US$ 30,0 M'.

    MM = millones, M = miles de millones (uso local: 'palo' y 'luca larga'
    no entran en un mail, pero 5.343.227.882 tampoco se lee).
    """
    if valor is None or valor == "":
        return "-"
    try:
        v = float(valor)
    except (TypeError, ValueError):
        return str(valor)
    if v == 0:
        return "-"

    simbolo = SIMBOLOS.get(str(moneda).upper().strip(), "")
    signo = f"{simbolo} " if simbolo else ""

    for umbral, sufijo in ((1e9, " MM"), (1e6, " M"), (1e3, " k")):
        if abs(v) >= umbral:
            return f"{signo}{formato_ar(v / umbral, 1)}{sufijo}"
    return f"{signo}{formato_ar(v, 0)}"


def monto_largo(valor: Any, moneda: str = "") -> str:
    if valor is None or valor == "":
        return "-"
    simbolo = SIMBOLOS.get(str(moneda).upper().strip(), "")
    signo = f"{simbolo} " if simbolo else ""
    return f"{signo}{formato_ar(valor, 0)}"


def a_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ_AR)


def fecha_hora(dt: datetime | None) -> str:
    local = a_local(dt)
    return local.strftime("%d/%m/%Y %H:%M") if local else "-"


def solo_fecha(dt: datetime | None) -> str:
    local = a_local(dt)
    return local.strftime("%d/%m/%Y") if local else "-"


def iso_fecha(dt: datetime | None) -> str:
    """Para la planilla: ISO, que ordena bien y no depende del locale."""
    local = a_local(dt)
    return local.strftime("%Y-%m-%d") if local else ""


def valor_para_mostrar(valor: Any) -> str:
    if isinstance(valor, datetime):
        return fecha_hora(valor)
    if isinstance(valor, float):
        return formato_ar(valor, 0)
    if valor in (None, ""):
        return "-"
    return str(valor)
