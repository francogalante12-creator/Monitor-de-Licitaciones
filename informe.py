"""
Mail de informe: el panorama del mercado, pensado para reenviar.

Es distinto del mail de novedades. Aquel es operativo y para una persona que
ya tiene contexto: dice "esto cambio desde hace dos horas". Este es un
resumen del estado de las cosas, y esta escrito sabiendo que va a terminar
reenviado a alguien que no sabe que existe este bot, no va a abrir un
adjunto y probablemente lo lea en el telefono.

De ahi las tres reglas de este archivo:

1. Todo lo que importa esta en el cuerpo del mail. Nada obliga a abrir un
   archivo: los adjuntos, si van, son un extra.
2. Se explica solo. Un pie dice que es esto, de donde salen los datos y con
   que frecuencia se arma.
3. HTML de mail, no HTML de web: tablas, estilos en linea y nada de flex ni
   grid, que Outlook no renderiza.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timedelta, timezone

from formato import a_local, fecha_hora, formato_ar, monto_largo, solo_fecha
from modelo import CATEGORIAS_ON

log = logging.getLogger(__name__)

DIAS_ES = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
MESES_ES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

TINTA = "#111827"
TINTA_2 = "#374151"
GRIS = "#6b7280"
LINEA = "#e5e7eb"
ACENTO = "#1a5f8a"
VERDE = "#0b6b3a"


def _e(v) -> str:
    return html.escape(str(v if v not in (None, "") else "-"))


def fecha_larga(dt: datetime) -> str:
    local = a_local(dt)
    return (f"{DIAS_ES[local.weekday()]} {local.day} de {MESES_ES[local.month - 1]} "
            f"de {local.year}")


# ---------------------------------------------------------------------------
# Seleccion de contenido
# ---------------------------------------------------------------------------

def _mediana(valores: list[float]) -> float | None:
    if not valores:
        return None
    s = sorted(valores)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def armar_datos(registros: list[dict], ahora: datetime, dias: int = 7) -> dict:
    """Elige que entra en el informe.

    Se separa de la maquetacion a proposito: que se muestra es una decision
    de contenido y conviene poder leerla (y testearla) sin atravesar HTML.
    """
    desde = ahora - timedelta(days=dias)

    en_rueda, anunciadas, resueltas = [], [], []
    for r in registros:
        estado = r.get("estado")
        inicio = r.get("fecha_inicio")
        if estado == "Activa":
            en_rueda.append(r)
        elif estado == "Anunciada":
            # Solo los avisos recientes: uno de hace un mes ya no anticipa nada.
            if inicio and inicio >= desde:
                anunciadas.append(r)
        elif estado == "Finalizada" and inicio and inicio >= desde:
            resueltas.append(r)

    orden_fin = lambda r: r.get("fecha_fin") or r.get("fecha_inicio") or desde
    en_rueda.sort(key=orden_fin)
    anunciadas.sort(key=lambda r: r.get("fecha_inicio") or desde, reverse=True)
    resueltas.sort(key=lambda r: r.get("fecha_inicio") or desde, reverse=True)

    on_resueltas = [r for r in resueltas if r.get("categoria") in CATEGORIAS_ON]
    tasas_ars = [r["tasa_corte"] for r in on_resueltas
                 if r.get("tasa_corte") is not None
                 and str(r.get("moneda_monto", "")).startswith("ARS")]
    tasas_usd = [r["tasa_corte"] for r in on_resueltas
                 if r.get("tasa_corte") is not None
                 and str(r.get("moneda_monto", "")).startswith("USD")]

    return {
        "en_rueda": en_rueda,
        "anunciadas": anunciadas,
        "resueltas": resueltas,
        "on_resueltas": on_resueltas,
        "dias": dias,
        "desde": desde,
        "mediana_ars": _mediana(tasas_ars),
        "mediana_usd": _mediana(tasas_usd),
        "desiertas": sum(1 for r in on_resueltas if r.get("desierta")),
    }


def hay_algo_para_contar(datos: dict) -> bool:
    return bool(datos["en_rueda"] or datos["anunciadas"] or datos["resueltas"])


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

def _titulo_seccion(texto: str, aclaracion: str = "") -> str:
    extra = (f' <span style="color:{GRIS};font-weight:400;text-transform:none;'
             f'letter-spacing:0;font-size:12px">{_e(aclaracion)}</span>'
             if aclaracion else "")
    return (
        f'<tr><td style="padding:26px 0 9px">'
        f'<div style="font-size:12.5px;font-weight:700;color:{TINTA};'
        'text-transform:uppercase;letter-spacing:.6px;border-bottom:2px solid '
        f'{TINTA};padding-bottom:6px">{_e(texto)}{extra}</div>'
        '</td></tr>'
    )


def _fila_licitacion(r: dict, mostrar: str) -> str:
    """Una licitacion como fila de tabla. `mostrar` cambia la columna derecha:
    lo que importa de una que abre es cuando cierra; de una que cerro, en
    cuanto cortó."""
    titulo = r.get("titulo", "")
    emisor = r.get("emisor", "")

    if mostrar == "rueda":
        cierre = a_local(r.get("fecha_fin"))
        derecha = (f'<div style="font-size:13px;color:{TINTA};font-weight:600">'
                   f'cierra {cierre.strftime("%H:%M")}</div>' if cierre else "")
        bajo = _e(monto_largo(r.get("monto_licitar"), r.get("moneda_monto")))
    elif mostrar == "anuncio":
        derecha = (f'<div style="font-size:12px;color:{GRIS}">'
                   f'{_e(solo_fecha(r.get("fecha_inicio")))}</div>')
        bajo = "aviso publicado"
    else:
        if r.get("desierta"):
            derecha = ('<div style="font-size:13px;color:#8a1a1a;font-weight:700">'
                       'DESIERTA</div>')
        elif r.get("tasa_corte") is not None:
            derecha = (f'<div style="font-size:14px;color:{TINTA};font-weight:700">'
                       f'{_e(formato_ar(r["tasa_corte"], 2))}%</div>')
        else:
            derecha = f'<div style="font-size:12px;color:{GRIS}">sin corte</div>'
        adj = r.get("monto_adjudicado")
        bajo = (f'adjudicado {_e(monto_largo(adj, r.get("moneda_monto")))}'
                if adj else _e(monto_largo(r.get("monto_licitar"),
                                           r.get("moneda_monto"))))

    # La categoria va en cada fila porque quien recibe esto reenviado no tiene
    # por que saber si "FF MEGABONO" es una ON o un fideicomiso.
    cat = r.get("categoria", "")
    etiqueta = (f'<span style="color:{GRIS}"> &middot; {_e(cat)}</span>'
                if cat else "")

    return (
        f'<tr><td style="padding:9px 0;border-bottom:1px solid {LINEA}">'
        '<table cellpadding="0" cellspacing="0" border="0" width="100%">'
        '<tr>'
        '<td style="vertical-align:top">'
        f'<div style="font-size:13.5px;font-weight:600;color:{TINTA};line-height:1.35">'
        f'{_e(titulo)}</div>'
        f'<div style="font-size:12px;color:{GRIS};margin-top:2px">'
        f'{_e(emisor)}{etiqueta}</div>'
        '</td>'
        '<td style="vertical-align:top;text-align:right;white-space:nowrap;'
        'padding-left:14px">'
        f'{derecha}'
        f'<div style="font-size:11.5px;color:{GRIS};margin-top:2px">{bajo}</div>'
        '</td>'
        '</tr></table></td></tr>'
    )


def _tile(valor: str, etiqueta: str) -> str:
    return (
        f'<td style="padding:11px 13px;border:1px solid {LINEA};border-radius:6px;'
        'vertical-align:top">'
        f'<div style="font-size:19px;font-weight:700;color:{TINTA};line-height:1.2">'
        f'{valor}</div>'
        f'<div style="font-size:11px;color:{GRIS};margin-top:3px">{_e(etiqueta)}</div>'
        '</td>'
    )


def cuerpo_html(datos: dict, ahora: datetime, con_adjunto: bool = False) -> str:
    filas = []

    # Panorama en numeros. Solo los que tienen algo que decir: un tile en cero
    # ocupa lugar y no informa.
    tiles = []
    if datos["en_rueda"]:
        tiles.append(_tile(str(len(datos["en_rueda"])), "en rueda ahora"))
    if datos["anunciadas"]:
        tiles.append(_tile(str(len(datos["anunciadas"])), "anunciadas"))
    tiles.append(_tile(str(len(datos["resueltas"])), f"cerraron en {datos['dias']} dias"))
    if datos["mediana_ars"] is not None:
        tiles.append(_tile(f'{formato_ar(datos["mediana_ars"], 2)}%',
                           "corte mediano en $"))
    if datos["mediana_usd"] is not None:
        tiles.append(_tile(f'{formato_ar(datos["mediana_usd"], 2)}%',
                           "corte mediano en US$"))

    filas.append(
        '<tr><td style="padding:4px 0 0">'
        '<table cellpadding="0" cellspacing="6" border="0" width="100%"><tr>'
        + "".join(tiles) + '</tr></table></td></tr>'
    )

    if datos["en_rueda"]:
        filas.append(_titulo_seccion("En rueda ahora", "se puede entrar hoy"))
        filas.append('<tr><td><table cellpadding="0" cellspacing="0" border="0" '
                     'width="100%">'
                     + "".join(_fila_licitacion(r, "rueda") for r in datos["en_rueda"])
                     + '</table></td></tr>')

    if datos["anunciadas"]:
        filas.append(_titulo_seccion("Anunciadas", "publicadas en la CNV, "
                                                   "todavia no licitaron"))
        filas.append('<tr><td><table cellpadding="0" cellspacing="0" border="0" '
                     'width="100%">'
                     + "".join(_fila_licitacion(r, "anuncio")
                               for r in datos["anunciadas"][:10])
                     + '</table></td></tr>')

    if datos["resueltas"]:
        filas.append(_titulo_seccion(
            f"Cerraron en los ultimos {datos['dias']} dias",
            "tasa o margen de corte"))
        filas.append('<tr><td><table cellpadding="0" cellspacing="0" border="0" '
                     'width="100%">'
                     + "".join(_fila_licitacion(r, "resultado")
                               for r in datos["resueltas"][:25])
                     + '</table></td></tr>')
        if len(datos["resueltas"]) > 25:
            filas.append(
                f'<tr><td style="padding:9px 0;font-size:12px;color:{GRIS}">'
                f'y {len(datos["resueltas"]) - 25} colocaciones mas en el periodo.'
                '</td></tr>')

    nota_adjunto = ""
    if con_adjunto:
        nota_adjunto = (
            f'<p style="margin:0 0 9px">Va adjunto el tablero completo, con el '
            'historico, los filtros y la evolucion de las tasas de corte.</p>'
        )

    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,'
        f'sans-serif;max-width:660px;margin:0 auto;padding:22px;background:#ffffff;'
        f'color:{TINTA}">'

        # Encabezado
        f'<div style="border-bottom:2px solid {TINTA};padding-bottom:13px">'
        '<div style="font-size:19px;font-weight:700;letter-spacing:-.01em">'
        'Licitaciones de obligaciones negociables</div>'
        f'<div style="font-size:12.5px;color:{GRIS};margin-top:4px">'
        f'Mercado primario argentino &middot; {_e(fecha_larga(ahora))}</div>'
        '</div>'

        '<table cellpadding="0" cellspacing="0" border="0" width="100%">'
        + "".join(filas) +
        '</table>'

        # Pie: quien reciba esto reenviado no sabe que es, y tiene que poder
        # entenderlo sin preguntarle a nadie.
        f'<div style="margin-top:30px;padding-top:14px;border-top:1px solid {LINEA};'
        f'font-size:11.5px;color:{GRIS};line-height:1.6">'
        + nota_adjunto +
        '<p style="margin:0 0 9px"><strong style="color:' + TINTA_2 + '">Que es '
        'esto.</strong> Un resumen automatico de las licitaciones del mercado '
        'primario argentino: obligaciones negociables corporativas y PyME CNV, '
        'fideicomisos financieros y deuda publica. Se arma solo a partir de la '
        'grilla de licitaciones de A3 Mercados (ex MAE) y de los hechos '
        'relevantes publicados en la CNV.</p>'
        '<p style="margin:0"><strong style="color:' + TINTA_2 + '">Antes de '
        'operar.</strong> Los datos son informativos y el mercado puede '
        'corregirlos despues de publicados. Verificar siempre contra el aviso de '
        'suscripcion y el suplemento de prospecto.</p>'
        '</div>'
        '</div>'
    )


def cuerpo_texto(datos: dict, ahora: datetime) -> str:
    p = [
        "LICITACIONES DE OBLIGACIONES NEGOCIABLES",
        f"Mercado primario argentino - {fecha_larga(ahora)}",
        "",
    ]

    if datos["mediana_ars"] is not None:
        p.append(f"Corte mediano en pesos: {formato_ar(datos['mediana_ars'], 2)}%")
    if datos["mediana_usd"] is not None:
        p.append(f"Corte mediano en dolares: {formato_ar(datos['mediana_usd'], 2)}%")
    p.append("")

    def bloque(titulo, regs, modo):
        if not regs:
            return
        p.append(titulo.upper())
        p.append("-" * 58)
        for r in regs:
            p.append(f"  {r.get('titulo', '')}")
            p.append(f"    {r.get('emisor', '')}")
            if modo == "rueda":
                p.append(f"    Cierra {fecha_hora(r.get('fecha_fin'))} - "
                         f"{monto_largo(r.get('monto_licitar'), r.get('moneda_monto'))}")
            elif modo == "anuncio":
                p.append(f"    Aviso publicado {solo_fecha(r.get('fecha_inicio'))}")
            else:
                if r.get("desierta"):
                    p.append("    DESIERTA")
                elif r.get("tasa_corte") is not None:
                    p.append(f"    Corte {formato_ar(r['tasa_corte'], 2)}% - "
                             f"adjudicado "
                             f"{monto_largo(r.get('monto_adjudicado'), r.get('moneda_monto'))}")
        p.append("")

    bloque("En rueda ahora", datos["en_rueda"], "rueda")
    bloque("Anunciadas en la CNV", datos["anunciadas"][:10], "anuncio")
    bloque(f"Cerraron en los ultimos {datos['dias']} dias",
           datos["resueltas"][:25], "resultado")

    p.append("Resumen automatico armado con la grilla de licitaciones de A3 "
             "Mercados (ex MAE) y los hechos relevantes de la CNV.")
    p.append("Datos informativos: verificar contra el aviso de suscripcion antes "
             "de operar.")
    return "\n".join(p)


def asunto(datos: dict, cfg: dict, ahora: datetime) -> str:
    local = a_local(ahora)
    partes = []
    if datos["en_rueda"]:
        partes.append(f"{len(datos['en_rueda'])} en rueda")
    if datos["resueltas"]:
        partes.append(f"{len(datos['resueltas'])} cerradas")
    resumen = ", ".join(partes) if partes else "sin movimientos"

    return cfg.get("asunto_informe", "Licitaciones ON - {fecha}").format(
        fecha=local.strftime("%d/%m/%Y"),
        resumen=resumen,
        en_rueda=len(datos["en_rueda"]),
        cerradas=len(datos["resueltas"]),
    )
