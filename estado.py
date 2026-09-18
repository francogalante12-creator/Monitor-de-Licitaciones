"""
Persistencia del estado y deteccion de novedades entre corridas.

El estado es un unico JSON con todas las licitaciones vistas alguna vez,
indexadas por id. Nunca se poda: la API solo devuelve una ventana movil de
30 dias, asi que este archivo es la unica memoria larga del sistema.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

# v1: indexado por el id numerico de A3.
# v2: indexado por "fuente:id", desde que hay mas de una fuente.
VERSION_ESTADO = 2

# Eventos que el bot sabe reconocer.
EV_NUEVA = "nueva"
EV_RESULTADO = "resultado"
EV_CANCELADA = "cancelada"
EV_MODIFICADA = "modificada"

ETIQUETAS_EVENTO = {
    EV_NUEVA: "Nueva licitacion",
    EV_RESULTADO: "Resultado de adjudicacion",
    EV_CANCELADA: "Cancelada o suspendida",
    EV_MODIFICADA: "Datos modificados",
}

# Campos cuyo cambio se considera una modificacion digna de aviso. Se dejan
# afuera los de puro ruido (consultado, fecha_modificacion) y los derivados.
CAMPOS_VIGILADOS = (
    "estado",
    "titulo",
    "monto_licitar",
    "monto_adjudicado",
    "moneda",
    "valor_corte",
    "fecha_inicio",
    "fecha_fin",
    "fecha_liquidacion",
    "fecha_vencimiento",
    "ampliable_hasta",
    "colocador",
    "observaciones",
)

ETIQUETAS_CAMPO = {
    "estado": "Estado",
    "titulo": "Titulo",
    "monto_licitar": "Monto a licitar",
    "monto_adjudicado": "Monto adjudicado",
    "moneda": "Moneda",
    "valor_corte": "Valor de corte",
    "fecha_inicio": "Inicio",
    "fecha_fin": "Cierre",
    "fecha_liquidacion": "Liquidacion",
    "fecha_vencimiento": "Vencimiento",
    "ampliable_hasta": "Ampliable hasta",
    "colocador": "Colocadores",
    "observaciones": "Observaciones",
}


class _Encoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, datetime):
            return o.isoformat()
        return super().default(o)


def _revivir_fechas(reg: dict) -> dict:
    """Vuelve a convertir en datetime los campos de fecha al leer del JSON."""
    for clave, valor in reg.items():
        if clave.startswith("fecha_") and isinstance(valor, str) and valor:
            try:
                reg[clave] = datetime.fromisoformat(valor)
            except ValueError:
                reg[clave] = None
    return reg


def _migrar_v1(licitaciones: dict) -> dict[str, dict]:
    """El estado v1 estaba indexado por el id numerico de A3, cuando esa era
    la unica fuente. Se reindexa a "a3:<id>" y se completan los campos que el
    modelo agrego despues, para no perder el historico acumulado."""
    salida = {}
    for k, reg in licitaciones.items():
        reg.setdefault("fuente", "a3")
        reg.setdefault("enlace", "")
        clave = reg.get("clave") or f"a3:{reg.get('id', k)}"
        reg["clave"] = clave
        salida[clave] = reg
    log.info("Estado migrado de v1 a v2: %d licitaciones reindexadas.", len(salida))
    return salida


def cargar(ruta: str | Path) -> dict[str, dict]:
    """Lee el estado previo. Devuelve {} si es la primera corrida."""
    ruta = Path(ruta)
    if not ruta.exists():
        log.info("No hay estado previo en %s: primera corrida.", ruta)
        return {}

    try:
        with ruta.open(encoding="utf-8") as fh:
            crudo = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        # Un estado corrupto no debe frenar el monitoreo, pero tampoco hay
        # que pisarlo en silencio: se hace copia y se arranca de cero.
        respaldo = ruta.with_suffix(ruta.suffix + ".corrupto")
        log.error("Estado ilegible (%s). Lo muevo a %s y arranco de cero.", exc, respaldo)
        try:
            ruta.replace(respaldo)
        except OSError:
            pass
        return {}

    licitaciones = {
        k: _revivir_fechas(v) for k, v in crudo.get("licitaciones", {}).items()
    }

    if int(crudo.get("version", 1)) < 2:
        return _migrar_v1(licitaciones)

    return licitaciones


def guardar(ruta: str | Path, licitaciones: dict[str, dict]) -> None:
    """Guarda el estado de forma atomica.

    Se escribe a un temporal en el mismo directorio y se reemplaza, para que
    una interrupcion (o un runner de CI que se corta) no deje el archivo a
    medio escribir y pierda todo el historico.
    """
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "version": VERSION_ESTADO,
        "actualizado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "licitaciones": {str(k): v for k, v in licitaciones.items()},
    }

    fd, tmp = tempfile.mkstemp(dir=str(ruta.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, cls=_Encoder, ensure_ascii=False, indent=1)
        os.replace(tmp, ruta)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

    log.info("Estado guardado: %d licitaciones en %s.", len(licitaciones), ruta)


def _valor_comparable(valor: Any) -> Any:
    if isinstance(valor, datetime):
        return valor.isoformat()
    return valor


def _diferencias(previo: dict, actual: dict) -> list[dict]:
    cambios = []
    for campo in CAMPOS_VIGILADOS:
        antes = _valor_comparable(previo.get(campo))
        ahora = _valor_comparable(actual.get(campo))
        if antes != ahora:
            cambios.append({
                "campo": campo,
                "etiqueta": ETIQUETAS_CAMPO.get(campo, campo),
                "antes": previo.get(campo),
                "ahora": actual.get(campo),
            })
    return cambios


def _tiene_resultado(reg: dict) -> bool:
    """Una licitacion tiene resultado publicado si finalizo y ademas informo
    monto adjudicado, un valor de corte con numero, o quedo desierta.

    El caso "desierta" hay que contemplarlo aparte: no tiene monto ni tasa,
    asi que sin esto el bot nunca la daria por resuelta y se quedaria
    esperando un resultado que no va a existir.
    """
    if reg.get("estado_cod") != "F":
        return False
    return (
        bool(reg.get("monto_adjudicado"))
        or reg.get("tasa_corte") is not None
        or bool(reg.get("desierta"))
    )


def detectar_novedades(
    previas: dict[str, dict],
    actuales: Iterable[dict],
) -> list[dict]:
    """Compara la corrida actual contra el estado guardado.

    Devuelve una lista de eventos: {"evento", "etiqueta", "licitacion",
    "cambios"}. Una misma licitacion puede generar como mucho un evento por
    corrida -- se elige el mas importante, para no mandar tres avisos de lo
    mismo en el mismo mail.
    """
    eventos: list[dict] = []

    for reg in actuales:
        previo = previas.get(reg["clave"])

        if previo is None:
            eventos.append({
                "evento": EV_NUEVA,
                "etiqueta": ETIQUETAS_EVENTO[EV_NUEVA],
                "licitacion": reg,
                "cambios": [],
            })
            continue

        cambios = _diferencias(previo, reg)
        if not cambios:
            continue

        cod_previo = previo.get("estado_cod")
        cod_actual = reg.get("estado_cod")

        if cod_actual == "C" and cod_previo != "C":
            evento = EV_CANCELADA
        elif _tiene_resultado(reg) and not _tiene_resultado(previo):
            evento = EV_RESULTADO
        else:
            evento = EV_MODIFICADA

        eventos.append({
            "evento": evento,
            "etiqueta": ETIQUETAS_EVENTO[evento],
            "licitacion": reg,
            "cambios": cambios,
        })

    # Orden de lectura del mail: primero lo que abre, despues lo que cierra.
    prioridad = {EV_NUEVA: 0, EV_RESULTADO: 1, EV_CANCELADA: 2, EV_MODIFICADA: 3}
    eventos.sort(key=lambda e: (
        prioridad[e["evento"]],
        e["licitacion"].get("categoria", ""),
        e["licitacion"].get("titulo", ""),
    ))
    return eventos


def fusionar(previas: dict[str, dict], actuales: Iterable[dict]) -> dict[str, dict]:
    """Estado nuevo = todo lo que habia + lo que llego ahora.

    Las licitaciones que ya salieron de la ventana de 30 dias de la API se
    conservan tal como se vieron por ultima vez.
    """
    fusionado = dict(previas)
    for reg in actuales:
        fusionado[reg["clave"]] = reg
    return fusionado
