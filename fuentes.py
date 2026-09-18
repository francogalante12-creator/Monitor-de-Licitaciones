"""
Registro de fuentes.

Este es el unico lugar del bot que sabe que las fuentes existen. Todo lo que
viene despues -- deteccion de novedades, mail, planilla, dashboard -- trabaja
sobre el modelo normalizado y no distingue de donde salio cada registro.

Para sumar una fuente nueva alcanza con:
  1. escribir un modulo con una funcion traer_licitaciones() que devuelva
     una lista de dicts crudos con las claves que espera modelo.normalizar(),
  2. agregarlo a FUENTES,
  3. habilitarlo en config.yaml.

Ningun otro archivo se toca.
"""

from __future__ import annotations

import logging
from typing import Callable, NamedTuple

import fuente_a3
import fuente_cnv

log = logging.getLogger(__name__)


class Fuente(NamedTuple):
    clave: str
    nombre: str
    traer: Callable[..., list[dict]]
    descripcion: str
    url: str          # a que host apuntar el chequeo de conectividad


FUENTES: dict[str, Fuente] = {
    "a3": Fuente(
        clave="a3",
        nombre="A3 Mercados",
        traer=fuente_a3.traer_licitaciones,
        descripcion="Grilla del mercado primario: la licitacion con todos sus "
                    "datos, el dia que abre.",
        url=fuente_a3.BASE_URL,
    ),
    "cnv": Fuente(
        clave="cnv",
        nombre="CNV / AIF",
        traer=fuente_cnv.traer_licitaciones,
        descripcion="Hechos relevantes: el aviso, uno a tres dias antes, sin "
                    "monto ni tasa.",
        url=fuente_cnv.URL_HECHOS,
    ),
}

# Errores que cuentan como "esta fuente no anduvo" y no deben tirar abajo la
# corrida entera.
ERRORES_DE_FUENTE = (fuente_a3.FuenteNoDisponible, fuente_cnv.FuenteNoDisponible)


class NingunaFuenteDisponible(RuntimeError):
    pass


def traer_todo(habilitadas: list[str] | tuple[str, ...]) -> tuple[list[dict], list[str]]:
    """Consulta las fuentes pedidas y devuelve (registros, errores).

    Si una fuente falla, se sigue con las demas: quedarse sin la CNV es
    perder anticipacion, quedarse sin A3 es perder el grueso, pero perder las
    dos por culpa de una sola seria peor que cualquiera de las dos.
    Solo se levanta excepcion si no anduvo ninguna.
    """
    registros: list[dict] = []
    errores: list[str] = []
    anduvo_alguna = False

    for clave in habilitadas:
        fuente = FUENTES.get(clave)
        if fuente is None:
            errores.append(f"'{clave}' no es una fuente conocida "
                           f"(las que hay: {', '.join(FUENTES)})")
            continue

        try:
            crudos = fuente.traer()
        except ERRORES_DE_FUENTE as exc:
            errores.append(f"{fuente.nombre}: {exc}")
            log.error("La fuente %s no respondio: %s", fuente.nombre, exc)
            continue
        except Exception as exc:                      # noqa: BLE001
            # Un parser roto tira cualquier cosa; no puede matar la corrida.
            errores.append(f"{fuente.nombre}: error inesperado ({exc!r})")
            log.exception("Error inesperado en la fuente %s.", fuente.nombre)
            continue

        anduvo_alguna = True
        for reg in crudos:
            reg.setdefault("_fuente", clave)
        registros.extend(crudos)
        log.info("%s: %d registros.", fuente.nombre, len(crudos))

    if not anduvo_alguna:
        raise NingunaFuenteDisponible(
            "Ninguna fuente se pudo consultar:\n  " + "\n  ".join(errores)
        )

    return registros, errores
