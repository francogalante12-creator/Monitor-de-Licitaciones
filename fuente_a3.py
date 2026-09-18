"""
Cliente de la API de licitaciones de A3 Mercados (ex MAE).

Endpoint descubierto por inspeccion de red en marketdata.mae.com.ar:

    GET https://api.marketdata.mae.com.ar/api/mercado/licitacionesporestado/Todos
        ?oTitulo={"estado":"A","fechaDesde":"YYYY-MM-DD","fechaHasta":"YYYY-MM-DD"}

Sin autenticacion. Notas de comportamiento verificadas empiricamente
(2026-09-10) -- leer antes de tocar este archivo:

1. Los parametros fechaDesde / fechaHasta SE IGNORAN. La API siempre
   devuelve una ventana movil de aproximadamente los ultimos 30 dias
   habiles, sin importar el rango pedido. Se mandan igual porque el
   endpoint devuelve 400 si falta el objeto oTitulo.

   Consecuencia operativa: el bot no puede pedir historia vieja. Si deja
   de correr mas de 30 dias, ese hueco se pierde para siempre. Por eso
   el historico se acumula en el estado local, que nunca se poda.

2. El campo "estado" del payload SIEMPRE dice "Activa", incluso en
   licitaciones finalizadas hace un mes. Es un campo roto: NO USARLO.
   El estado real es el parametro con el que se consulto (A / C / F).
   Por eso cada registro se marca con _estado_real al momento de traerlo.

3. El unico valor aceptado en el segmento del path es "Todos". Cualquier
   otro (por ejemplo un tipo de instrumento) devuelve 400.

4. El campo "id" es negativo y estable a lo largo del ciclo de vida de la
   licitacion: la misma licitacion conserva su id al pasar de A a F. Es
   una clave primaria confiable. Los ids son mas negativos cuanto mas
   reciente es la licitacion.

5. Los tres estados NO son disjuntos: en la muestra del 10/09/2026, 3 de 84
   registros aparecieron en dos buckets a la vez (tipico de una licitacion
   que cierra mientras corre la consulta). Hay que deduplicar por id y
   quedarse con el estado mas avanzado del ciclo de vida.

6. "existeArchivo" viene en 0 y "archivos" vacio en toda la muestra
   observada: la API no expone los PDF de los avisos de suscripcion.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import red

log = logging.getLogger(__name__)

BASE_URL = "https://api.marketdata.mae.com.ar/api/mercado/licitacionesporestado/Todos"

# Los tres buckets de estado que expone el grid del sitio.
ESTADOS = {
    "A": "Activa",
    "C": "Cancelada/Suspendida",
    "F": "Finalizada",
}

# Cuando un mismo id aparece en dos buckets (ver nota 5), gana el estado mas
# avanzado: una licitacion que ya finalizo o se cancelo no sigue "activa".
PRIORIDAD_ESTADO = {"A": 0, "C": 1, "F": 2}

# El sitio principal esta detras de Imperva/Incapsula; el subdominio de la API
# no lo estaba al momento de escribir esto (responde a un cliente HTTP comun),
# pero mandar cabeceras de navegador cuesta nada y evita que un endurecimiento
# del WAF rompa el bot de un dia para el otro. El User-Agent lo pone red.py.
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://marketdata.mae.com.ar",
    "Referer": "https://marketdata.mae.com.ar/licitaciones",
}


class FuenteNoDisponible(RuntimeError):
    """La API no respondio o respondio algo que no es la lista esperada."""


def _get_con_reintentos(url: str) -> Any:
    """Consulta la API. Los reintentos y el diagnostico de red viven en red.py."""
    try:
        return red.get(url, headers=HEADERS, como_json=True, intentos=3)
    except red.ErrorDeRed as exc:
        raise FuenteNoDisponible(str(exc)) from exc


def _url_para(estado: str, dias_ventana: int = 45) -> str:
    """Arma la URL. Las fechas se ignoran del lado del servidor (ver nota 1),
    pero el objeto tiene que estar bien formado o la API devuelve 400."""
    hasta = date.today()
    desde = hasta - timedelta(days=dias_ventana)
    o_titulo = json.dumps(
        {
            "estado": estado,
            "fechaDesde": desde.isoformat(),
            "fechaHasta": hasta.isoformat(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"{BASE_URL}?oTitulo={quote(o_titulo)}"


def traer_licitaciones(estados: tuple[str, ...] = ("A", "C", "F")) -> list[dict]:
    """Trae las licitaciones de los estados pedidos y las normaliza.

    Devuelve una lista de dicts crudos de la API mas tres campos agregados:
      _estado_real  -- "Activa" / "Cancelada/Suspendida" / "Finalizada"
      _estado_cod   -- "A" / "C" / "F"
      _consultado   -- timestamp ISO UTC de esta corrida
    """
    ahora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    por_id: dict[int, dict] = {}
    errores: list[str] = []

    for cod in estados:
        try:
            datos = _get_con_reintentos(_url_para(cod))
        except FuenteNoDisponible as exc:
            # Que falle un bucket no debe tirar abajo la corrida entera:
            # perder las canceladas es molesto, perder las activas es grave.
            # El detalle va una sola vez al final: los tres estados salen por
            # la misma conexion, asi que cuando falla uno suelen fallar los
            # tres con el mismo motivo, y repetirlo tres veces solo tapa el log.
            errores.append(str(exc))
            log.error("No se pudo traer el estado %s (%s).", cod, ESTADOS[cod])
            log.debug("Detalle del fallo en %s: %s", cod, exc)
            continue

        if not isinstance(datos, list):
            errores.append(f"{cod}: la API devolvio {type(datos).__name__}, no una lista")
            continue

        for reg in datos:
            if not isinstance(reg, dict) or "id" not in reg:
                continue
            reg["_estado_real"] = ESTADOS[cod]
            reg["_estado_cod"] = cod
            reg["_consultado"] = ahora
            # Un id puede venir en dos buckets a la vez: gana el estado mas
            # avanzado del ciclo de vida (F > C > A).
            previo = por_id.get(reg["id"])
            if previo is None or (
                PRIORIDAD_ESTADO[cod] >= PRIORIDAD_ESTADO[previo["_estado_cod"]]
            ):
                por_id[reg["id"]] = reg

        log.info("Estado %s (%s): %d licitaciones.", cod, ESTADOS[cod], len(datos))

    if not por_id and errores:
        # dict.fromkeys mantiene el orden y saca los repetidos.
        unicos = list(dict.fromkeys(errores))
        if len(unicos) == 1:
            raise FuenteNoDisponible(unicos[0])
        raise FuenteNoDisponible(
            "Ningun estado se pudo consultar:\n  " + "\n  ".join(unicos)
        )

    return list(por_id.values())
