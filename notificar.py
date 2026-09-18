"""
Armado y envio del mail de novedades.

El mail se manda en multipart/alternative: texto plano para clientes que no
renderizan HTML (y para la preview del celular) y HTML para el resto. El HTML
usa estilos en linea y tablas, porque es lo unico que Outlook y Gmail
renderizan igual.
"""

from __future__ import annotations

import html
import logging
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from pathlib import Path

import bonos
import contexto as contexto_mod
from estado import EV_CANCELADA, EV_MODIFICADA, EV_NUEVA, EV_RESULTADO
from formato import (
    fecha_hora, formato_ar, monto_largo, solo_fecha, valor_para_mostrar, a_local,
)

log = logging.getLogger(__name__)

COLORES = {
    EV_NUEVA: ("#0b6b3a", "#e8f5ee"),
    EV_RESULTADO: ("#1a4f8a", "#e8f0f9"),
    EV_CANCELADA: ("#8a1a1a", "#f9e8e8"),
    EV_MODIFICADA: ("#7a5c00", "#fbf3de"),
}

_TITULOS_SECCION = {
    EV_NUEVA: "Licitaciones nuevas",
    EV_RESULTADO: "Resultados de adjudicacion",
    EV_CANCELADA: "Canceladas o suspendidas",
    EV_MODIFICADA: "Cambios en licitaciones ya avisadas",
}

ORDEN_SECCIONES = (EV_NUEVA, EV_RESULTADO, EV_CANCELADA, EV_MODIFICADA)


def resumen_corto(eventos: list[dict]) -> str:
    """Texto para el asunto: '3 nuevas, 2 resultados'."""
    conteo: dict[str, int] = {}
    for ev in eventos:
        conteo[ev["evento"]] = conteo.get(ev["evento"], 0) + 1

    partes = []
    plurales = {
        EV_NUEVA: ("nueva", "nuevas"),
        EV_RESULTADO: ("resultado", "resultados"),
        EV_CANCELADA: ("cancelada", "canceladas"),
        EV_MODIFICADA: ("cambio", "cambios"),
    }
    for clave in ORDEN_SECCIONES:
        n = conteo.get(clave, 0)
        if n:
            sing, plur = plurales[clave]
            partes.append(f"{n} {sing if n == 1 else plur}")
    return ", ".join(partes) if partes else "sin novedades"


# --------------------------------------------------------------------------
# Texto plano
# --------------------------------------------------------------------------

def _bloque_texto(ev: dict) -> str:
    lic = ev["licitacion"]

    if lic.get("fuente") == "cnv":
        lineas = [
            f"  {lic['titulo']}",
            f"    Emisor: {lic['emisor'] or '-'}",
            f"    Categoria: {lic['categoria']}",
            f"    Publicado: {fecha_hora(lic['fecha_inicio'])}",
        ]
        if lic.get("enlace"):
            lineas.append(f"    {lic['enlace']}")
        return "\n".join(lineas)

    lineas = [
        f"  {lic['titulo']}",
        f"    Emisor: {lic['emisor'] or '-'}",
        f"    Tipo: {lic['tipo'] or '-'}  |  Categoria: {lic['categoria']}",
        f"    Monto a licitar: {monto_largo(lic['monto_licitar'], lic['moneda_monto'])}",
    ]
    # Solo cuando el filtro de moneda esta activo y esta entro igual por no
    # poder clasificarse. Sin filtro la aclaracion sobra, asi que la marca la
    # pone el filtro (bot.filtrar_por_moneda), no el modelo.
    if lic.get("moneda_a_confirmar"):
        lineas.append("    Moneda: A CONFIRMAR (el aviso de la CNV no la dice)")
    if lic.get("desierta"):
        lineas.append("    Resultado: DESIERTA")
    if lic.get("monto_adjudicado"):
        lineas.append(
            f"    Adjudicado: {monto_largo(lic['monto_adjudicado'], lic['moneda_monto'])}"
        )
    if lic.get("valor_corte"):
        lineas.append(f"    Corte: {lic['valor_corte']}")
    riesgo = bonos.resumen_una_linea(bonos.desde_licitacion(lic))
    if riesgo:
        lineas.append(f"    Riesgo tasa: {riesgo}")
    ctx = ev.get("contexto")
    if ctx:
        lineas.append(f"    Contra el mercado: {ctx['etiqueta']} - "
                      f"{contexto_mod.frase_texto(ctx)}")
    lineas.append(
        f"    Licita: {fecha_hora(lic['fecha_inicio'])} a {fecha_hora(lic['fecha_fin'])}"
    )
    if lic.get("fecha_liquidacion"):
        lineas.append(f"    Liquida: {solo_fecha(lic['fecha_liquidacion'])}")
    if lic.get("fecha_vencimiento"):
        lineas.append(f"    Vence: {solo_fecha(lic['fecha_vencimiento'])}")
    if lic.get("colocador"):
        lineas.append(f"    Colocadores: {lic['colocador']}")
    for c in ev.get("cambios", []):
        lineas.append(
            f"    * {c['etiqueta']}: {valor_para_mostrar(c['antes'])}"
            f" -> {valor_para_mostrar(c['ahora'])}"
        )
    return "\n".join(lineas)


def _es_anticipo(ev: dict) -> bool:
    """Un aviso de la CNV: la emision existe pero todavia no salio a licitar."""
    return ev["licitacion"].get("fuente") == "cnv"


def _particionar(eventos: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separa los anticipos de la CNV del resto.

    Van en secciones distintas porque se leen distinto: uno dice "esto se
    viene, sin datos duros todavia" y el otro "esto esta en rueda ahora, con
    monto y tasa". Mezclados, el anticipo parece una licitacion incompleta.
    """
    anticipos = [e for e in eventos if _es_anticipo(e) and e["evento"] == EV_NUEVA]
    resto = [e for e in eventos if e not in anticipos]
    return anticipos, resto


def cuerpo_texto(eventos: list[dict], generado: datetime,
                 avisos: list[str] | None = None) -> str:
    anticipos, resto = _particionar(eventos)
    partes = [
        "Monitor de licitaciones - mercado primario argentino",
        f"Corrida del {fecha_hora(generado)} (hora Argentina)",
        f"Novedades: {resumen_corto(eventos)}",
        "",
    ]

    if anticipos:
        partes.append(f"AVISOS PUBLICADOS EN LA CNV ({len(anticipos)})")
        partes.append("Todavia no salieron a licitar; sin monto ni tasa.")
        partes.append("-" * 60)
        for ev in anticipos:
            partes.append(_bloque_texto(ev))
            partes.append("")

    for clave in ORDEN_SECCIONES:
        grupo = [e for e in resto if e["evento"] == clave]
        if not grupo:
            continue
        partes.append(f"{_TITULOS_SECCION[clave].upper()} ({len(grupo)})")
        partes.append("-" * 60)
        for ev in grupo:
            partes.append(_bloque_texto(ev))
            partes.append("")

    for aviso in (avisos or []):
        partes.append(f"[!] Fuente con problemas: {aviso}")
    if avisos:
        partes.append("")

    partes.append("Fuentes: A3 Mercados (ex MAE) y hechos relevantes de la CNV.")
    return "\n".join(partes)


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------

def _e(valor) -> str:
    return html.escape(str(valor if valor not in (None, "") else "-"))


def _fila_dato(etiqueta: str, valor: str, resaltar: bool = False) -> str:
    peso = "600" if resaltar else "400"
    color = "#111827" if resaltar else "#374151"
    return (
        '<tr>'
        f'<td style="padding:2px 10px 2px 0;color:#6b7280;font-size:12px;'
        'white-space:nowrap;vertical-align:top">' + _e(etiqueta) + '</td>'
        f'<td style="padding:2px 0;font-size:13px;color:{color};font-weight:{peso}">'
        + valor + '</td></tr>'
    )


COLOR_ANTICIPO = ("#4a3aa7", "#eeecf9")

# Para el contexto de tasa: pagar mas que el mercado no es "bueno" ni "malo"
# en abstracto -- depende de si comprás o emitís -- asi que se usan dos tonos
# neutros del acento en vez de verde/rojo, que sugerirían un juicio.
COLOR_ALTO = "#8a4b0f"
COLOR_BAJO = "#1a5f8a"
GRIS_CTX = "#4b5563"


def _tarjeta_cnv(ev: dict) -> str:
    """Tarjeta de un aviso de la CNV.

    Un aviso tiene emisor, fecha de publicacion y poco mas. Mostrarle los
    campos de una licitacion llenos de guiones lo hace parecer un dato roto,
    cuando en realidad es otro tipo de cosa.
    """
    lic = ev["licitacion"]
    borde, fondo = COLOR_ANTICIPO

    filas = [
        _fila_dato("Emisor", _e(lic["emisor"]), resaltar=True),
        _fila_dato("Publicado", _e(fecha_hora(lic["fecha_inicio"]))),
    ]

    enlace_html = ""
    if lic.get("enlace"):
        enlace_html = (
            f'<div style="margin-top:9px"><a href="{_e(lic["enlace"])}" '
            f'style="color:{borde};font-size:12px;font-weight:600;'
            'text-decoration:underline">Ver la publicacion en la CNV &rarr;</a></div>'
        )

    return (
        f'<div style="border-left:3px solid {borde};background:{fondo};'
        'border-radius:0 6px 6px 0;padding:12px 14px;margin:0 0 10px">'
        f'<div style="font-size:14px;font-weight:700;color:#111827;margin-bottom:2px">'
        f'{_e(lic["titulo"])}</div>'
        f'<div style="font-size:11px;color:{borde};font-weight:600;'
        'text-transform:uppercase;letter-spacing:.4px;margin-bottom:8px">'
        f'{_e(lic["categoria"])} '
        '<span style="color:#9ca3af;font-weight:400">&middot; aviso CNV</span></div>'
        '<table cellpadding="0" cellspacing="0" border="0" style="width:100%">'
        + "".join(filas) + '</table>' + enlace_html + '</div>'
    )


def _tarjeta(ev: dict) -> str:
    if _es_anticipo(ev):
        return _tarjeta_cnv(ev)

    lic = ev["licitacion"]
    borde, fondo = COLORES[ev["evento"]]

    filas = [
        _fila_dato("Emisor", _e(lic["emisor"])),
        _fila_dato("Tipo", _e(lic["tipo"])),
        _fila_dato(
            "A licitar",
            _e(monto_largo(lic["monto_licitar"], lic["moneda_monto"])),
            resaltar=True,
        ),
    ]

    if lic.get("moneda_a_confirmar"):
        filas.append(_fila_dato(
            "Moneda",
            '<span style="color:#8a5a00;font-weight:700">A confirmar</span>'
            ' <span style="color:#666">&mdash; el aviso de la CNV no la dice</span>',
        ))
    if lic.get("desierta"):
        filas.append(_fila_dato(
            "Resultado",
            '<span style="color:#8a1a1a;font-weight:700">DESIERTA</span>',
            resaltar=True,
        ))
    if lic.get("monto_adjudicado"):
        filas.append(_fila_dato(
            "Adjudicado",
            _e(monto_largo(lic["monto_adjudicado"], lic["moneda_monto"])),
            resaltar=True,
        ))
    if lic.get("valor_corte"):
        filas.append(_fila_dato("Corte", _e(lic["valor_corte"]), resaltar=True))

    # Riesgo tasa. Solo aparece cuando el instrumento lo admite: con tasa
    # variable no hay flujo futuro conocido y la fila no se dibuja, en vez de
    # mostrar un numero que parece calculado y no lo es.
    riesgo = bonos.resumen_una_linea(bonos.desde_licitacion(lic))
    if riesgo:
        filas.append(_fila_dato("Riesgo tasa", _e(riesgo)))

    # Dónde queda esa tasa dentro del mercado. Es lo que convierte el numero
    # en una decision: un 38% puede ser caro o barato segun como venga todo.
    ctx = ev.get("contexto")
    if ctx:
        color = (COLOR_ALTO if ctx["percentil"] >= 70
                 else COLOR_BAJO if ctx["percentil"] <= 30 else GRIS_CTX)
        filas.append(_fila_dato(
            "Contra el mercado",
            f'<span style="color:{color};font-weight:600">'
            f'{_e(ctx["etiqueta"])}</span>'
            f'<div style="font-size:11.5px;color:#6b7280;font-weight:400;'
            f'margin-top:2px">{contexto_mod.frase(ctx)}</div>',
        ))
    if lic.get("variable_licitar"):
        filas.append(_fila_dato("Se licita", _e(lic["variable_licitar"])))

    filas.append(_fila_dato(
        "Licitacion",
        f'{_e(fecha_hora(lic["fecha_inicio"]))} &rarr; {_e(fecha_hora(lic["fecha_fin"]))}',
    ))
    if lic.get("fecha_liquidacion"):
        filas.append(_fila_dato("Liquidacion", _e(solo_fecha(lic["fecha_liquidacion"]))))
    if lic.get("fecha_vencimiento"):
        filas.append(_fila_dato("Vencimiento", _e(solo_fecha(lic["fecha_vencimiento"]))))
    if lic.get("colocador"):
        filas.append(_fila_dato("Colocadores", _e(lic["colocador"])))
    if lic.get("sistema_adjudicacion"):
        filas.append(_fila_dato("Adjudicacion", _e(lic["sistema_adjudicacion"])))
    if lic.get("observaciones"):
        filas.append(_fila_dato("Observaciones", _e(lic["observaciones"])))

    cambios_html = ""
    if ev.get("cambios"):
        items = "".join(
            f'<li style="margin:1px 0">{_e(c["etiqueta"])}: '
            f'<span style="color:#9ca3af">{_e(valor_para_mostrar(c["antes"]))}</span> '
            f'&rarr; <strong>{_e(valor_para_mostrar(c["ahora"]))}</strong></li>'
            for c in ev["cambios"]
        )
        cambios_html = (
            '<ul style="margin:8px 0 0;padding-left:18px;font-size:12px;color:#374151">'
            + items + '</ul>'
        )

    # El enlace al documento de la CNV es medio punto del anticipo: sin el hay
    # que ir a buscarlo a mano.
    enlace_html = ""
    if lic.get("enlace"):
        enlace_html = (
            f'<div style="margin-top:9px"><a href="{_e(lic["enlace"])}" '
            f'style="color:{borde};font-size:12px;font-weight:600;'
            'text-decoration:underline">Ver la publicacion en la CNV &rarr;</a></div>'
        )

    origen = "CNV" if lic.get("fuente") == "cnv" else "A3"

    return (
        f'<div style="border-left:3px solid {borde};background:{fondo};'
        'border-radius:0 6px 6px 0;padding:12px 14px;margin:0 0 10px">'
        f'<div style="font-size:14px;font-weight:700;color:#111827;margin-bottom:2px">'
        f'{_e(lic["titulo"])}</div>'
        f'<div style="font-size:11px;color:{borde};font-weight:600;'
        'text-transform:uppercase;letter-spacing:.4px;margin-bottom:8px">'
        f'{_e(lic["categoria"])} '
        f'<span style="color:#9ca3af;font-weight:400">&middot; {origen}</span></div>'
        '<table cellpadding="0" cellspacing="0" border="0" style="width:100%">'
        + "".join(filas) + '</table>' + cambios_html + enlace_html + '</div>'
    )


def _encabezado_seccion(titulo: str, cantidad: int, color: str,
                        subtitulo: str = "") -> str:
    sub = (f'<div style="font-size:11.5px;color:#6b7280;font-weight:400;'
           f'text-transform:none;letter-spacing:0;margin-top:3px">{_e(subtitulo)}</div>'
           if subtitulo else "")
    return (
        f'<h2 style="font-size:13px;font-weight:700;color:{color};margin:22px 0 10px;'
        'text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid #e5e7eb;'
        f'padding-bottom:6px">{_e(titulo)} '
        f'<span style="color:#9ca3af;font-weight:400">({cantidad})</span>{sub}</h2>'
    )


def cuerpo_html(eventos: list[dict], generado: datetime,
                avisos: list[str] | None = None) -> str:
    anticipos, resto = _particionar(eventos)
    secciones = []

    if anticipos:
        secciones.append(
            _encabezado_seccion(
                "Avisos publicados en la CNV", len(anticipos), "#4a3aa7",
                "Todavia no salieron a licitar: sin monto ni tasa hasta que abra la rueda.",
            )
            + "".join(_tarjeta(e) for e in anticipos)
        )

    for clave in ORDEN_SECCIONES:
        grupo = [e for e in resto if e["evento"] == clave]
        if not grupo:
            continue
        secciones.append(
            _encabezado_seccion(_TITULOS_SECCION[clave], len(grupo), COLORES[clave][0])
            + "".join(_tarjeta(e) for e in grupo)
        )

    pie_avisos = ""
    if avisos:
        pie_avisos = (
            '<div style="margin-top:18px;padding:10px 12px;border-left:3px solid #d03b3b;'
            'background:#f9e8e8;border-radius:0 6px 6px 0;font-size:12px;color:#374151">'
            '<strong>Alguna fuente no respondio en esta corrida.</strong> '
            'Lo que sigue puede estar incompleto:<ul style="margin:6px 0 0;padding-left:18px">'
            + "".join(f'<li>{_e(a)}</li>' for a in avisos) + '</ul></div>'
        )

    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
        'max-width:680px;margin:0 auto;padding:20px;background:#ffffff;color:#111827">'
        '<div style="border-bottom:2px solid #111827;padding-bottom:12px;margin-bottom:4px">'
        '<div style="font-size:18px;font-weight:700">Monitor de licitaciones</div>'
        '<div style="font-size:12px;color:#6b7280;margin-top:3px">'
        f'Mercado primario argentino &middot; {_e(fecha_hora(generado))} h'
        '</div></div>'
        f'<div style="font-size:13px;color:#374151;margin:14px 0">'
        f'<strong>{_e(resumen_corto(eventos))}</strong> desde la corrida anterior.</div>'
        + "".join(secciones) + pie_avisos +
        '<div style="margin-top:26px;padding-top:12px;border-top:1px solid #e5e7eb;'
        'font-size:11px;color:#9ca3af">'
        'Fuentes: grilla de licitaciones de A3 Mercados (ex MAE) y hechos '
        'relevantes de la CNV.<br>'
        'Los datos son informativos y pueden ser corregidos por el mercado despues '
        'de la publicacion. Verificar contra el aviso de suscripcion antes de operar.'
        '</div></div>'
    )


# --------------------------------------------------------------------------
# Envio
# --------------------------------------------------------------------------

# Tope por adjunto. Muchos servidores rechazan mensajes de mas de 25 MB, y el
# dashboard crece con el historico: mejor cortar antes y decirlo en el log.
MAX_ADJUNTO_MB = 8

TIPOS_ADJUNTO = {
    ".html": ("text", "html"),
    ".csv": ("text", "csv"),
    ".xlsx": ("application",
              "vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
}


def _adjuntar(msg: EmailMessage, rutas: list) -> None:
    """Suma los archivos pedidos al mensaje, salteando lo que no exista."""
    for ruta in rutas:
        ruta = Path(ruta)
        if not ruta.exists():
            log.warning("No adjunto %s: todavia no existe.", ruta.name)
            continue

        tam_mb = ruta.stat().st_size / 1e6
        if tam_mb > MAX_ADJUNTO_MB:
            log.warning(
                "No adjunto %s: pesa %.1f MB y el tope es %d MB. El archivo "
                "esta igual en la carpeta datos.", ruta.name, tam_mb, MAX_ADJUNTO_MB,
            )
            continue

        maintype, subtype = TIPOS_ADJUNTO.get(
            ruta.suffix.lower(), ("application", "octet-stream")
        )
        try:
            datos = ruta.read_bytes()
        except OSError as exc:
            log.warning("No pude leer %s para adjuntar: %s", ruta.name, exc)
            continue

        msg.add_attachment(datos, maintype=maintype, subtype=subtype,
                           filename=ruta.name)
        log.info("Adjunto %s (%.1f MB).", ruta.name, tam_mb)


def armar_mensaje(eventos: list[dict], cfg: dict, generado: datetime,
                  avisos: list[str] | None = None,
                  adjuntos: list | None = None) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = cfg["asunto"].format(
        resumen=resumen_corto(eventos),
        fecha=solo_fecha(generado),
        cantidad=len(eventos),
    )
    msg["From"] = formataddr(("Monitor de licitaciones", cfg["remitente"]))

    # Si todo va en copia oculta no queda nadie visible en Para, y varios
    # servidores marcan como spam un mensaje sin destinatario. En ese caso el
    # remitente se pone a si mismo, que es la convencion para un envio a lista.
    para = list(cfg.get("destinatarios") or [])
    if not para:
        para = [cfg["remitente"]]
    msg["To"] = ", ".join(para)

    if cfg.get("copia"):
        msg["Cc"] = ", ".join(cfg["copia"])
    if cfg.get("copia_oculta"):
        # smtplib.send_message toma los destinatarios de To, Cc y Bcc, y borra
        # el Bcc antes de enviar: nadie ve esa lista.
        msg["Bcc"] = ", ".join(cfg["copia_oculta"])

    msg["Date"] = formatdate(localtime=True)

    msg.set_content(cuerpo_texto(eventos, generado, avisos))
    msg.add_alternative(cuerpo_html(eventos, generado, avisos), subtype="html")

    if adjuntos:
        _adjuntar(msg, adjuntos)

    return msg


def armar_informe(datos: dict, cfg: dict, generado: datetime,
                  adjuntos: list | None = None) -> EmailMessage:
    """Arma el mail de informe (informe.py) con los mismos destinatarios y
    reglas de envio que el de novedades."""
    import informe

    msg = EmailMessage()
    msg["Subject"] = informe.asunto(datos, cfg, generado)
    msg["From"] = formataddr((cfg.get("nombre_remitente")
                              or "Monitor de licitaciones", cfg["remitente"]))

    para = list(cfg.get("destinatarios_informe") or cfg.get("destinatarios") or [])
    if not para:
        para = [cfg["remitente"]]
    msg["To"] = ", ".join(para)
    if cfg.get("copia_informe"):
        msg["Cc"] = ", ".join(cfg["copia_informe"])
    if cfg.get("copia_oculta_informe"):
        msg["Bcc"] = ", ".join(cfg["copia_oculta_informe"])
    msg["Date"] = formatdate(localtime=True)

    msg.set_content(informe.cuerpo_texto(datos, generado))
    msg.add_alternative(
        informe.cuerpo_html(datos, generado, con_adjunto=bool(adjuntos)),
        subtype="html",
    )
    if adjuntos:
        _adjuntar(msg, adjuntos)
    return msg


def enviar_informe(datos: dict, cfg: dict, generado: datetime,
                   adjuntos: list | None = None) -> bool:
    msg = armar_informe(datos, cfg, generado, adjuntos)
    return _entregar(msg, cfg)


def enviar(eventos: list[dict], cfg: dict, generado: datetime,
           avisos: list[str] | None = None,
           adjuntos: list | None = None) -> bool:
    """Manda el mail de novedades. Devuelve True si salio."""
    return _entregar(armar_mensaje(eventos, cfg, generado, avisos, adjuntos), cfg)


def diagnostico_credenciales(cfg: dict, exc: Exception | None = None) -> str:
    """Explica por que el servidor rechazo el login, mirando lo que se mando.

    Nunca imprime la contrasena: solo su largo y si trae espacios, que es lo
    que hace falta para darse cuenta del error sin exponerla.
    """
    usuario = cfg.get("usuario_smtp", "")
    clave = cfg.get("password_smtp", "")
    host = cfg.get("smtp_host", "")

    # Google muestra la contrasena de aplicacion en cuatro grupos de cuatro, y
    # los espacios no cuentan. Se informan los dos numeros para que la linea
    # no parezca contradecir al diagnostico de abajo.
    sin_espacios = clave.replace(" ", "")
    largo = f"{len(clave)} caracteres"
    if " " in clave:
        largo += f" ({len(sin_espacios)} sin los espacios, que Google ignora)"

    lineas = [
        f"  Casilla:  {usuario or '(vacia)'}",
        f"  Servidor: {host}:{cfg.get('smtp_puerto')}",
        f"  Clave:    {largo}"
        + (", entre comillas (!)" if clave[:1] in "\"'" else ""),
        "",
    ]

    es_google = "gmail" in host.lower()

    if clave[:1] in "\"'" or clave[-1:] in "\"'":
        lineas.append("  >> La clave quedo guardada CON las comillas adentro.")
        lineas.append("     Pasa cuando se usa setx con comillas y la clave ya")
        lineas.append("     tenia otras. Volve a cargarla sin comillas de mas.")
    elif es_google and len(clave.replace(" ", "")) != 16:
        lineas.append(f"  >> Una contrasena de aplicacion de Google tiene 16")
        lineas.append(f"     caracteres; esta tiene {len(clave.replace(' ', ''))}.")
        lineas.append("     Lo mas probable es que sea la clave de la cuenta, que")
        lineas.append("     Google no acepta por SMTP desde 2022.")
    elif es_google:
        lineas.append("  >> El largo es el correcto, asi que el problema es otro:")
        lineas.append("     - la contrasena es de OTRA cuenta distinta a la de arriba")
        lineas.append("     - se revoco desde la configuracion de Google")
        lineas.append("     - la cuenta no tiene verificacion en dos pasos activada")
        lineas.append("       (sin eso Google no deja crear contrasenas de aplicacion)")
        lineas.append("     - en Workspace, el admin bloqueo este tipo de acceso")
    else:
        lineas.append("  >> Revisa que la casilla y la clave sean del mismo servidor,")
        lineas.append(f"     y que {host} sea el SMTP correcto para esa casilla.")

    if es_google:
        lineas.append("")
        lineas.append("  Para generar una: myaccount.google.com > Seguridad >")
        lineas.append("  Verificacion en dos pasos > Contrasenas de aplicaciones.")

    if exc is not None:
        lineas.append("")
        lineas.append(f"  Respuesta del servidor: {exc}")

    return "\n".join(lineas)


def probar_credenciales(cfg: dict) -> bool:
    """Conecta y autentica, sin mandar nada. Para probar el mail rapido."""
    contexto = ssl.create_default_context()
    try:
        if cfg["smtp_tls"]:
            with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_puerto"], timeout=30) as s:
                s.starttls(context=contexto)
                s.login(cfg["usuario_smtp"], cfg["password_smtp"])
        else:
            with smtplib.SMTP_SSL(cfg["smtp_host"], cfg["smtp_puerto"],
                                  context=contexto, timeout=30) as s:
                s.login(cfg["usuario_smtp"], cfg["password_smtp"])
    except smtplib.SMTPAuthenticationError as exc:
        print("[FALLA] El servidor rechazo las credenciales.\n")
        print(diagnostico_credenciales(cfg, exc))
        return False
    except (smtplib.SMTPException, OSError) as exc:
        print(f"[FALLA] No pude conectarme a {cfg['smtp_host']}:"
              f"{cfg['smtp_puerto']}: {exc}\n")
        print("  Si es un timeout, puede ser el firewall de la empresa "
              "bloqueando el puerto de salida.")
        return False

    print(f"[ok] {cfg['usuario_smtp']} autentico bien en {cfg['smtp_host']}.")
    return True


def _entregar(msg: EmailMessage, cfg: dict) -> bool:
    """Conexion, login y envio. Lo comparten el mail de novedades y el informe."""
    contexto = ssl.create_default_context()
    try:
        if cfg["smtp_tls"]:
            with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_puerto"], timeout=30) as s:
                s.starttls(context=contexto)
                s.login(cfg["usuario_smtp"], cfg["password_smtp"])
                s.send_message(msg)
        else:
            with smtplib.SMTP_SSL(
                cfg["smtp_host"], cfg["smtp_puerto"], context=contexto, timeout=30
            ) as s:
                s.login(cfg["usuario_smtp"], cfg["password_smtp"])
                s.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        log.error("El servidor rechazo las credenciales.\n%s",
                  diagnostico_credenciales(cfg, exc))
        return False
    except (smtplib.SMTPException, OSError) as exc:
        log.error("No se pudo enviar el mail: %s", exc)
        return False

    # Se informa lo que salio en el mensaje, no lo que dice la config: el
    # informe y las novedades pueden ir a listas distintas.
    visibles = ", ".join(x for x in (msg["To"], msg["Cc"]) if x)
    ocultos = len([d for d in (msg["Bcc"] or "").split(",") if d.strip()])
    log.info("Mail enviado a %s%s.", visibles,
             f" (+{ocultos} en copia oculta)" if ocultos else "")
    return True
