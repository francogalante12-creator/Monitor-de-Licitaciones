"""
Carga de configuracion.

Las preferencias (que categorias avisar, a quien, cada cuanto) van en
config.yaml, que se versiona. Las credenciales van SIEMPRE en variables de
entorno y nunca en el archivo: en GitHub Actions salen de los Secrets del
repo, y en la PC de la oficina del entorno del usuario o del .bat.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

import modelo
from modelo import TODAS_LAS_CATEGORIAS

log = logging.getLogger(__name__)

RAIZ = Path(__file__).resolve().parent

DEFAULTS: dict[str, Any] = {
    "fuentes": ["a3", "cnv"],
    # Proxy de salida, si la red lo exige. Sin credenciales aca: para eso esta
    # la variable de entorno BOT_PROXY, que ademas tiene prioridad.
    "proxy": "",
    # Todas las emisiones de deuda del mercado primario, deuda publica
    # incluida. Se recorta en config.yaml si alguna categoria no interesa.
    "categorias_aviso": sorted(TODAS_LAS_CATEGORIAS),
    "categorias_planilla": sorted(TODAS_LAS_CATEGORIAS),
    "eventos_aviso": ["nueva", "resultado", "cancelada"],
    # Ventana para comparar una tasa de corte contra el mercado, en dias.
    "contexto_dias": 60,
    "monto_minimo_ars": 0,
    # Monedas que se siguen. Vacio = todas, que es el comportamiento por
    # defecto. Ver la nota en config.yaml: los avisos de la CNV no traen
    # moneda, y por eso existe la opcion de abajo, que decide que hacer con lo
    # que no se puede clasificar cuando el filtro SI esta puesto.
    "monedas": [],
    "moneda_desconocida": "avisar",   # avisar | ignorar
    "emisores_destacados": [],
    # Que archivos viajan adjuntos en el mail: dashboard | xlsx | csv
    "adjuntar": [],
    # --- Mail de informe (el que se reenvia) ---
    "adjuntar_informe": [],
    "asunto_informe": "Licitaciones ON - {fecha}",
    "informe_dias": 7,
    "nombre_remitente": "",
    # Si quedan vacios, el informe usa los destinatarios del mail de novedades.
    "destinatarios_informe": [],
    "copia_informe": [],
    "copia_oculta_informe": [],
    "asunto": "Licitaciones ON - {resumen}",
    "destinatarios": [],
    "copia": [],
    "copia_oculta": [],
    "remitente": "",
    "smtp_host": "smtp.gmail.com",
    "smtp_puerto": 587,
    "smtp_tls": True,
    # Carpetas adicionales donde dejar copia de las salidas, por ejemplo un
    # share de red para que lo vea el resto del equipo.
    "copiar_a": [],
    # URL publica del dashboard, si se publica en algun lado. Se usa para la
    # vista previa cuando el link se comparte en un chat.
    "url_publica": "",
    "archivo_estado": "datos/estado.json",
    "archivo_csv": "datos/licitaciones.csv",
    "archivo_xlsx": "datos/licitaciones.xlsx",
    "archivo_dashboard": "datos/dashboard.html",
}

# Variables de entorno que llevan secretos.
ENV_USUARIO = "SMTP_USUARIO"
ENV_PASSWORD = "SMTP_PASSWORD"
ENV_DESTINATARIOS = "BOT_DESTINATARIOS"  # opcional, separados por coma


class ConfigInvalida(ValueError):
    pass


def _resolver(ruta: str) -> Path:
    p = Path(ruta)
    return p if p.is_absolute() else RAIZ / p


def cargar(ruta_config: str | Path | None = None) -> dict:
    ruta = Path(ruta_config) if ruta_config else RAIZ / "config.yaml"

    datos: dict[str, Any] = {}
    if ruta.exists():
        with ruta.open(encoding="utf-8") as fh:
            datos = yaml.safe_load(fh) or {}
        if not isinstance(datos, dict):
            raise ConfigInvalida(f"{ruta} no contiene un mapa YAML valido.")
    else:
        # Sin config el bot arranca, pero sin destinatarios no manda nada, y
        # eso se ve igual que "no hubo novedades". Mejor gritarlo: la causa
        # habitual es una extraccion del zip que quedo incompleta.
        log.error(
            "No encontre %s.\n"
            "  Ese archivo tiene que estar en la misma carpeta que bot.py.\n"
            "  Si acabas de descomprimir el zip, es probable que la extraccion\n"
            "  haya quedado a medias: fijate que esten los %d archivos y volve\n"
            "  a extraer si falta alguno.\n"
            "  Por ahora sigo con los valores por defecto, que NO incluyen\n"
            "  destinatarios: no se va a enviar ningun mail.",
            ruta, 24,
        )

    cfg = {**DEFAULTS, **datos}

    # Los destinatarios de la variable de entorno pisan los del archivo, para
    # poder cambiarlos en CI sin tocar el repo.
    env_dest = os.getenv(ENV_DESTINATARIOS, "").strip()
    if env_dest:
        cfg["destinatarios"] = [d.strip() for d in env_dest.split(",") if d.strip()]

    # Los espacios y saltos de linea de los bordes se sacan siempre. Al pegar
    # una casilla en el formulario de secretos de GitHub (o en un setx) se
    # cuela un salto de linea al final sin que se vea, y despues el envio
    # explota en la cabecera "From" con un ValueError de la libreria de
    # correo, que no dice nada de donde vino el problema.
    cfg["usuario_smtp"] = os.getenv(ENV_USUARIO, "").strip()
    cfg["password_smtp"] = os.getenv(ENV_PASSWORD, "").strip()
    cfg["remitente"] = str(cfg["remitente"] or "").strip()
    if not cfg["remitente"]:
        cfg["remitente"] = cfg["usuario_smtp"]

    # Si todavia queda un espacio o un caracter de control ADENTRO, no se
    # arregla solo: una direccion no los lleva, asi que es un error de carga y
    # conviene decirlo aca, con el nombre del secreto que hay que corregir.
    for clave, etiqueta in (("usuario_smtp", ENV_USUARIO), ("remitente", "remitente")):
        valor = cfg[clave]
        if valor and any(c.isspace() for c in valor):
            raise ConfigInvalida(
                f"{etiqueta} tiene un espacio o un salto de linea adentro: "
                f"{valor!r}. Una direccion de correo no puede tenerlos. "
                f"Volve a cargarlo cuidando de no arrastrar el salto de linea "
                f"al copiar y pegar."
            )

    for clave in ("archivo_estado", "archivo_csv", "archivo_xlsx", "archivo_dashboard"):
        cfg[clave] = _resolver(str(cfg[clave]))

    from fuentes import FUENTES
    if not cfg["fuentes"]:
        raise ConfigInvalida("Hay que habilitar al menos una fuente.")
    desconocidas = set(cfg["fuentes"]) - set(FUENTES)
    if desconocidas:
        raise ConfigInvalida(
            f"fuentes nombra fuentes que no existen: {sorted(desconocidas)}. "
            f"Las que hay: {sorted(FUENTES)}"
        )

    adjuntos_validos = {"dashboard", "xlsx", "csv"}
    for clave in ("adjuntar", "adjuntar_informe"):
        desconocidos = set(cfg[clave]) - adjuntos_validos
        if desconocidos:
            raise ConfigInvalida(
                f"{clave} nombra archivos que no existen: {sorted(desconocidos)}. "
                f"Validos: {sorted(adjuntos_validos)}"
            )

    try:
        cfg["informe_dias"] = max(1, int(cfg["informe_dias"]))
    except (TypeError, ValueError):
        raise ConfigInvalida(
            f"informe_dias tiene que ser un numero entero de dias, "
            f"no {cfg['informe_dias']!r}."
        ) from None

    monedas_validas = set(modelo.MONEDAS_CONOCIDAS)
    cfg["monedas"] = [str(m).strip() for m in (cfg["monedas"] or []) if str(m).strip()]
    desconocidas = set(cfg["monedas"]) - monedas_validas
    if desconocidas:
        raise ConfigInvalida(
            f"monedas nombra monedas que no existen: {sorted(desconocidas)}. "
            f"Validas: {sorted(monedas_validas)}. Lista vacia = todas."
        )

    if cfg["moneda_desconocida"] not in ("avisar", "ignorar"):
        raise ConfigInvalida(
            f"moneda_desconocida tiene que ser 'avisar' o 'ignorar', "
            f"no {cfg['moneda_desconocida']!r}."
        )

    categorias_validas = set(TODAS_LAS_CATEGORIAS)
    for clave in ("categorias_aviso", "categorias_planilla"):
        desconocidas = set(cfg[clave]) - categorias_validas
        if desconocidas:
            raise ConfigInvalida(
                f"{clave} nombra categorias que no existen: {sorted(desconocidas)}. "
                f"Validas: {sorted(categorias_validas)}"
            )

    return cfg


def todos_los_destinos(cfg: dict) -> list[str]:
    """Para, copia y copia oculta juntos: a cuanta gente le llega en total."""
    return [d for clave in ("destinatarios", "copia", "copia_oculta")
            for d in cfg.get(clave, []) if d]


def puede_mandar_mail(cfg: dict) -> tuple[bool, str]:
    """Dice si estan dadas las condiciones para enviar, y por que no si falta algo."""
    if not todos_los_destinos(cfg):
        return False, ("no hay destinatarios configurados (ni en 'destinatarios', "
                       "ni en 'copia', ni en 'copia_oculta')")
    if not cfg["usuario_smtp"] or not cfg["password_smtp"]:
        return False, f"faltan las variables de entorno {ENV_USUARIO} y/o {ENV_PASSWORD}"
    return True, ""
