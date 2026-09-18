"""
Capa de red comun a todas las fuentes.

Existe por dos motivos. Uno, no repetir la logica de reintentos en cada
fuente. Dos, y mas importante: traducir los errores de red a un diagnostico
util. "getaddrinfo failed" y "403 Forbidden" se ven parecidos desde el codigo
pero se arreglan de manera completamente distinta, y perder media hora
probando lo que no es sale caro.
"""

from __future__ import annotations

import logging
import os
import re
import socket
import time
from typing import Any
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

# Cabeceras de navegador. Varios sitios del mercado local estan detras de
# WAFs que rechazan clientes sin User-Agent creible.
HEADERS_BASE = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-AR,es;q=0.9",
}

# Variable de entorno propia del bot: tiene prioridad sobre la config y sobre
# las HTTP(S)_PROXY del sistema. Es el lugar para un proxy con usuario y
# clave, que no deberia quedar escrito en config.yaml.
ENV_PROXY = "BOT_PROXY"

# Proxy configurado desde config.yaml, si lo hay.
_proxy_configurado: str | None = None


class ErrorDeRed(RuntimeError):
    """Falla de red ya traducida a algo que se puede accionar."""


def configurar_proxy(proxy: str | None) -> None:
    """Fija el proxy que van a usar todas las fuentes. Lo llama el bot al
    arrancar, con lo que diga config.yaml."""
    global _proxy_configurado
    _proxy_configurado = proxy or None


def _proxies() -> dict | None:
    """Devuelve el dict de proxies para requests, o None para que use lo del
    sistema (requests ya lee HTTP_PROXY y HTTPS_PROXY solo)."""
    proxy = os.getenv(ENV_PROXY, "").strip() or _proxy_configurado
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def proxy_visible() -> str:
    """El proxy en uso, con la contrasena tapada, para poder loguearlo."""
    p = _proxies()
    if not p:
        deL_sistema = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
        if deL_sistema:
            return _tapar(deL_sistema) + " (del sistema)"
        return "sin proxy"
    return _tapar(p["https"])


def _tapar(url: str) -> str:
    return re.sub(r"//([^:/@]+):([^@]+)@", r"//\1:****@", url)


# ---------------------------------------------------------------------------
# Diagnostico
# ---------------------------------------------------------------------------

def explicar(exc: Exception, url: str) -> str:
    """Traduce una excepcion de requests al problema real y a como se arregla.

    Cada rama corresponde a una causa distinta con una solucion distinta.
    """
    host = urlparse(url).hostname or url
    causa = _causa_raiz(exc)
    texto = str(causa).lower()

    # 1. DNS: el nombre no se pudo resolver. La conexion nunca se intento.
    if isinstance(causa, socket.gaierror) or "getaddrinfo" in texto \
            or "name or service not known" in texto \
            or "failed to resolve" in texto or "nameresolution" in texto:
        return (
            f"No se pudo resolver el nombre {host} (falla de DNS). La maquina "
            f"no llego a intentar la conexion.\n"
            f"  Esto NO es el sitio bloqueandote: es tu red o tu DNS.\n"
            f"  Causas tipicas, en orden:\n"
            f"    a) la maquina esta sin internet -> probar: ping 8.8.8.8\n"
            f"    b) el DNS de la empresa no resuelve ese dominio -> probar:\n"
            f"       nslookup {host}   y luego   nslookup {host} 8.8.8.8\n"
            f"       si el segundo anda y el primero no, es el DNS corporativo\n"
            f"    c) la salida a internet pasa por un proxy y hay que declararlo:\n"
            f"       set {ENV_PROXY}=http://usuario:clave@proxy.empresa:8080\n"
            f"       o la opcion 'proxy' en config.yaml"
        )

    # 2. Proxy: hay proxy pero rechaza o no responde.
    if "proxyerror" in texto or "tunnel connection failed" in texto \
            or "cannot connect to proxy" in texto:
        return (
            f"El proxy rechazo la conexion a {host}.\n"
            f"  Proxy en uso: {proxy_visible()}\n"
            f"  Si dice 403 o 407, ese dominio no esta en la lista blanca de la\n"
            f"  empresa, o faltan las credenciales del proxy. Es una decision de\n"
            f"  red, no algo que el bot pueda saltear: hay que pedir que lo\n"
            f"  habiliten, o correr el bot desde otra red."
        )

    # 3. Timeout: resolvio y conecto, pero el sitio no contesta a tiempo.
    if "timed out" in texto or "timeout" in texto:
        return (
            f"{host} no respondio a tiempo. Puede estar caido o muy lento -- al "
            f"sitio de la CNV le pasa seguido.\n"
            f"  Si se repite en varias corridas, probar abrirlo en el navegador."
        )

    # 4. Certificado.
    if "certificate" in texto or "ssl" in texto:
        return (
            f"Falla de certificado al conectar a {host}.\n"
            f"  Tipico de redes con inspeccion SSL: el proxy corporativo firma\n"
            f"  el trafico con su propio certificado. Hay que instalar el\n"
            f"  certificado de la empresa en el almacen de Python (paquete\n"
            f"  certifi) o usar la variable REQUESTS_CA_BUNDLE apuntando al .pem."
        )

    # 5. Conexion rechazada / sin ruta.
    if "connection refused" in texto or "no route to host" in texto \
            or "connectionreset" in texto or "connection aborted" in texto:
        return (
            f"La conexion a {host} se rechazo o se corto.\n"
            f"  Puede ser un firewall local, un antivirus con inspeccion de red,\n"
            f"  o el sitio cortando la conexion."
        )

    return f"Error de red contra {host}: {causa!r}"


def _causa_raiz(exc: BaseException) -> BaseException:
    """requests envuelve las excepciones en varias capas; interesa el fondo."""
    visto = set()
    actual = exc
    while True:
        if id(actual) in visto:
            return actual
        visto.add(id(actual))
        siguiente = getattr(actual, "reason", None) or actual.__cause__ \
            or actual.__context__
        if siguiente is None or not isinstance(siguiente, BaseException):
            return actual
        actual = siguiente


def chequear_conectividad(url: str) -> list[str]:
    """Prueba capa por capa donde se corta la cosa, y devuelve el informe.

    Se prueba en este orden porque cada paso depende del anterior: si no hay
    DNS no hay nada que discutir sobre el proxy, y si no hay internet no hay
    nada que discutir sobre DNS.
    """
    host = urlparse(url).hostname or url
    lineas: list[str] = []

    # 1. Hay internet? Se resuelve un dominio que anda en todos lados.
    try:
        socket.getaddrinfo("www.google.com", 443)
        lineas.append("[ok]    DNS general funciona (resuelve www.google.com)")
        hay_dns = True
    except OSError:
        lineas.append("[FALLA] No se resuelve ni www.google.com: la maquina "
                      "esta sin internet o sin DNS.")
        lineas.append("        Probar 'ping 8.8.8.8'. Si tampoco anda, no hay red.")
        return lineas

    # 2. Se resuelve este host en particular?
    try:
        info = socket.getaddrinfo(host, 443)
        ips = sorted({i[4][0] for i in info})
        lineas.append(f"[ok]    {host} resuelve a {', '.join(ips[:3])}")
        resuelve = True
    except OSError as exc:
        resuelve = False
        lineas.append(f"[FALLA] {host} NO resuelve ({exc.strerror or exc}).")
        if hay_dns:
            lineas.append("        Internet anda pero este dominio no resuelve:")
            lineas.append("        es un bloqueo del DNS de la empresa, o la salida")
            lineas.append("        pasa por un proxy que hay que declarar.")
            lineas.append(f"        Comparar:  nslookup {host}")
            lineas.append(f"                   nslookup {host} 8.8.8.8")

    # 3. Se puede abrir el puerto?
    if resuelve:
        try:
            with socket.create_connection((host, 443), timeout=8):
                lineas.append(f"[ok]    Se abre la conexion TCP a {host}:443")
        except OSError as exc:
            lineas.append(f"[FALLA] No se puede conectar a {host}:443 "
                          f"({exc.strerror or exc}).")
            lineas.append("        Firewall, antivirus con inspeccion de red, o")
            lineas.append("        salida bloqueada. Probable que haga falta proxy.")

    # 4. Hay proxy declarado?
    p = _proxies()
    sistema = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
    if p:
        lineas.append(f"[info]  Proxy declarado para el bot: {_tapar(p['https'])}")
    elif sistema:
        lineas.append(f"[info]  Proxy del sistema: {_tapar(sistema)}")
    else:
        lineas.append("[info]  No hay proxy declarado.")
        lineas.append("        Si el navegador entra al sitio y el bot no, casi")
        lineas.append("        seguro hay un proxy que Chrome usa por politica y")
        lineas.append("        Python no ve. Buscarlo en Windows en:")
        lineas.append("        Configuracion > Red e Internet > Proxy,")
        lineas.append(f"        y declararlo con:  set {ENV_PROXY}=http://host:puerto")

    return lineas


# ---------------------------------------------------------------------------
# Descarga
# ---------------------------------------------------------------------------

def get(
    url: str,
    *,
    headers: dict | None = None,
    timeout: int = 30,
    intentos: int = 3,
    backoff: float = 2.0,
    como_json: bool = False,
) -> Any:
    """GET con reintentos. Devuelve el texto, o el JSON parseado.

    Reintenta lo transitorio (timeouts, 5xx, cortes). No reintenta lo que no
    va a cambiar: un 4xx, o un DNS que no resuelve -- ahi solo se pierde
    tiempo, y en una tarea programada cada segundo de mas cuenta.
    """
    cabeceras = {**HEADERS_BASE, **(headers or {})}
    proxies = _proxies()
    ultimo: Exception | None = None

    for intento in range(1, intentos + 1):
        try:
            resp = requests.get(url, headers=cabeceras, timeout=timeout,
                                proxies=proxies)

            if 400 <= resp.status_code < 500:
                raise ErrorDeRed(
                    f"{url} respondio {resp.status_code}. "
                    + ("Suele significar que cambio el formato de la consulta."
                       if resp.status_code == 400 else
                       "El sitio o el proxy estan rechazando el pedido.")
                )

            resp.raise_for_status()

            if como_json:
                return resp.json()

            # Varios sitios locales no declaran bien el charset y los acentos
            # salen rotos; se detecta por contenido.
            if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text

        except ErrorDeRed:
            raise
        except requests.RequestException as exc:
            ultimo = exc
            causa = str(_causa_raiz(exc)).lower()
            # Un DNS que no resuelve no se arregla reintentando.
            if "getaddrinfo" in causa or "failed to resolve" in causa \
                    or "name or service not known" in causa:
                raise ErrorDeRed(explicar(exc, url)) from exc
            if intento < intentos:
                espera = backoff ** intento
                log.warning("Intento %d/%d contra %s fallo. Reintento en %.0fs.",
                            intento, intentos, urlparse(url).hostname, espera)
                time.sleep(espera)
        except ValueError as exc:                 # JSON invalido
            ultimo = exc
            if intento < intentos:
                time.sleep(backoff ** intento)

    raise ErrorDeRed(explicar(ultimo, url) if ultimo else f"No se pudo bajar {url}")
