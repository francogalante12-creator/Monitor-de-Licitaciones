"""
Contexto de mercado para una tasa de corte.

El problema que resuelve: el mail dice "corte 38,50%" y hay que saberse de
memoria cómo viene el mercado para saber si eso es caro o barato. El bot ya
tiene el historico para responderlo solo.

La regla que gobierna todo este archivo es qué cuenta como comparable:

1. **La moneda separa mundos.** Una ON en pesos al 38% y una en dolares al 7%
   no se comparan: son regimenes distintos. Nunca se mezclan.
2. **La categoria importa.** Una ON PyME paga mas que una corporativa por el
   riesgo, no porque el mercado se haya movido. Se compara primero contra su
   propia categoria; si no hay suficientes casos, se abre a todas las ON y se
   aclara en el texto.
3. **La ventana es movil.** Un corte de hace seis meses no dice nada del
   mercado de hoy. Por defecto, 60 dias.
4. **Con pocos casos no se opina.** Debajo de MINIMO_COMPARABLES no se calcula
   percentil: un "percentil 80" sobre tres observaciones es ruido con formato
   de dato.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from modelo import CATEGORIAS_ON

log = logging.getLogger(__name__)

# Debajo de esto no se calcula percentil: no alcanza para decir nada.
MINIMO_COMPARABLES = 5

# Ventana por defecto, en dias corridos.
VENTANA_DIAS = 60


def _moneda_grupo(reg: dict) -> str:
    """Agrupa monedas en los dos regimenes que de verdad se comparan."""
    m = str(reg.get("moneda_monto") or reg.get("moneda") or "").upper()
    if m.startswith("ARS") or m.startswith("PESOS"):
        return "ARS"
    if m.startswith("USD"):
        return "USD"
    return m or "OTRA"


def _percentil(valor: float, muestras: list[float]) -> int:
    """Porcentaje de muestras por debajo del valor, redondeado.

    Se usa la definicion simple (cuantas quedan abajo) porque lo que interesa
    es "cuantas pagaron menos que esta", no una interpolacion estadistica.
    """
    if not muestras:
        return 0
    abajo = sum(1 for m in muestras if m < valor)
    return round(abajo / len(muestras) * 100)


def _mediana(muestras: list[float]) -> float | None:
    if not muestras:
        return None
    s = sorted(muestras)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def comparables(
    reg: dict,
    historico: list[dict],
    ahora: datetime,
    ventana_dias: int = VENTANA_DIAS,
) -> tuple[list[float], str]:
    """Devuelve (tasas comparables, descripcion del universo usado).

    Primero intenta contra la misma categoria; si no junta suficientes, abre
    a todas las ON de la misma moneda. La descripcion se muestra al usuario
    para que sepa contra qué se lo está comparando.
    """
    moneda = _moneda_grupo(reg)
    desde = ahora - timedelta(days=ventana_dias)
    clave_propia = reg.get("clave")

    def juntar(filtro_categoria: bool) -> list[float]:
        salida = []
        for otro in historico:
            if otro.get("clave") == clave_propia:
                continue
            if otro.get("tasa_corte") is None or otro.get("desierta"):
                continue
            if otro.get("categoria") not in CATEGORIAS_ON:
                continue
            if _moneda_grupo(otro) != moneda:
                continue
            if filtro_categoria and otro.get("categoria") != reg.get("categoria"):
                continue
            inicio = otro.get("fecha_inicio")
            if inicio is None or inicio < desde:
                continue
            salida.append(float(otro["tasa_corte"]))
        return salida

    propias = juntar(True)
    if len(propias) >= MINIMO_COMPARABLES:
        return propias, f"{reg.get('categoria')} en {moneda}"

    todas = juntar(False)
    return todas, f"ON en {moneda}"


def calcular(
    reg: dict,
    historico: list[dict],
    ahora: datetime,
    ventana_dias: int = VENTANA_DIAS,
) -> dict | None:
    """Ubica la tasa de una licitacion dentro de su mercado.

    Devuelve None cuando no corresponde opinar: sin tasa, o sin suficientes
    comparables. Preferimos no decir nada antes que decir algo sin sustento.
    """
    tasa = reg.get("tasa_corte")
    if tasa is None or reg.get("desierta"):
        return None
    if reg.get("categoria") not in CATEGORIAS_ON:
        return None

    muestras, universo = comparables(reg, historico, ahora, ventana_dias)
    if len(muestras) < MINIMO_COMPARABLES:
        return None

    tasa = float(tasa)
    mediana = _mediana(muestras)
    pct = _percentil(tasa, muestras)
    dif = tasa - mediana if mediana is not None else 0.0

    return {
        "tasa": tasa,
        "percentil": pct,
        "mediana": mediana,
        "diferencia": dif,
        "n": len(muestras),
        "universo": universo,
        "ventana_dias": ventana_dias,
        "minimo": min(muestras),
        "maximo": max(muestras),
        "etiqueta": _etiqueta(pct),
    }


def _etiqueta(pct: int) -> str:
    """Traduce el percentil a una palabra. Los cortes son deliberadamente
    anchos en el medio: la mayoria de las emisiones no son noticia."""
    if pct >= 90:
        return "muy por encima del mercado"
    if pct >= 70:
        return "por encima del mercado"
    if pct <= 10:
        return "muy por debajo del mercado"
    if pct <= 30:
        return "por debajo del mercado"
    return "en linea con el mercado"


def frase(ctx: dict | None) -> str:
    """Una linea lista para el mail. Cadena vacia si no hay contexto."""
    if not ctx:
        return ""
    from formato import formato_ar

    signo = "+" if ctx["diferencia"] >= 0 else ""
    return (
        f"percentil {ctx['percentil']} de {ctx['universo']} "
        f"({ctx['n']} casos en {ctx['ventana_dias']} dias) &middot; "
        f"mediana {formato_ar(ctx['mediana'], 2)}%, "
        f"{signo}{formato_ar(ctx['diferencia'], 2)} p.p."
    )


def frase_texto(ctx: dict | None) -> str:
    if not ctx:
        return ""
    from formato import formato_ar

    signo = "+" if ctx["diferencia"] >= 0 else ""
    return (
        f"percentil {ctx['percentil']} de {ctx['universo']} "
        f"({ctx['n']} casos en {ctx['ventana_dias']} dias), "
        f"mediana {formato_ar(ctx['mediana'], 2)}%, "
        f"{signo}{formato_ar(ctx['diferencia'], 2)} p.p."
    )
