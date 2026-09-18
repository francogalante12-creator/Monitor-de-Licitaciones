#!/usr/bin/env python3
"""
Prueba end-to-end sin tocar la red ni mandar mail.

Usa un fixture con registros reales de la API (copiados tal cual de una
consulta del 10/09/2026, incluidas sus rarezas: espacios de sobra, guiones
distintos, fechas nulas 0001-01-01, estado "Activa" en licitaciones ya
finalizadas). Simula dos corridas consecutivas y verifica que se detecte lo
que tiene que detectarse y nada mas.

    python autotest.py
"""

from __future__ import annotations

import copy
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import estado as est
import notificar
import salidas
from modelo import (
    CAT_FF, CAT_ON_CORP, CAT_ON_PYME, CAT_ON_SOST, CAT_PUBLICA,
    categorizar, extraer_tasa, normalizar_todos, parse_fecha,
)
from formato import formato_ar, monto_corto

FALLAS: list[str] = []


def check(condicion: bool, descripcion: str, detalle: str = "") -> None:
    if condicion:
        print(f"  ok    {descripcion}")
    else:
        print(f"  FALLA {descripcion}" + (f"  -> {detalle}" if detalle else ""))
        FALLAS.append(descripcion)


# ---------------------------------------------------------------------------
# Fixture: registros reales de api.marketdata.mae.com.ar
# ---------------------------------------------------------------------------

def _reg(**kw):
    base = {
        "fechaInicio": "2026-09-03T13:00:00Z",
        "fechaFin": "2026-09-03T19:00:00Z",
        "fechaLiquidacion": "2026-09-04T03:00:00Z",
        "fechaVencimiento": "0001-01-01T00:00:00",
        "fechaVencimientoEspecie": "0001-01-01T00:00:00",
        "titulo": "", "emisor": "", "industria": "", "moneda": "ARS PESOS",
        "ampliableHasta": "NO AMPLIABLE", "variableLicitar": "", "montoaLicitar": 0,
        "rueda": "", "modalidad": "Abierta", "liquidador": "Clear",
        "estado": "Activa", "tipo": "", "colocador": "", "observaciones": "",
        "monto_Adjudicado": 0, "sistema_Adjudicacion": "Holandes",
        "valor_Corte": "", "duration": "", "id": 0, "existeArchivo": 0,
        "comentario": "", "fechaModificacion": "2026-09-03T21:00:00Z",
        "monedaMonto": "ARS PESOS", "plazoEspecie": "", "archivos": [],
    }
    base.update(kw)
    return base


CORRIDA_1 = [
    # ON corporativa grande, todavia abierta.
    _reg(id=-940, titulo="ON MIRGOR CLASE VII", emisor="MIRGOR S.A.C.I.F.I.A.",
         tipo="Privada - ON", montoaLicitar=30000000, moneda="USD",
         monedaMonto="USD", fechaVencimientoEspecie="2029-09-08T03:00:00Z",
         _bucket="A"),
    # ON PyME CNV: el terreno de las SGR.
    _reg(id=-935, titulo="ON PYME CNV MEDIANO IMPACTO SION SERIE V",
         emisor="SION S.A. ", tipo="Privada - ON/VCP MEDIANO IMPACTO",
         montoaLicitar=1500000000, fechaVencimientoEspecie="2028-09-03T03:00:00Z",
         _bucket="A"),
    # Fideicomiso, con raya larga en el tipo y valor de corte multi-tramo.
    _reg(id=-899, titulo="FF MEDIANO IMPACTO AMPLIADO GOCREDITOS SERIE 1",
         emisor="TMF Trust Company (Argentina) S.A.",
         tipo="Privada – FF MEDIANO IMPACTO", montoaLicitar=5343227882,
         colocador="STONEX SECURITIES S.A. ",
         valor_Corte="MARGEN DE CORTE VDFA:   % / PRECIO DE CORTE CP: %",
         observaciones="Se ofrecen Valores Fiduciarios por los siguientes V/N  ",
         _bucket="A"),
    # Deuda publica ya finalizada, con resultado.
    _reg(id=-820, titulo="LETRA DEL TESORO NACIONAL CAPITALIZABLE EN PESOS",
         emisor="MINISTERIO DE ECONOMIA", tipo="Publica",
         montoaLicitar=0, monto_Adjudicado=1234567890123,
         valor_Corte="TASA DE CORTE: 2,85%", _bucket="F"),
    # ON sostenible.
    _reg(id=-861, titulo="ON BAJO IMPACTO SVS ASOCIACION CIVIL SUMATORIA CLASE IX",
         emisor="ASOCIACION CIVIL SUMATORIA", tipo="Privada - ON VS/SVS",
         montoaLicitar=800000000, _bucket="F",
         monto_Adjudicado=800000000, valor_Corte="TASA DE CORTE: 24.50%"),
]

# Segunda corrida: Mirgor cerro con resultado, SION se cancelo, aparece una ON
# nueva de Genneia, y al FF le corrigieron el monto.
CORRIDA_2 = copy.deepcopy(CORRIDA_1)
for r in CORRIDA_2:
    if r["id"] == -940:
        r["_bucket"] = "F"
        r["monto_Adjudicado"] = 50000000
        r["valor_Corte"] = "TASA DE CORTE: 7,25%"
        r["fechaModificacion"] = "2026-09-03T22:10:00Z"
    elif r["id"] == -935:
        r["_bucket"] = "C"
    elif r["id"] == -899:
        r["montoaLicitar"] = 5500000000
CORRIDA_2.append(
    _reg(id=-951, titulo="ON GENNEIA CLASE XLV", emisor="GENNEIA S.A.",
         tipo="Privada - ON", montoaLicitar=100000000, moneda="USD",
         monedaMonto="USD", fechaInicio="2026-09-10T13:00:00Z",
         fechaFin="2026-09-10T19:00:00Z", _bucket="A")
)


def preparar(registros):
    """Emula lo que hace fuente_a3.traer_licitaciones con el campo _bucket."""
    salida = []
    for r in registros:
        r = dict(r)
        cod = r.pop("_bucket", "A")
        r["_estado_cod"] = cod
        r["_estado_real"] = {"A": "Activa", "C": "Cancelada/Suspendida",
                             "F": "Finalizada"}[cod]
        r["_consultado"] = "2026-09-10T15:00:00+00:00"
        salida.append(r)
    return salida


# ---------------------------------------------------------------------------

def probar_categorias():
    print("\nCategorizacion")
    check(categorizar("Privada - ON") == CAT_ON_CORP, "'Privada - ON' -> ON Corporativa")
    check(categorizar("Privada - ON/VCP BAJO IMPACTO") == CAT_ON_PYME,
          "'ON/VCP BAJO IMPACTO' -> ON PyME CNV")
    check(categorizar("Privada - ON/VCP MEDIANO IMPACTO") == CAT_ON_PYME,
          "'ON/VCP MEDIANO IMPACTO' -> ON PyME CNV")
    check(categorizar("Privada - ON VS/SVS") == CAT_ON_SOST,
          "'ON VS/SVS' -> ON Sostenible")
    check(categorizar("Privada - FF") == CAT_FF, "'Privada - FF' -> Fideicomiso")
    # El caso que rompe una implementacion ingenua: raya larga + "MEDIANO
    # IMPACTO", que matchearia ON PyME si no se desempata por FF.
    check(categorizar("Privada – FF MEDIANO IMPACTO") == CAT_FF,
          "'Privada [raya] FF MEDIANO IMPACTO' -> Fideicomiso, no ON PyME")
    check(categorizar("Publica") == CAT_PUBLICA, "'Publica' -> Deuda Publica")
    check(categorizar("Pública") == CAT_PUBLICA, "'Publica' con acento -> Deuda Publica")


def probar_parseo():
    print("\nParseo de campos")
    check(parse_fecha("0001-01-01T00:00:00") is None,
          "la fecha nula 0001-01-01 se lee como None")
    check(parse_fecha("2026-09-03T13:00:00Z").hour == 13, "timestamp con Z se lee en UTC")
    check(extraer_tasa("TASA DE CORTE: 7,25%") == 7.25, "tasa con coma decimal")
    check(extraer_tasa("MARGEN DE CORTE: -0.10%  ") == -0.10, "margen negativo")
    check(extraer_tasa("MARGEN DE CORTE VDFA:   % / PRECIO CP: %") is None,
          "corte sin numero -> None")
    check(extraer_tasa("Ninguno") is None, "'Ninguno' -> None")
    check(formato_ar(1234567.89) == "1.234.567,89", "formato argentino de numero")
    check(monto_corto(5343227882, "ARS PESOS") == "$ 5,3 MM", "monto corto en pesos",
          monto_corto(5343227882, "ARS PESOS"))
    check(monto_corto(30000000, "USD") == "US$ 30,0 M", "monto corto en dolares",
          monto_corto(30000000, "USD"))


def probar_normalizacion():
    print("\nNormalizacion")
    regs = normalizar_todos(preparar(CORRIDA_1))
    porid = {r["id"]: r for r in regs}
    check(len(regs) == 5, "se normalizan las 5 licitaciones")
    check(porid[-935]["emisor"] == "SION S.A.", "se limpian los espacios de sobra")
    check(porid[-820]["estado"] == "Finalizada",
          "el estado sale del bucket consultado, no del campo roto de la API",
          porid[-820]["estado"])
    check(porid[-940]["categoria"] == CAT_ON_CORP, "Mirgor queda como ON Corporativa")
    check(porid[-940]["fecha_vencimiento"] is not None, "se lee el vencimiento de la especie")
    check(porid[-899]["fecha_vencimiento"] is None,
          "vencimiento nulo queda None y no como el ano 1")
    check(regs[0]["fecha_inicio"] >= regs[-1]["fecha_inicio"],
          "vienen ordenadas de mas reciente a mas vieja")


def probar_deteccion():
    print("\nDeteccion de novedades")
    c1 = normalizar_todos(preparar(CORRIDA_1))
    c2 = normalizar_todos(preparar(CORRIDA_2))

    # Primera corrida: todo es nuevo.
    ev1 = est.detectar_novedades({}, c1)
    check(len(ev1) == 5, "en la primera corrida todo aparece como nuevo")
    check(all(e["evento"] == est.EV_NUEVA for e in ev1), "todos con evento 'nueva'")

    previas = est.fusionar({}, c1)

    # Corrida sin cambios: silencio absoluto. Es la condicion mas importante:
    # un bot que avisa lo mismo cada hora se termina filtrando a la papelera.
    ev_igual = est.detectar_novedades(previas, c1)
    check(len(ev_igual) == 0, "una corrida sin cambios no genera ningun evento",
          str([e["licitacion"]["titulo"] for e in ev_igual]))

    # Segunda corrida.
    ev2 = est.detectar_novedades(previas, c2)
    por_evento = {}
    for e in ev2:
        por_evento.setdefault(e["evento"], []).append(e["licitacion"]["id"])

    check(por_evento.get(est.EV_NUEVA) == [-951], "detecta la ON nueva de Genneia",
          str(por_evento))
    check(por_evento.get(est.EV_RESULTADO) == [-940],
          "detecta el resultado de adjudicacion de Mirgor", str(por_evento))
    check(por_evento.get(est.EV_CANCELADA) == [-935],
          "detecta la cancelacion de SION", str(por_evento))
    check(por_evento.get(est.EV_MODIFICADA) == [-899],
          "detecta el cambio de monto del FF", str(por_evento))
    check(len(ev2) == 4, "no inventa eventos de mas", str(len(ev2)))

    cambio_ff = next(e for e in ev2 if e["licitacion"]["id"] == -899)
    check(any(c["campo"] == "monto_licitar" for c in cambio_ff["cambios"]),
          "el evento de modificacion dice que cambio el monto")

    # Un resultado ya avisado no se vuelve a avisar.
    previas2 = est.fusionar(previas, c2)
    check(len(est.detectar_novedades(previas2, c2)) == 0,
          "un resultado ya avisado no se repite en la corrida siguiente")


def probar_casos_reales():
    """Casos que aparecieron al validar contra los 84 registros reales de la
    API el 10/09/2026 y que una implementacion ingenua se come."""
    print("\nCasos reales de la API")
    from fuente_a3 import PRIORIDAD_ESTADO, ESTADOS
    from modelo import es_desierta, normalizar

    # 3 de 84 registros venian en dos buckets a la vez.
    check(PRIORIDAD_ESTADO["F"] > PRIORIDAD_ESTADO["C"] > PRIORIDAD_ESTADO["A"],
          "ante un id duplicado gana el estado mas avanzado (F > C > A)")

    # Emulacion del bucle de deduplicacion de traer_licitaciones.
    por_id = {}
    for cod in ("A", "C", "F"):
        reg = {"id": -777, "_estado_cod": cod, "_estado_real": ESTADOS[cod]}
        previo = por_id.get(-777)
        if previo is None or PRIORIDAD_ESTADO[cod] >= PRIORIDAD_ESTADO[previo["_estado_cod"]]:
            por_id[-777] = reg
    check(por_id[-777]["_estado_real"] == "Finalizada",
          "un id que aparece en A, C y F queda como Finalizada",
          por_id[-777]["_estado_real"])

    # "DESIERTA" viene escrito en valor_Corte, no en un campo propio.
    check(es_desierta("DESIERTA"), "reconoce una licitacion desierta")
    check(es_desierta("  desierta  "), "la reconoce con espacios y minusculas")
    check(not es_desierta("TASA DE CORTE: 7,25%"), "no confunde una adjudicada")

    desierta = normalizar({"id": -1, "titulo": "ON X", "tipo": "Privada - ON",
                           "valor_Corte": "DESIERTA", "_estado_cod": "F",
                           "_estado_real": "Finalizada"})
    check(est._tiene_resultado(desierta),
          "una desierta cuenta como resultado, aunque no tenga monto ni tasa")
    check(desierta["tasa_corte"] is None, "una desierta no inventa una tasa")

    # Precio de corte en dolares por VNO: tiene numeros pero no es una tasa.
    check(extraer_tasa("PRECIO DE CORTE POR CADA VNO USD 1.000: USD 986.00") is None,
          "un precio de corte en USD no se confunde con una tasa")

    # Monedas que existen en la API mas alla de pesos y dolares.
    check(monto_corto(1000000, "UVA").startswith("UVA"), "formatea montos en UVA",
          monto_corto(1000000, "UVA"))
    check(monto_corto(5000000, "USD LINK").startswith("US$ link"),
          "formatea montos dollar-linked", monto_corto(5000000, "USD LINK"))


def probar_filtros():
    print("\nFiltros de aviso")
    from bot import filtrar_para_aviso

    c1 = normalizar_todos(preparar(CORRIDA_1))
    c2 = normalizar_todos(preparar(CORRIDA_2))
    eventos = est.detectar_novedades(est.fusionar({}, c1), c2)

    base = {
        "categorias_aviso": [CAT_ON_CORP, CAT_ON_PYME, CAT_ON_SOST],
        "eventos_aviso": ["nueva", "resultado", "cancelada"],
        "monto_minimo_ars": 0, "emisores_destacados": [],
    }
    sel = filtrar_para_aviso(eventos, base)
    ids = {e["licitacion"]["id"] for e in sel}
    check(ids == {-951, -940, -935},
          "con el filtro por defecto entran las ON y queda afuera el FF", str(ids))

    solo_ff = filtrar_para_aviso(eventos, {**base, "categorias_aviso": [CAT_FF],
                                           "eventos_aviso": ["modificada"]})
    check({e["licitacion"]["id"] for e in solo_ff} == {-899},
          "pidiendo FF y 'modificada' entra solo el fideicomiso")

    # El piso de monto no debe aplicarse a montos en dolares.
    con_piso = filtrar_para_aviso(eventos, {**base, "monto_minimo_ars": 10_000_000_000})
    ids_piso = {e["licitacion"]["id"] for e in con_piso}
    check(-951 in ids_piso,
          "un piso en pesos no filtra una licitacion en dolares", str(ids_piso))
    check(-935 not in ids_piso, "el piso en pesos si filtra la ON PyME chica")

    destacado = filtrar_para_aviso(
        eventos, {**base, "categorias_aviso": [], "emisores_destacados": ["Genneia"]}
    )
    check({e["licitacion"]["id"] for e in destacado} == {-951},
          "un emisor destacado entra aunque su categoria este desactivada")


def probar_filtro_de_moneda():
    print("\nFiltro de moneda")
    import modelo
    from bot import filtrar_por_moneda

    # --- clasificacion ---
    casos = [
        ("USD", modelo.MONEDA_USD),
        ("usd", modelo.MONEDA_USD),
        ("U$S", modelo.MONEDA_USD),
        ("Dolares Estadounidenses", modelo.MONEDA_USD),
        ("ARS PESOS", modelo.MONEDA_ARS),
        ("Pesos", modelo.MONEDA_ARS),
        ("UVA", modelo.MONEDA_UVA),
        # "Dolar linked" tiene la palabra dolar adentro pero se paga en pesos:
        # tiene que ganarle la regla mas especifica.
        ("USD LINKED", modelo.MONEDA_USD_LINKED),
        ("Dolar Linked", modelo.MONEDA_USD_LINKED),
        ("", ""),
        ("-", ""),
    ]
    malos = [(t, modelo.clase_moneda(t), esp) for t, esp in casos
             if modelo.clase_moneda(t) != esp]
    check(not malos, "cada moneda se clasifica en su clase", str(malos))

    # El campo de A3 manda sobre el titulo: un titulo que menciona el dolar de
    # pasada no puede dar vuelta una licitacion en pesos.
    check(modelo.clase_moneda("ARS PESOS", "ON CLASE 5 VINCULADA AL DOLAR")
          == modelo.MONEDA_ARS,
          "el campo de moneda le gana al titulo")
    # Pero si no hay campo -- el caso de la CNV -- el titulo es lo unico que hay.
    check(modelo.clase_moneda("", "AVISO DE SUSCRIPCION ON CLASE 3 EN DOLARES")
          == modelo.MONEDA_USD,
          "sin campo de moneda, el titulo decide")

    # --- filtro ---
    def reg(clase, clave):
        return {"clave": clave, "moneda_clase": clase}

    universo = [reg(modelo.MONEDA_USD, "a3:1"), reg(modelo.MONEDA_ARS, "a3:2"),
                reg(modelo.MONEDA_UVA, "a3:3"),
                reg(modelo.MONEDA_USD_LINKED, "a3:4"), reg("", "cnv:5")]

    solo_usd = {"monedas": [modelo.MONEDA_USD], "moneda_desconocida": "ignorar"}
    quedan, fuera = filtrar_por_moneda(list(universo), solo_usd)
    check({r["clave"] for r in quedan} == {"a3:1"},
          "con USD estricto queda solo la hard dollar",
          str([r["clave"] for r in quedan]))
    check(sum(fuera.values()) == 4, "y las otras cuatro se cuentan como descartadas")

    con_linked = {"monedas": [modelo.MONEDA_USD, modelo.MONEDA_USD_LINKED],
                  "moneda_desconocida": "ignorar"}
    quedan, _ = filtrar_por_moneda(list(universo), con_linked)
    check({r["clave"] for r in quedan} == {"a3:1", "a3:4"},
          "sumando dolar linked entran las dos")

    # El aviso de la CNV sin moneda es el que da la anticipacion: descartarlo
    # por no decir la moneda apagaria la fuente entera.
    por_defecto = {**con_linked, "moneda_desconocida": "avisar"}
    quedan, _ = filtrar_por_moneda(list(universo), por_defecto)
    check("cnv:5" in {r["clave"] for r in quedan},
          "el aviso sin moneda declarada pasa con moneda_desconocida: avisar")
    check("a3:2" not in {r["clave"] for r in quedan},
          "pero los pesos identificados siguen afuera")

    todas, fuera = filtrar_por_moneda(list(universo), {"monedas": []})
    check(len(todas) == 5 and not fuera, "con la lista vacia no filtra nada")

    # La aclaracion "moneda a confirmar" solo tiene sentido cuando hay filtro:
    # es la explicacion de por que esa licitacion entro igual. Sin filtro es
    # ruido, y por eso la marca la pone el filtro y no el modelo.
    quedan, _ = filtrar_por_moneda(list(universo), por_defecto)
    cnv = next(r for r in quedan if r["clave"] == "cnv:5")
    check(cnv.get("moneda_a_confirmar") is True,
          "con filtro puesto, la licitacion sin moneda queda marcada")
    todas, _ = filtrar_por_moneda(list(universo), {"monedas": []})
    check(not any(r.get("moneda_a_confirmar") for r in todas),
          "sin filtro, nada se marca como 'a confirmar'")

    # Y el default de fabrica: todo el mercado primario, sin filtro de moneda.
    import config as cfg_mod
    d = cfg_mod.DEFAULTS
    check(d["monedas"] == [], "por defecto no hay filtro de moneda")
    check(set(d["categorias_aviso"]) == set(modelo.TODAS_LAS_CATEGORIAS),
          "y el aviso cubre todas las categorias, deuda publica incluida",
          str(sorted(set(modelo.TODAS_LAS_CATEGORIAS) - set(d["categorias_aviso"]))))

    # Un estado guardado por una version anterior no tiene el campo. Si se
    # tomara como desconocido, todo el historico en pesos volveria a entrar.
    viejo = [{"clave": "a3:9", "moneda_monto": "ARS PESOS", "titulo": "ON CLASE 1"}]
    quedan, _ = filtrar_por_moneda(viejo, por_defecto)
    check(not quedan,
          "un registro viejo sin el campo se reclasifica, no se da por desconocido")


def probar_calculadora_de_bonos():
    print("\nCalculadora de bonos")
    import bonos

    # --- contra formulas cerradas, que es la unica verificacion que vale ---
    # Un bono a la par rinde la efectiva de su cupon, no la nominal.
    f = bonos.armar_flujos(8.0, 10, "semestral")
    y = bonos.tir(f, 100.0)
    check(abs(y - ((1 + 0.08 / 2) ** 2 - 1) * 100) < 1e-8,
          "un bono a la par rinde la TEA de su cupon, no la TNA", f"{y}")

    # Duration de Macaulay de un bono a la par: (1+i)/i * [1-(1+i)^-n] / m
    i, n, m = 0.04, 20, 2
    cerrada = (1 + i) / i * (1 - (1 + i) ** -n) / m
    calc = bonos.duration(f, y, 100.0)
    check(abs(calc - cerrada) < 1e-9,
          "la duration coincide con la formula cerrada del bono a la par",
          f"{calc} vs {cerrada}")

    # Un cupon cero tiene duration igual a su plazo. Exacto, sin excusas.
    z = bonos.armar_flujos(0.0, 7, "anual")
    yz = bonos.tir(z, 70.0)
    check(abs(bonos.duration(z, yz, 70.0) - 7) < 1e-9,
          "la duration de un cupon cero es su plazo")

    # Descontar a la TIR tiene que devolver el precio de partida.
    malos = [p for p in (85.0, 100.0, 112.5)
             if abs(bonos.valor_presente(f, bonos.tir(f, p)) - p) > 1e-6]
    check(not malos, "el precio a la TIR calculada reproduce el precio de entrada",
          str(malos))

    # --- relaciones que tienen que cumplirse siempre ---
    plan = bonos.amortizacion_lineal(10, "semestral", desde_anio=3)
    amort = bonos.armar_flujos(8.0, 10, "semestral", plan)
    ya = bonos.tir(amort, 100.0)
    check(bonos.duration(amort, ya, 100.0) < bonos.duration(f, y, 100.0),
          "un bono que amortiza tiene menos duration que el bullet al mismo plazo")
    check(abs(sum(x.amortizacion for x in amort) - 100) < 1e-9,
          "el plan de amortizacion devuelve exactamente el 100% del capital")
    check(abs(amort[-1].saldo) < 1e-9, "y el saldo queda en cero al vencimiento")
    check(amort[0].renta > amort[-1].renta,
          "los cupones decrecen cuando el capital amortiza")

    # A mayor plazo, mayor duration; a mayor cupon, menor duration.
    d5 = bonos.analizar(8.0, 5).duration
    d10 = bonos.analizar(8.0, 10).duration
    check(d10 > d5, "mas plazo, mas duration", f"{d5} / {d10}")
    check(bonos.analizar(12.0, 10).duration < d10,
          "mas cupon, menos duration (el capital vuelve antes)")

    # Precio y tasa se mueven al reves, y la convexidad hace que la suba de
    # precio por una baja de tasa sea mayor que la caida simetrica.
    filas = {x["bp"]: x for x in bonos.sensibilidad(f, y)}
    check(filas[-100]["precio"] > 100 > filas[100]["precio"],
          "si baja la tasa sube el precio y al reves")
    check(abs(filas[-300]["variacion"]) > abs(filas[300]["variacion"]),
          "la convexidad hace asimetrica la respuesta a movimientos grandes")

    # --- conversiones de tasa ---
    check(abs(bonos.tna_a_tea(10.0, "semestral") - 10.25) < 1e-9,
          "TNA 10% semestral son 10,25% efectivo")
    check(abs(bonos.tna_a_tea(10.0, "anual") - 10.0) < 1e-9,
          "con pago anual la TNA y la TEA coinciden")
    vueltas = [frec for frec in bonos.FRECUENCIAS
               if abs(bonos.tea_a_tna(bonos.tna_a_tea(10.0, frec), frec) - 10.0) > 1e-9]
    check(not vueltas, "TNA -> TEA -> TNA vuelve al mismo numero", str(vueltas))

    # --- cuando NO hay que calcular ---
    # Esto importa tanto como calcular bien: un numero de mas es peor que un
    # campo vacio, porque parece dato.
    variable = {"valor_corte": "MARGEN DE CORTE: 4.50%", "tasa_corte": 4.5,
                "plazo_anios": 3.0}
    check(bonos.desde_licitacion(variable) is None,
          "un margen de corte (tasa variable) no se calcula")
    check(bonos.es_tasa_variable({"titulo": "ON CLASE 3 BADLAR PRIVADA + 400 BP"}),
          "Badlar en el titulo tambien marca tasa variable")
    fija = {"valor_corte": "TASA DE CORTE: 9.50%", "tasa_corte": 9.5,
            "plazo_anios": 5.0}
    check(bonos.desde_licitacion(fija) is not None, "una tasa de corte fija si")
    check(bonos.desde_licitacion({**fija, "plazo_anios": None}) is None,
          "sin vencimiento no hay flujo que descontar")
    check(bonos.desde_licitacion({**fija, "tasa_corte": None}) is None,
          "sin tasa de corte tampoco")
    check(bonos.resumen_una_linea(None) == "",
          "y el mail no dibuja la fila cuando no hay que opinar")

    # El supuesto de bullet se declara siempre que no haya plan de pagos.
    r = bonos.analizar(9.5, 5.0)
    check(not r.confiable and any("bullet" in s for s in r.supuestos),
          "sin plan de amortizacion el resultado se marca como estimacion")
    r2 = bonos.analizar(9.5, 5.0, amortizaciones=bonos.amortizacion_lineal(5, "semestral"))
    check(r2.confiable, "con el plan de pagos cargado, el resultado es exacto")

    # --- entradas invalidas ---
    for kw, motivo in [
        ({"frecuencia": "quincenal"}, "una frecuencia que no existe"),
        ({"amortizaciones": [50.0, 30.0]}, "un plan que no suma 100%"),
    ]:
        try:
            bonos.armar_flujos(8.0, 5, **kw)
            check(False, f"se rechaza {motivo}")
        except bonos.DatosInsuficientes:
            check(True, f"se rechaza {motivo}")

    # Un plazo de cero o negativo no arma nada.
    try:
        bonos.armar_flujos(8.0, 0)
        check(False, "se rechaza un plazo de cero")
    except bonos.DatosInsuficientes:
        check(True, "se rechaza un plazo de cero")

    _probar_calculadora_del_dashboard()


def _probar_calculadora_del_dashboard():
    """La calculadora del dashboard es un port a JavaScript de bonos.py.

    Dos implementaciones de la misma formula se separan sola: alguien corrige
    un signo de un lado y no del otro, y el dashboard empieza a mostrar
    numeros distintos a los del mail sin que nadie se entere. Esto las corre
    contra los mismos casos y compara.

    Si no hay node instalado no se puede verificar y se dice, en vez de dar
    por buena una comparacion que no se hizo.
    """
    import json
    import shutil
    import subprocess
    import tempfile
    import bonos
    import dashboard

    if not shutil.which("node"):
        check(True, "[sin node: no pude comparar el dashboard contra bonos.py]")
        return

    fuente = dashboard.PLANTILLA
    try:
        js = fuente[fuente.index("function flujosBono"):fuente.index("function calcular()")]
    except ValueError:
        check(False, "encuentro la calculadora dentro de la plantilla del dashboard")
        return

    casos = [
        {"tasa": 8.0, "anios": 10, "m": 2, "precio": 100.0, "gracia": -1},
        {"tasa": 9.5, "anios": 5, "m": 2, "precio": 98.5, "gracia": -1},
        {"tasa": 0.0, "anios": 7, "m": 1, "precio": 70.0, "gracia": -1},
        {"tasa": 12.0, "anios": 3, "m": 4, "precio": 104.0, "gracia": -1},
        {"tasa": 8.0, "anios": 10, "m": 2, "precio": 100.0, "gracia": 3},
        {"tasa": 7.25, "anios": 6, "m": 12, "precio": 92.0, "gracia": 0},
    ]
    guion = js + """
const casos = JSON.parse(process.argv[2]);
console.log(JSON.stringify(casos.map(c => {
  const fl = flujosBono(c.tasa, c.anios, c.m, c.gracia);
  const y = tirBono(fl, c.precio);
  if (y === null) return null;
  const d = durBono(fl, y, c.precio);
  return {tir: y, dur: d, conv: convBono(fl, y, c.precio), vida: vidaProm(fl),
          p100: vpBono(fl, y + 1)};
})));
"""
    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / "calc.js"
        ruta.write_text(guion, encoding="utf-8")
        try:
            salida = subprocess.run(["node", str(ruta), json.dumps(casos)],
                                    capture_output=True, text=True, timeout=30)
        except (subprocess.SubprocessError, OSError) as exc:
            check(False, "la calculadora del dashboard corre en node", str(exc))
            return
    if salida.returncode != 0:
        check(False, "la calculadora del dashboard corre sin errores",
              salida.stderr.strip()[:300])
        return

    resultados = json.loads(salida.stdout)
    frec = {1: "anual", 2: "semestral", 4: "trimestral", 12: "mensual"}
    peor, donde = 0.0, ""
    for c, j in zip(casos, resultados):
        f = frec[c["m"]]
        plan = (None if c["gracia"] < 0
                else bonos.amortizacion_lineal(c["anios"], f, c["gracia"]))
        fl = bonos.armar_flujos(c["tasa"], c["anios"], f, plan)
        y = bonos.tir(fl, c["precio"])
        py = {
            "tir": y,
            "dur": bonos.duration(fl, y, c["precio"]),
            "conv": bonos.convexidad(fl, y, c["precio"]),
            "vida": bonos.vida_promedio(fl),
            "p100": bonos.valor_presente(fl, y + 1),
        }
        for k, v in py.items():
            d = abs(v - j[k])
            if d > peor:
                peor, donde = d, f"{c['tasa']}% {f} {c['anios']}a gracia={c['gracia']} -> {k}"
    check(peor < 1e-8,
          "el dashboard y bonos.py dan el mismo numero (mismas formulas)",
          f"peor diferencia {peor:.2e} en {donde}")


def probar_salidas():
    print("\nSalidas y mail")
    c2 = normalizar_todos(preparar(CORRIDA_2))
    eventos = est.detectar_novedades(est.fusionar({}, normalizar_todos(preparar(CORRIDA_1))), c2)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        csv_path = salidas.escribir_csv(c2, tmp / "l.csv")
        texto = csv_path.read_text(encoding="utf-8-sig")
        lineas = texto.strip().split("\n")
        check(len(lineas) == 7, "el CSV tiene encabezado + 6 filas", str(len(lineas)))
        check(";" in lineas[0], "el CSV usa punto y coma como delimitador")
        check("1.234.567.890.123,00" in texto,
              "los montos salen en formato argentino")

        xlsx = salidas.escribir_xlsx(c2, tmp / "l.xlsx")
        check(xlsx is not None and xlsx.exists() and xlsx.stat().st_size > 4000,
              "el XLSX se genera y no esta vacio")

        # Estado: guardar y releer tiene que ser identico.
        est.guardar(tmp / "e.json", est.fusionar({}, c2))
        releido = est.cargar(tmp / "e.json")
        check(len(releido) == 6, "el estado se relee completo")
        check(isinstance(releido["a3:-940"]["fecha_inicio"], datetime),
              "las fechas vuelven a ser datetime despues del round-trip")
        check(len(est.detectar_novedades(releido, c2)) == 0,
              "releer el estado del disco no genera falsos eventos")

        # Estado corrupto: no debe explotar.
        (tmp / "roto.json").write_text("{esto no es json", encoding="utf-8")
        check(est.cargar(tmp / "roto.json") == {},
              "un estado corrupto se descarta sin tirar excepcion")
        check((tmp / "roto.json.corrupto").exists(),
              "el estado corrupto queda respaldado, no se pierde en silencio")

    ahora = datetime.now(timezone.utc)
    html = notificar.cuerpo_html(eventos, ahora)
    txt = notificar.cuerpo_texto(eventos, ahora)
    check("GENNEIA" in html and "MIRGOR" in html, "el HTML nombra las licitaciones")
    check("<script" not in html.lower(), "el HTML no lleva scripts")
    check("US$ 100.000.000" in html, "los montos del mail salen formateados",
          [l for l in html.split("<") if "100.000.000" in l][:1])
    check("Resultado" in html, "el mail tiene la seccion de resultados")
    check(len(txt) > 200 and "Monitor de licitaciones" in txt,
          "la version en texto plano tiene contenido")

    asunto = notificar.armar_mensaje(eventos, {
        "asunto": "Licitaciones ON - {resumen}", "remitente": "a@b.com",
        "destinatarios": ["c@d.com"],
    }, ahora)["Subject"]
    check("1 nueva" in asunto and "1 resultado" in asunto,
          f"el asunto resume las novedades: {asunto!r}")


# HTML que replica la estructura real de cnv.gov.ar/SitioWeb/HechosRelevantes,
# con las rarezas del sitio: mes abreviado con punto, mayusculas, espacios de
# sobra, una tabla de navegacion que NO hay que confundir con las de datos, y
# publicaciones que mencionan ON pero no son emisiones.
HTML_CNV = """
<html><body>
<table class="menu"><tr><td>Inicio</td><td>Empresas</td></tr></table>
<table id="grid1">
  <thead><tr><th>Fecha</th><th>Entidad</th><th>Descripci&oacute;n</th><th>Documento</th><th></th></tr></thead>
  <tbody>
    <tr>
      <td>10 sep. 2026 09:15</td>
      <td>GENNEIA S.A.</td>
      <td>INFORMACI&Oacute;N RELEVANTE RELATIVA A INSTRUMENTOS - AVISO DE SUSCRIPCI&Oacute;N
          OBLIGACIONES NEGOCIABLES CLASE XLV</td>
      <td>3568900</td>
      <td><a href="https://aif2.cnv.gov.ar/Presentations/publicview/AAAA1111-2222-3333-4444-555566667777">Ver</a></td>
    </tr>
    <tr>
      <td>9 sep. 2026 22:53</td>
      <td>YPF S.A.</td>
      <td>INFORMACI&Oacute;N RELEVANTE RELATIVA A INSTRUMENTOS - OFERTAS DE COMPRA DE OBLIGACIONES NEGOCIABLES</td>
      <td>3568546</td>
      <td><a href="https://aif2.cnv.gov.ar/Presentations/publicview/98D26F19-6FE3-4BB9-96B2-3F504622A556">Ver</a></td>
    </tr>
    <tr>
      <td>9 sep. 2026 17:57</td>
      <td>AEROPUERTOS ARGENTINA 2000 S.A.</td>
      <td>INFORMACI&Oacute;N SOCIETARIA - DESIGNACI&Oacute;N DEL SR. LEANDRO OSHIRO COMO GERENTE</td>
      <td>3568490</td>
      <td><a href="https://aif2.cnv.gov.ar/Presentations/publicview/E6A6DAE2-8CB9-4CD1-ABFC-ED74D960FC92">Ver</a></td>
    </tr>
    <tr>
      <td>9 sep. 2026 11:02</td>
      <td>SION S.A.</td>
      <td>SUPLEMENTO DE PROSPECTO - ON PYME CNV GARANTIZADA MEDIANO IMPACTO SERIE VI</td>
      <td>3568400</td>
      <td><a href="https://aif2.cnv.gov.ar/Presentations/publicview/BBBB1111-2222-3333-4444-555566667777">Ver</a></td>
    </tr>
  </tbody>
</table>
<table id="grid4">
  <tr><th>Fecha</th><th>Entidad</th><th>Descripci&oacute;n</th><th>Documento</th><th></th></tr>
  <tr>
    <td>8 sep. 2026 16:40</td>
    <td>TMF TRUST COMPANY (ARGENTINA) S.A.</td>
    <td>AVISO DE SUSCRIPCI&Oacute;N - VALORES FIDUCIARIOS FF MEGABONO 358</td>
    <td>3568100</td>
    <td><a href="https://aif2.cnv.gov.ar/Presentations/publicview/CCCC1111-2222-3333-4444-555566667777">Ver</a></td>
  </tr>
</table>
</body></html>
"""


def probar_fuente_cnv():
    print("\nFuente CNV (AIF)")
    import fuente_cnv as cnv

    # --- parseo del HTML ---
    hechos = cnv.parsear_hechos(HTML_CNV)
    check(len(hechos) == 5, "parsea las 5 publicaciones de las dos tablas",
          str(len(hechos)))
    check(all(h["fecha"] for h in hechos), "todas tienen fecha parseada")

    g = next((h for h in hechos if "GENNEIA" in h["entidad"]), None)
    check(g is not None, "encuentra la publicacion de Genneia")
    check(g and g["documento"] == "3568900", "lee el numero de documento")
    check(g and g["enlace"].startswith("https://aif2.cnv.gov.ar/Presentations/"),
          "captura el enlace a la publicacion")
    check(g and "AVISO DE SUSCRIPCION" in _sin_tildes(g["descripcion"].upper()),
          "la descripcion llega entera y con las entidades HTML resueltas",
          g["descripcion"][:60] if g else "")

    # La tabla de navegacion no tiene encabezados de datos: debe ignorarse.
    check(not any("Inicio" in h["entidad"] for h in hechos),
          "ignora las tablas que no son de datos")

    # --- fechas ---
    f = cnv.parse_fecha_cnv("9 sep. 2026 22:53")
    check(f is not None and f.hour == 1 and f.day == 10,
          "convierte hora argentina a UTC (22:53 del 9 -> 01:53 UTC del 10)",
          str(f))
    check(cnv.parse_fecha_cnv("09/09/2026 15:30") is not None,
          "acepta tambien el formato numerico")
    check(cnv.parse_fecha_cnv("no es una fecha") is None,
          "un texto cualquiera devuelve None")
    check(cnv.parse_fecha_cnv("31 feb. 2026 10:00") is None,
          "una fecha imposible devuelve None en vez de explotar")

    # --- filtro de relevancia ---
    check(cnv.es_de_emision("AVISO DE SUSCRIPCION OBLIGACIONES NEGOCIABLES CLASE XLV"),
          "un aviso de suscripcion pasa el filtro")
    check(cnv.es_de_emision("SUPLEMENTO DE PROSPECTO - ON PYME CNV SERIE VI"),
          "un suplemento de prospecto pasa el filtro")
    check(not cnv.es_de_emision("OFERTAS DE COMPRA DE OBLIGACIONES NEGOCIABLES"),
          "una oferta de compra NO pasa: menciona ON pero no es una emision")
    check(not cnv.es_de_emision("DESIGNACION DEL SR. OSHIRO COMO GERENTE"),
          "una designacion de gerente no pasa")
    check(not cnv.es_de_emision("RESCATE ANTICIPADO DE OBLIGACIONES NEGOCIABLES"),
          "un rescate anticipado no pasa")

    relevantes = [h for h in hechos if cnv.es_de_emision(h["descripcion"], h["entidad"])]
    check(len(relevantes) == 3,
          "de las 5 publicaciones, quedan las 3 de emision", str(len(relevantes)))

    # --- traduccion al modelo ---
    regs = normalizar_todos([cnv.a_registro(h) for h in relevantes])
    porem = {r["emisor"]: r for r in regs}

    check(all(r["fuente"] == "cnv" for r in regs), "los registros quedan marcados como CNV")
    check(all(r["estado"] == "Anunciada" for r in regs), "el estado es 'Anunciada'")
    check(all(r["clave"].startswith("cnv:") for r in regs),
          "la clave lleva el prefijo de la fuente")
    check(porem["GENNEIA S.A."]["categoria"] == CAT_ON_CORP,
          "Genneia cae en ON Corporativa", porem["GENNEIA S.A."]["categoria"])
    check(porem["SION S.A."]["categoria"] == CAT_ON_PYME,
          "la ON PyME CNV cae en ON PyME", porem["SION S.A."]["categoria"])
    check(porem["TMF TRUST COMPANY (ARGENTINA) S.A."]["categoria"] == CAT_FF,
          "los valores fiduciarios caen en Fideicomiso",
          porem["TMF TRUST COMPANY (ARGENTINA) S.A."]["categoria"])
    check(porem["GENNEIA S.A."]["enlace"].startswith("https://aif2.cnv.gov.ar"),
          "el enlace sobrevive hasta el modelo")
    check(porem["GENNEIA S.A."]["monto_licitar"] is None,
          "un aviso no inventa monto")
    check(porem["GENNEIA S.A."]["tasa_corte"] is None,
          "un aviso no inventa tasa")

    # --- titulos textuales del feed real del 14/09/2026 ---
    # La CNV escribe distinto de lo que uno supone; estos son los que de
    # verdad aparecieron, con su veredicto correcto.
    reales_si = [
        "INFORMACIÓN RELEVANTE RELATIVA A INSTRUMENTOS - PAYWAY S.A.U. - ON CLASE 12",
        "AVISO DE SUSCRIPCIÓN - VALORES FIDUCIARIOS FF MEGABONO 358",
        "SUPLEMENTO DE PROSPECTO - ON PYME CNV GARANTIZADA MEDIANO IMPACTO SERIE VI",
    ]
    reales_no = [
        # Este se colaba como emision: el patron buscaba "rescate anticipado"
        # exacto y el titulo real dice otra cosa.
        "INFORMACIÓN RELEVANTE RELATIVA A INSTRUMENTOS - EVENTUAL RESCATE Y PRECANCELACIÓN",
        "INFORMACIÓN RELEVANTE RELATIVA A INSTRUMENTOS - INFORMA RECOMPRAS DE ONS",
        "INFORMACIÓN RELEVANTE RELATIVA A INSTRUMENTOS - CEDEARS-AVISOS DE PAGO",
        "INFORMACIÓN RELEVANTE RELATIVA A INSTRUMENTOS - ANUNCIO DE DIVIDENDOS",
        "INFORMACIÓN SOCIETARIA - NÓMINA DE LA COMISIÓN FISCALIZADORA",
        "INFORMACIÓN SOCIETARIA - CONVOCATORIA ASAMBLEA ORDINARIA UNÁNIME",
        "INFORMACIÓN COMERCIAL - AVALES, FIANZAS Y GARANTÍAS OTORGADOS AL 31-08-2026",
        "OTRA INFORMACIÓN DEL ADMINSITRADO - INFORMACIÓN DIARIA PRAP",
    ]
    fallan_si = [t for t in reales_si if not cnv.es_de_emision(t)]
    fallan_no = [t for t in reales_no if cnv.es_de_emision(t)]
    check(not fallan_si, "los titulos reales de emision pasan el filtro",
          str(fallan_si))
    check(not fallan_no, "los titulos reales que no son emision quedan afuera",
          str(fallan_no))

    # --- tablas sin fila de encabezados ---
    # La CNV parte el feed en cinco tablas y no todas traen cabecera: dos de
    # las cinco quedaban sin leer, 152 filas perdidas en la corrida real.
    sin_cabecera = (
        "<html><body><table>"
        '<tr><td>11 sep. 2026 10:00</td><td>TMF TRUST</td>'
        "<td>AVISO DE SUSCRIPCION - FF MEGABONO 359</td><td>3569000</td>"
        '<td><a href="https://aif2.cnv.gov.ar/x">Ver</a></td></tr>'
        '<tr><td>10 sep. 2026 16:00</td><td>ROSFID</td>'
        "<td>INFORMACION SOCIETARIA - NOMINA</td><td>3569002</td>"
        '<td><a href="https://aif2.cnv.gov.ar/z">Ver</a></td></tr>'
        '<tr><td>10 sep. 2026 15:00</td><td>TMF TRUST</td>'
        "<td>AVISO DE SUSCRIPCION - VALORES FIDUCIARIOS FF AGRO V</td>"
        "<td>3569003</td><td><a href=\"https://aif2.cnv.gov.ar/w\">Ver</a></td></tr>"
        "</table></body></html>"
    )
    filas = cnv.parsear_hechos(sin_cabecera)
    check(len(filas) == 3,
          "una tabla sin encabezados se reconoce igual, por su forma",
          str(len(filas)))
    check(filas[0]["entidad"] == "TMF TRUST" and filas[0]["documento"] == "3569000",
          "y las columnas se mapean bien por posicion")

    # --- las cinco tablas no usan los mismos encabezados ---
    # Verificado contra el feed real del 14/09/2026: en Fideicomisos y en PIC
    # la descripcion esta en la columna 3, no en la 2, y la 2 es otra cosa.
    # Mapeadas por posicion, el bot leia el nombre del fiduciario como si fuera
    # la descripcion de la publicacion.
    otras_cabeceras = (
        "<html><body>"
        "<table><tr><th>FECHA</th><th>FIDEICOMISO</th><th>FIDUCIARIO</th>"
        "<th>DESCRIPCION</th><th></th></tr>"
        "<tr><td>8 sep. 2026 10:00</td><td>SECUBONO 236 FIDEICOMISO FINANCIERO"
        "</td><td>Banco de Valores S.A.</td><td>NOMINA DE AUTORIDADES</td>"
        "<td></td></tr>"
        "<tr><td>8 sep. 2026 11:00</td><td>ALZ AGRO FIDEICOMISO FINANCIERO</td>"
        "<td>Banco CMF S.A.</td>"
        "<td>AVISO DE SUSCRIPCION VALORES FIDUCIARIOS</td><td></td></tr>"
        "</table>"
        "<table><tr><th>FECHA PRESENTACIÓN</th><th>CATEGORÍA</th>"
        "<th>RAZÓN SOCIAL</th><th>DESCRIPCIÓN</th><th></th></tr>"
        "<tr><td>10 sep. 2026 09:00</td><td>PYME CNV</td>"
        "<td>METALURGICA DEL SUR S.A.</td>"
        "<td>SUPLEMENTO DE PROSPECTO ON PYME SERIE III</td><td></td></tr>"
        "<tr><td>10 sep. 2026 09:30</td><td>PYME CNV</td>"
        "<td>AGRO NORTE S.R.L.</td><td>ESTADOS CONTABLES AL 30/06/2026</td>"
        "<td></td></tr></table></body></html>"
    )
    otras = cnv.parsear_hechos(otras_cabeceras)
    check(len(otras) == 4, "las cinco tablas del feed se leen con sus propios "
          "encabezados", str(len(otras)))
    ff = next(h for h in otras if "ALZ AGRO" in h["entidad"])
    check(ff["descripcion"].startswith("AVISO DE SUSCRIPCION"),
          "en la tabla de fideicomisos la descripcion no es el fiduciario",
          ff["descripcion"])
    pyme = next(h for h in otras if "METALURGICA" in h["entidad"])
    check(pyme["entidad"] == "METALURGICA DEL SUR S.A.",
          "en la tabla PIC la entidad es la razon social, no la categoria",
          pyme["entidad"])

    # El nombre de la entidad no alcanza para declarar una emision: en la tabla
    # de fideicomisos TODAS se llaman "... FIDEICOMISO FINANCIERO" y asi pasaba
    # el filtro cada una de las 51 filas, fuera lo que fuera la publicacion.
    check(not cnv.es_de_emision("NOMINA DE AUTORIDADES",
                                "SECUBONO 236 FIDEICOMISO FINANCIERO"),
          "el nombre del fideicomiso no convierte cualquier aviso en emision")
    check(cnv.es_de_emision("AVISO DE SUSCRIPCION VALORES FIDUCIARIOS",
                            "SECUBONO 236 FIDEICOMISO FINANCIERO"),
          "pero el aviso de suscripcion del mismo fideicomiso si pasa")
    check(cnv.es_de_emision("", "FF MEGABONO 358 FIDEICOMISO FINANCIERO"),
          "sin descripcion, la entidad vuelve a ser lo unico que hay")

    # Una tabla de navegacion no tiene que colarse por el fallback.
    nav = ("<html><body><table><tr><td>Inicio</td><td>Empresas</td>"
           "<td>Contacto</td></tr><tr><td>Legal</td><td>Ayuda</td>"
           "<td>Mapa</td></tr></table></body></html>")
    try:
        cnv.parsear_hechos(nav)
        check(False, "una tabla sin fechas no se toma como tabla de datos")
    except cnv.FuenteNoDisponible:
        check(True, "una tabla sin fechas no se toma como tabla de datos")

    # --- el parser roto no puede parecer un dia tranquilo ---
    try:
        cnv.parsear_hechos("<html><body><p>Sitio en mantenimiento</p></body></html>")
        check(False, "un HTML sin tablas levanta FuenteNoDisponible")
    except cnv.FuenteNoDisponible:
        check(True, "un HTML sin tablas levanta FuenteNoDisponible en vez de devolver []")


def _sin_tildes(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def probar_dos_fuentes():
    """Las dos fuentes tienen que convivir sin pisarse en el estado."""
    print("\nConvivencia de fuentes")
    import fuente_cnv as cnv

    a3 = normalizar_todos(preparar(CORRIDA_1))
    hechos = [h for h in cnv.parsear_hechos(HTML_CNV)
              if cnv.es_de_emision(h["descripcion"], h["entidad"])]
    cnv_regs = normalizar_todos([cnv.a_registro(h) for h in hechos])

    todo = a3 + cnv_regs
    claves = [r["clave"] for r in todo]
    check(len(claves) == len(set(claves)), "no hay claves repetidas entre fuentes")

    # El caso que motivo las claves compuestas: mismo id en dos fuentes.
    choque_a3 = dict(a3[0]); choque_a3["clave"] = "a3:3568900"; choque_a3["id"] = 3568900
    choque_cnv = next(r for r in cnv_regs if r["id"] == "3568900")
    fusion = est.fusionar({}, [choque_a3, choque_cnv])
    check(len(fusion) == 2,
          "dos registros con el mismo numero pero distinta fuente no se pisan",
          str(list(fusion)))

    # Deteccion mezclada.
    eventos = est.detectar_novedades({}, todo)
    check(len(eventos) == len(todo), "la primera corrida ve todo como nuevo")
    previas = est.fusionar({}, todo)
    check(len(est.detectar_novedades(previas, todo)) == 0,
          "la segunda corrida no repite nada")

    # El mail separa anticipos de licitaciones.
    ahora = datetime.now(timezone.utc)
    html = notificar.cuerpo_html(eventos, ahora)
    check("Avisos publicados en la CNV" in html,
          "el mail arma una seccion aparte para los avisos de la CNV")
    check("aif2.cnv.gov.ar" in html, "el mail linkea la publicacion de la CNV")
    check("Ver la publicacion en la CNV" in html, "el enlace tiene texto entendible")

    # Una tarjeta de aviso no debe mostrar los campos de una licitacion vacios:
    # "A licitar: -" hace parecer que el dato se perdio.
    solo_cnv = [e for e in eventos if e["licitacion"].get("fuente") == "cnv"]
    tarjeta = notificar._tarjeta(solo_cnv[0])
    check("A licitar" not in tarjeta,
          "la tarjeta de un aviso no muestra 'A licitar' vacio")
    check("Publicado" in tarjeta, "la tarjeta de un aviso muestra la fecha de publicacion")
    check("aviso CNV" in tarjeta, "la tarjeta se identifica como aviso de la CNV")

    con_aviso = notificar.cuerpo_html(eventos, ahora, avisos=["A3 Mercados: timeout"])
    check("Alguna fuente no respondio" in con_aviso,
          "si una fuente falla, el mail lo dice")
    txt = notificar.cuerpo_texto(eventos, ahora, avisos=["A3 Mercados: timeout"])
    check("Fuente con problemas" in txt, "la version en texto tambien lo dice")


def probar_migracion_estado():
    print("\nMigracion del estado v1 -> v2")
    import json

    c1 = normalizar_todos(preparar(CORRIDA_1))
    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / "estado.json"
        # Un estado como lo escribia la version anterior: v1, indexado por id
        # numerico y sin los campos fuente/clave/enlace.
        viejo = {}
        for r in c1:
            copia = {k: v for k, v in r.items()
                     if k not in ("fuente", "clave", "enlace")}
            for k, v in list(copia.items()):
                if isinstance(v, datetime):
                    copia[k] = v.isoformat()
            viejo[str(r["id"])] = copia
        ruta.write_text(json.dumps(
            {"version": 1, "actualizado": "2026-09-01T00:00:00", "licitaciones": viejo},
            ensure_ascii=False), encoding="utf-8")

        migrado = est.cargar(ruta)
        check(len(migrado) == len(c1), "migra todas las licitaciones del estado viejo",
              f"{len(migrado)} de {len(c1)}")
        check(all(k.startswith("a3:") for k in migrado),
              "las claves quedan con el prefijo a3", str(list(migrado)[:2]))
        check(all(r.get("fuente") == "a3" for r in migrado.values()),
              "los registros migrados quedan atribuidos a A3")
        # Lo que de verdad importa: migrar no puede disparar un mail con todo
        # el historico de vuelta.
        check(len(est.detectar_novedades(migrado, c1)) == 0,
              "despues de migrar, el bot no re-avisa lo que ya conocia")


def probar_version():
    print("\nVersion")
    import bot as bot_mod
    import version as ver

    check(bool(ver.VERSION) and ver.VERSION[0].isdigit(),
          f"hay un numero de version: {ver.VERSION}")
    check(ver.CAMBIOS[0][0] == ver.VERSION,
          "el changelog arranca por la version actual",
          f"{ver.CAMBIOS[0][0]} vs {ver.VERSION}")
    check(ver.VERSION in ver.detalle() and "1.0.0" in ver.detalle(),
          "el detalle lista la version actual y las anteriores")

    # --version no debe necesitar config ni red.
    check(bot_mod.main(["--version"]) == 0, "'--version' sale con codigo 0")

    # El dashboard estampa la version, que es como se sabe con que copia se genero.
    import dashboard as dash
    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / "d.html"
        dash.escribir(normalizar_todos(preparar(CORRIDA_1)), ruta,
                      datetime.now(timezone.utc))
        html = ruta.read_text(encoding="utf-8")
        check(f"v{ver.VERSION}" in html, "el dashboard estampa la version")
        check("__VERSION__" not in html, "no queda el placeholder sin reemplazar")


def probar_diagnostico_red():
    """El diagnostico tiene que nombrar la causa correcta: un DNS que no
    resuelve y un proxy que rechaza se arreglan de manera distinta, y mandar
    a alguien a revisar lo que no es cuesta media hora."""
    print("\nDiagnostico de red")
    import socket
    import red
    import requests

    URL = "https://www.cnv.gov.ar/SitioWeb/HechosRelevantes"

    dns = requests.exceptions.ConnectionError(
        "HTTPSConnectionPool(host='www.cnv.gov.ar', port=443): "
        "Max retries exceeded (Caused by NameResolutionError(\"Failed to "
        "resolve 'www.cnv.gov.ar' ([Errno 11001] getaddrinfo failed)\"))"
    )
    msg = red.explicar(dns, URL)
    check("DNS" in msg, "un fallo de resolucion se explica como DNS")
    check("nslookup" in msg, "y sugiere nslookup para confirmarlo")
    check("NO es el sitio bloqueandote" in msg,
          "aclara que no es el sitio rechazando, que es la confusion tipica")
    check("BOT_PROXY" in msg, "menciona como declarar un proxy")

    prox = requests.exceptions.ProxyError(
        "Unable to connect to proxy", OSError("Tunnel connection failed: 403 Forbidden")
    )
    msg = red.explicar(prox, URL)
    check("proxy" in msg.lower(), "un fallo de proxy se explica como proxy")
    check("DNS" not in msg, "y no se confunde con un problema de DNS")

    tout = requests.exceptions.ConnectTimeout("connection timed out")
    msg = red.explicar(tout, URL)
    check("no respondio a tiempo" in msg, "un timeout se explica como timeout")

    ssl_err = requests.exceptions.SSLError("certificate verify failed")
    msg = red.explicar(ssl_err, URL)
    check("certificado" in msg.lower(), "un fallo de TLS se explica como certificado")
    check("REQUESTS_CA_BUNDLE" in msg,
          "y dice como apuntar al certificado de la empresa")

    # gaierror crudo, sin envoltorio de requests.
    check("DNS" in red.explicar(socket.gaierror(11001, "getaddrinfo failed"), URL),
          "reconoce un gaierror crudo")

    # El chequeo por capas corre sin explotar y dice algo de cada capa.
    lineas = red.chequear_conectividad(URL)
    check(len(lineas) >= 2, "el chequeo por capas devuelve un informe")
    check(any("DNS" in l for l in lineas), "el informe habla del DNS")
    check(any(l.startswith("[") for l in lineas),
          "cada linea del informe viene marcada [ok] / [FALLA] / [info]")

    # Las credenciales del proxy no pueden aparecer en texto plano.
    red.configurar_proxy("http://franco:secreta123@proxy.empresa:8080")
    visible = red.proxy_visible()
    check("secreta123" not in visible,
          "la contrasena del proxy no se muestra", visible)
    check("****" in visible and "franco" in visible,
          "pero si el usuario y el host, para poder verificarlos", visible)
    red.configurar_proxy(None)


def probar_contexto_de_mercado():
    """Ubicar una tasa contra sus comparables, y callarse cuando no alcanza."""
    print("\nContexto de mercado para la tasa")
    import contexto as ctx_mod
    from datetime import timedelta

    ahora = datetime.now(timezone.utc)

    def on(tasa, moneda="ARS PESOS", cat=CAT_ON_CORP, dias=5, clave=None):
        return {"clave": clave or f"a3:{tasa}-{moneda}-{dias}",
                "tasa_corte": tasa, "moneda_monto": moneda, "categoria": cat,
                "fecha_inicio": ahora - timedelta(days=dias), "desierta": False}

    # Diez ON corporativas en pesos entre 30 y 39.
    universo = [on(30 + i, dias=i + 1, clave=f"a3:u{i}") for i in range(10)]

    alta = on(45.0, clave="a3:alta")
    c = ctx_mod.calcular(alta, universo, ahora)
    check(c is not None and c["percentil"] == 100,
          "una tasa por encima de todas da percentil 100",
          str(c and c["percentil"]))
    check(c["etiqueta"] == "muy por encima del mercado", "y se etiqueta como tal")
    check(c["n"] == 10, "cuenta bien los comparables")
    check(round(c["mediana"], 1) == 34.5, "calcula la mediana", str(c["mediana"]))

    baja = on(28.0, clave="a3:baja")
    c = ctx_mod.calcular(baja, universo, ahora)
    check(c["percentil"] == 0 and "debajo" in c["etiqueta"],
          "una tasa por debajo de todas se marca como muy por debajo")

    media = on(34.5, clave="a3:media")
    c = ctx_mod.calcular(media, universo, ahora)
    check("en linea" in c["etiqueta"],
          f"una tasa en la mediana queda 'en linea' (percentil {c['percentil']})")

    # La moneda separa mundos: una ON en dolares no se compara con las de pesos.
    dolar = on(7.0, moneda="USD", clave="a3:usd")
    check(ctx_mod.calcular(dolar, universo, ahora) is None,
          "una ON en dolares no se compara contra las de pesos")

    universo_usd = [on(5 + i * 0.5, moneda="USD", dias=i + 1, clave=f"a3:d{i}")
                    for i in range(8)]
    c = ctx_mod.calcular(dolar, universo_usd, ahora)
    check(c is not None and "USD" in c["universo"],
          "con comparables en dolares si opina", str(c and c["universo"]))

    # Sin suficientes casos no se inventa un percentil.
    check(ctx_mod.calcular(alta, universo[:3], ahora) is None,
          "con menos de 5 comparables no se calcula percentil")

    # Fuera de la ventana temporal tampoco cuenta.
    viejo = [on(30 + i, dias=200, clave=f"a3:v{i}") for i in range(10)]
    check(ctx_mod.calcular(alta, viejo, ahora, ventana_dias=60) is None,
          "los comparables fuera de la ventana no cuentan")

    # Si no hay suficientes de su categoria, abre a todas las ON y lo dice.
    pymes = [on(40 + i, cat=CAT_ON_PYME, dias=i + 1, clave=f"a3:p{i}")
             for i in range(3)]
    una_pyme = on(42.0, cat=CAT_ON_PYME, clave="a3:pp")
    c = ctx_mod.calcular(una_pyme, universo + pymes, ahora)
    check(c is not None and c["universo"] == "ON en ARS",
          "sin suficientes de su categoria, compara contra todas las ON y lo aclara",
          str(c and c["universo"]))

    # Nunca se compara consigo misma.
    c = ctx_mod.calcular(universo[0], universo, ahora)
    check(c is None or c["n"] == 9,
          "una licitacion no se cuenta a si misma entre sus comparables",
          str(c and c["n"]))

    # No opina de lo que no corresponde.
    check(ctx_mod.calcular({**alta, "tasa_corte": None}, universo, ahora) is None,
          "sin tasa no hay contexto")
    check(ctx_mod.calcular({**alta, "desierta": True}, universo, ahora) is None,
          "una desierta no tiene contexto")
    check(ctx_mod.calcular({**alta, "categoria": CAT_PUBLICA}, universo, ahora) is None,
          "la deuda publica no se compara contra ON")

    # La frase no puede quedar a medio armar.
    c = ctx_mod.calcular(alta, universo, ahora)
    f = ctx_mod.frase(c)
    check("percentil 100" in f and "mediana" in f and "p.p." in f,
          f"la frase del mail sale completa: {f}")
    check(ctx_mod.frase(None) == "", "sin contexto, la frase es vacia")

    # Y llega al mail.
    ev = {"evento": est.EV_RESULTADO, "etiqueta": "x",
          "licitacion": {**alta, "titulo": "ON TEST", "emisor": "X SA",
                         "valor_corte": "TASA DE CORTE: 45,00%",
                         "moneda_monto": "ARS PESOS", "monto_licitar": 1e9,
                         "fecha_inicio": ahora, "fecha_fin": ahora,
                         "tipo": "Privada - ON", "fuente": "a3"},
          "cambios": [], "contexto": c}
    tarjeta = notificar._tarjeta(ev)
    check("Contra el mercado" in tarjeta, "la tarjeta del mail muestra el contexto")
    check("percentil 100" in tarjeta, "con el percentil adentro")


def probar_diagnostico_credenciales():
    """El diagnostico tiene que orientar sin exponer nunca la contrasena."""
    print("\nDiagnostico de credenciales de correo")
    base = {"smtp_host": "smtp.gmail.com", "smtp_puerto": 587,
            "usuario_smtp": "franco@crecersgr.com.ar"}

    # Clave de cuenta en vez de contrasena de aplicacion (el caso tipico).
    d = notificar.diagnostico_credenciales({**base, "password_smtp": "MiClave2026!"})
    check("MiClave2026!" not in d, "la contrasena nunca se imprime")
    check("12 caracteres" in d, "informa el largo, que es lo que delata el error", d)
    check("16" in d, "dice cuantos caracteres tiene que tener la de aplicacion")
    check("dos pasos" in d or "aplicacion" in d, "explica que hace falta")

    # Largo correcto: el problema es otro.
    d = notificar.diagnostico_credenciales({**base, "password_smtp": "abcd efgh ijkl mnop"})
    check("sin los espacios" in d,
          "con espacios, aclara el largo real para no contradecir el diagnostico", d)
    check("largo es el correcto" in d,
          "con 16 caracteres reales, descarta el largo y lista otras causas")
    check("OTRA cuenta" in d, "sugiere que la clave sea de otra cuenta")

    # Comillas pegadas por un setx mal escrito.
    d = notificar.diagnostico_credenciales({**base, "password_smtp": '"abcd"'})
    check("comillas" in d, "detecta la clave guardada con comillas adentro")

    # Servidor que no es Google: no debe hablar de contrasenas de aplicacion.
    d = notificar.diagnostico_credenciales({
        "smtp_host": "smtp.office365.com", "smtp_puerto": 587,
        "usuario_smtp": "f@empresa.com", "password_smtp": "x" * 10})
    check("myaccount.google.com" not in d,
          "con un servidor que no es Google no manda a la config de Google")
    check("smtp.office365.com" in d, "nombra el servidor configurado")

    # Casilla vacia.
    d = notificar.diagnostico_credenciales({**base, "usuario_smtp": "",
                                            "password_smtp": ""})
    check("(vacia)" in d, "marca la casilla vacia en vez de mostrar nada")


def probar_nombres_windows():
    """Ningun archivo del paquete puede tener un nombre invalido en Windows.

    El paquete se entrega como zip y se descomprime con el Explorador. Si
    adentro hay un nombre que Windows no acepta, la extraccion falla ahi y
    los archivos que vienen despues en el zip no se extraen -- sin ningun
    error visible. Ya paso una vez: un test dejo una carpeta llamada
    "\\\\servidor-que-no-existe\\carpeta" y config.yaml, que iba despues,
    nunca llegaba al disco.
    """
    print("\nNombres de archivo validos en Windows")
    raiz = Path(__file__).resolve().parent

    invalidos = set('<>:"|?*\\')
    reservados = {"con", "prn", "aux", "nul"} | \
                 {f"com{i}" for i in range(1, 10)} | \
                 {f"lpt{i}" for i in range(1, 10)}

    problemas = []
    for ruta in raiz.rglob("*"):
        if any(p in ("__pycache__", ".git", "datos", "node_modules")
               for p in ruta.parts):
            continue
        nombre = ruta.name
        malos = invalidos & set(nombre)
        if malos:
            problemas.append(f"{nombre}: caracteres {sorted(malos)}")
        elif nombre != nombre.rstrip(". "):
            problemas.append(f"{nombre}: termina en punto o espacio")
        elif nombre.split(".")[0].lower() in reservados:
            problemas.append(f"{nombre}: nombre reservado por Windows")

    check(not problemas,
          "ningun archivo del paquete tiene un nombre invalido en Windows",
          "; ".join(problemas))


def probar_meta_social():
    """Un link pegado en WhatsApp o Slack se ve como URL pelada si la pagina
    no trae las etiquetas Open Graph."""
    print("\nVista previa del link")
    import dashboard as dash

    ahora = datetime.now(timezone.utc)
    regs = normalizar_todos(preparar(CORRIDA_2))

    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / "d.html"
        dash.escribir(regs, ruta, ahora, "https://ejemplo.github.io/licitaciones/")
        html = ruta.read_text(encoding="utf-8")

        check("__META__" not in html, "no queda el placeholder sin reemplazar")
        check('property="og:title"' in html, "lleva og:title")
        check('property="og:description"' in html, "lleva og:description")
        check('content="https://ejemplo.github.io/licitaciones/"' in html,
              "og:url usa la URL configurada")
        check('name="theme-color"' in html, "lleva theme-color para el movil")
        check("actualizado" in html, "la descripcion dice cuando se actualizo")

        # La descripcion tiene que resumir datos reales, no ser generica.
        import re
        m = re.search(r'name="description" content="([^"]*)"', html)
        desc = m.group(1) if m else ""
        check("en rueda" in desc or "corte mediano" in desc,
              f"la descripcion resume los datos de la corrida: {desc!r}")
        check("&quot;" not in desc and "<" not in desc,
              "la descripcion va escapada, sin comillas ni etiquetas sueltas")

        # Sin URL configurada, no se inventa una.
        ruta2 = Path(tmp) / "d2.html"
        dash.escribir(regs, ruta2, ahora)
        check('property="og:url"' not in ruta2.read_text(encoding="utf-8"),
              "sin url_publica configurada, no se emite og:url")


def probar_informe():
    """El informe tiene que servir reenviado: todo en el cuerpo, con contexto
    para quien no sabe que existe este bot."""
    print("\nMail de informe")
    import informe as inf

    ahora = datetime.now(timezone.utc)
    from datetime import timedelta

    def reg(**kw):
        base = {"titulo": "ON X", "emisor": "EMISOR SA", "categoria": CAT_ON_CORP,
                "estado": "Finalizada", "fecha_inicio": ahora - timedelta(days=1),
                "fecha_fin": ahora - timedelta(days=1), "moneda_monto": "ARS PESOS",
                "monto_licitar": 1e9, "monto_adjudicado": 1.2e9, "tasa_corte": 35.0,
                "desierta": False, "fuente": "a3"}
        base.update(kw)
        return base

    registros = [
        reg(titulo="ON EN RUEDA", estado="Activa", monto_adjudicado=None,
            tasa_corte=None, fecha_fin=ahora + timedelta(hours=3)),
        reg(titulo="AVISO ON NUEVA", estado="Anunciada", fuente="cnv",
            monto_licitar=None, monto_adjudicado=None, tasa_corte=None),
        reg(titulo="ON CERRADA PESOS", tasa_corte=38.5),
        reg(titulo="ON CERRADA DOLARES", moneda_monto="USD", tasa_corte=7.5),
        reg(titulo="ON DESIERTA", tasa_corte=None, desierta=True,
            monto_adjudicado=None),
        reg(titulo="FF VIEJO", fecha_inicio=ahora - timedelta(days=40),
            fecha_fin=ahora - timedelta(days=40), categoria=CAT_FF),
    ]

    datos = inf.armar_datos(registros, ahora, dias=7)
    check(len(datos["en_rueda"]) == 1, "separa lo que esta en rueda",
          str(len(datos["en_rueda"])))
    check(len(datos["anunciadas"]) == 1, "separa lo anunciado en la CNV")
    check(len(datos["resueltas"]) == 3,
          "toma solo lo cerrado dentro de la ventana", str(len(datos["resueltas"])))
    check(all(r["titulo"] != "FF VIEJO" for r in datos["resueltas"]),
          "lo de hace 40 dias queda afuera de una ventana de 7")
    check(datos["mediana_ars"] == 38.5, "mediana en pesos, solo de ON",
          str(datos["mediana_ars"]))
    check(datos["mediana_usd"] == 7.5, "mediana en dolares separada de la de pesos")
    check(datos["desiertas"] == 1, "cuenta las desiertas")

    html = inf.cuerpo_html(datos, ahora)
    check("ON EN RUEDA" in html and "ON CERRADA PESOS" in html,
          "el HTML incluye las licitaciones")
    check("38,50%" in html, "las tasas salen en formato argentino",
          "38,50%" in html and "ok" or html[:0])
    check("DESIERTA" in html, "marca la desierta")
    check("Que es esto" in html,
          "el pie explica que es, para quien lo recibe reenviado")
    check("A3 Mercados" in html and "CNV" in html, "el pie nombra las fuentes")
    check("Verificar" in html, "el pie advierte verificar antes de operar")
    check("display:flex" not in html and "display:grid" not in html,
          "no usa flex ni grid, que Outlook no renderiza")
    check("<script" not in html.lower(), "el mail no lleva scripts")
    check(CAT_ON_CORP in html, "cada fila dice de que categoria es")

    # Sin adjunto no debe prometer un adjunto.
    check("Va adjunto" not in html, "sin adjunto no menciona ningun adjunto")
    check("Va adjunto" in inf.cuerpo_html(datos, ahora, con_adjunto=True),
          "con adjunto si lo menciona")

    txt = inf.cuerpo_texto(datos, ahora)
    check("EN RUEDA AHORA" in txt and "ON CERRADA PESOS" in txt,
          "la version en texto tiene el mismo contenido")

    # Un periodo sin nada no debe generar un mail vacio.
    vacio = inf.armar_datos([], ahora, dias=7)
    check(not inf.hay_algo_para_contar(vacio),
          "sin movimientos, avisa que no hay nada que informar")
    check(inf.hay_algo_para_contar(datos), "con movimientos, si hay que informar")

    # Asunto.
    cfg = {"asunto_informe": "Licitaciones ON - {fecha} ({en_rueda} en rueda)"}
    asunto = inf.asunto(datos, cfg, ahora)
    check("1 en rueda" in asunto, f"el asunto interpola las variables: {asunto!r}")

    # El informe usa sus propios destinatarios si estan definidos.
    msg = notificar.armar_informe(datos, {
        "asunto_informe": "x", "remitente": "bot@crecersgr.com.ar",
        "nombre_remitente": "Monitor de licitaciones",
        "destinatarios": ["operativo@crecersgr.com.ar"],
        "destinatarios_informe": ["franco@crecersgr.com.ar"],
        "copia_oculta_informe": ["colega@crecersgr.com.ar"],
    }, ahora)
    check(msg["To"] == "franco@crecersgr.com.ar",
          "el informe usa destinatarios_informe, no los del aviso operativo",
          str(msg["To"]))
    check("colega@crecersgr.com.ar" in (msg["Bcc"] or ""),
          "y su propia copia oculta")
    check("Monitor de licitaciones" in msg["From"],
          "el remitente se ve con nombre, que ayuda al reenviar")


def probar_copia_a_red():
    """Copiar a un share de red y sobrevivir a que ese share no este."""
    print("\nCopia a carpeta compartida")
    c2 = normalizar_todos(preparar(CORRIDA_2))

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        local = tmp / "datos"
        salidas.escribir_csv(c2, local / "licitaciones.csv")
        salidas.escribir_xlsx(c2, local / "licitaciones.xlsx")

        # Copia a una carpeta que todavia no existe: se crea sola.
        destino = tmp / "red" / "licitaciones"
        problemas = salidas.copiar_a(
            [local / "licitaciones.csv", local / "licitaciones.xlsx"], [str(destino)])
        check(problemas == [], "copiar a una carpeta nueva no da problemas",
              str(problemas))
        check((destino / "licitaciones.csv").exists(), "el CSV llego al destino")
        check((destino / "licitaciones.xlsx").exists(), "el XLSX llego al destino")
        check((destino / "licitaciones.csv").read_bytes()
              == (local / "licitaciones.csv").read_bytes(),
              "la copia es identica al original")
        check(not list(destino.glob(".*tmp")),
              "no quedan temporales sueltos en el destino")

        # Un destino inaccesible: se reporta, no explota. Se simula con una
        # ruta cuyo padre es un archivo, que falla igual en Windows y en Linux
        # (un UNC inexistente solo falla en Windows: en Linux es un nombre de
        # carpeta perfectamente valido).
        bloqueado = tmp / "soy-un-archivo.txt"
        bloqueado.write_text("x", encoding="utf-8")
        problemas = salidas.copiar_a(
            [local / "licitaciones.csv"], [str(bloqueado / "adentro")])
        check(len(problemas) == 1, "un destino inaccesible devuelve un problema",
              str(problemas))
        check("soy-un-archivo" in problemas[0],
              "el problema nombra la carpeta que fallo", str(problemas))

        # Un archivo que no existe se saltea en silencio.
        check(salidas.copiar_a([local / "no-esta.html"], [str(destino)]) == [],
              "un archivo inexistente se saltea sin dar problema")

        # Destinos vacios en la config no rompen nada.
        check(salidas.copiar_a([local / "licitaciones.csv"], ["", "  "]) == [],
              "entradas vacias en copiar_a se ignoran")


def probar_salida_no_fatal():
    """Que no se pueda escribir una salida no puede impedir el aviso."""
    print("\nUna salida rota no frena la corrida")
    import bot as bot_mod
    import fuentes as fuentes_mod
    import salidas as sal_mod
    import yaml

    original_traer = fuentes_mod.traer_todo
    original_xlsx = sal_mod.escribir_xlsx
    lote = {"datos": preparar(CORRIDA_1), "errores": []}
    fuentes_mod.traer_todo = lambda *a, **k: (lote["datos"], list(lote["errores"]))

    def xlsx_roto(*a, **k):
        raise OSError(13, "Permission denied")
    sal_mod.escribir_xlsx = xlsx_roto

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = tmp / "config.yaml"
            cfg_path.write_text(yaml.safe_dump({
                "fuentes": ["a3"],
                "destinatarios": ["franco@ejemplo.com"],
                "archivo_estado": str(tmp / "estado.json"),
                "archivo_csv": str(tmp / "l.csv"),
                "archivo_xlsx": str(tmp / "l.xlsx"),
                "archivo_dashboard": str(tmp / "d.html"),
                # Estas pruebas no son sobre la moneda y los datos de muestra
                # son casi todos en pesos: sin esto, el filtro de moneda por
                # defecto (solo dolares) los dejaria afuera y estarian midiendo
                # otra cosa. El filtro tiene sus propias pruebas.
                "monedas": [],
            }, allow_unicode=True), encoding="utf-8")
            args = ["--config", str(cfg_path)]

            code = bot_mod.main(args + ["--init"])
            check(code == 4,
                  "si una salida falla la corrida sigue y avisa con 4",
                  f"codigo {code}")
            check((tmp / "l.csv").exists(),
                  "las otras salidas se generan igual")
            check((tmp / "d.html").exists(),
                  "el dashboard se genera aunque el xlsx haya fallado")
            check((tmp / "estado.json").exists(),
                  "el estado se guarda igual: no se pierde el trabajo de la corrida")
    finally:
        fuentes_mod.traer_todo = original_traer
        sal_mod.escribir_xlsx = original_xlsx


def probar_destinatarios():
    """Con copia oculta, la lista de direcciones no puede viajar en el mensaje:
    si el bot avisa a varios inversores, ninguno tiene que ver a los otros."""
    print("\nDestinatarios, copia y copia oculta")
    import config as cfg_mod

    c2 = normalizar_todos(preparar(CORRIDA_2))
    eventos = est.detectar_novedades({}, c2)
    ahora = datetime.now(timezone.utc)

    cfg = {
        "asunto": "Licitaciones ON - {resumen}",
        "remitente": "franco@crecersgr.com.ar",
        "destinatarios": ["franco@crecersgr.com.ar"],
        "copia": ["mesa@crecersgr.com.ar"],
        "copia_oculta": ["inversor1@ejemplo.com", "inversor2@ejemplo.com"],
    }
    msg = notificar.armar_mensaje(eventos, cfg, ahora)

    check(msg["To"] == "franco@crecersgr.com.ar", "el Para sale bien", str(msg["To"]))
    check(msg["Cc"] == "mesa@crecersgr.com.ar", "la copia sale bien", str(msg["Cc"]))
    check("inversor1@ejemplo.com" in (msg["Bcc"] or ""),
          "la copia oculta se carga en el mensaje")

    # Lo que de verdad importa: al enviar, el Bcc no viaja.
    import smtplib
    capturado = {}
    s = smtplib.SMTP()
    # send_message saluda al servidor antes de mandar; aca no hay servidor, y
    # lo que se quiere probar es lo que arma, no el dialogo SMTP.
    s.ehlo_or_helo_if_needed = lambda: None
    s.does_esmtp = False
    s.sendmail = lambda de, para, texto, *a, **k: capturado.update(
        de=de, para=para, texto=texto)
    try:
        s.send_message(msg)
    except Exception as exc:                       # noqa: BLE001
        check(False, "send_message corre sobre el mensaje armado", repr(exc))
    else:
        crudo = capturado["texto"]
        if isinstance(crudo, bytes):
            crudo = crudo.decode("utf-8", "replace")
        check("inversor1@ejemplo.com" in capturado["para"],
              "los de copia oculta igual reciben el mail")
        check("inversor1@ejemplo.com" not in crudo,
              "pero sus direcciones NO viajan adentro del mensaje")
        check("Bcc" not in crudo, "el encabezado Bcc no viaja en el mensaje")
        check("mesa@crecersgr.com.ar" in crudo,
              "los de copia normal si son visibles, como corresponde")
        check(len(capturado["para"]) == 4,
              "el mail sale a los 4 destinos", str(capturado["para"]))

    # Solo copia oculta: el Para no puede quedar vacio.
    solo_bcc = {**cfg, "destinatarios": [], "copia": []}
    msg2 = notificar.armar_mensaje(eventos, solo_bcc, ahora)
    check(msg2["To"] == "franco@crecersgr.com.ar",
          "si todo va oculto, el remitente se pone a si mismo en Para",
          str(msg2["To"]))

    # La validacion cuenta las tres listas.
    base = {"usuario_smtp": "u", "password_smtp": "p"}
    ok, _ = cfg_mod.puede_mandar_mail({**base, "destinatarios": [],
                                       "copia": [], "copia_oculta": ["a@b.com"]})
    check(ok, "alcanza con tener alguien en copia oculta para poder enviar")
    ok, motivo = cfg_mod.puede_mandar_mail({**base, "destinatarios": [],
                                           "copia": [], "copia_oculta": []})
    check(not ok and "destinatarios" in motivo,
          "sin nadie en ninguna lista, avisa que faltan destinatarios")
    check(len(cfg_mod.todos_los_destinos(cfg)) == 4,
          "cuenta bien el total de destinos")


def probar_adjuntos():
    print("\nAdjuntos del mail")
    c2 = normalizar_todos(preparar(CORRIDA_2))
    eventos = est.detectar_novedades({}, c2)
    ahora = datetime.now(timezone.utc)
    cfg = {"asunto": "x", "remitente": "a@b.com", "destinatarios": ["c@d.com"]}

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        import dashboard as dash
        dash.escribir(c2, tmp / "dashboard.html", ahora)
        salidas.escribir_xlsx(c2, tmp / "l.xlsx")

        msg = notificar.armar_mensaje(eventos, cfg, ahora,
                                      adjuntos=[tmp / "dashboard.html", tmp / "l.xlsx"])
        nombres = [p.get_filename() for p in msg.iter_attachments()]
        check(nombres == ["dashboard.html", "l.xlsx"],
              "adjunta el dashboard y la planilla", str(nombres))

        tipos = [p.get_content_type() for p in msg.iter_attachments()]
        check("text/html" in tipos, "el dashboard va con content-type text/html",
              str(tipos))
        check(any("spreadsheetml" in t for t in tipos),
              "el xlsx va con el content-type de Excel", str(tipos))

        # El adjunto tiene que ser el archivo entero, no un recorte.
        adj = next(p for p in msg.iter_attachments()
                   if p.get_filename() == "dashboard.html")
        check(len(adj.get_payload(decode=True)) == (tmp / "dashboard.html").stat().st_size,
              "el adjunto pesa lo mismo que el archivo original")

        # Un archivo que no existe no puede romper el envio.
        msg2 = notificar.armar_mensaje(eventos, cfg, ahora,
                                       adjuntos=[tmp / "no-existe.html"])
        check(len(list(msg2.iter_attachments())) == 0,
              "un adjunto inexistente se saltea sin romper el mail")

        # Uno demasiado grande tampoco: el mail sale igual, sin el adjunto.
        gordo = tmp / "gordo.csv"
        gordo.write_bytes(b"x" * int((notificar.MAX_ADJUNTO_MB + 1) * 1e6))
        msg3 = notificar.armar_mensaje(eventos, cfg, ahora, adjuntos=[gordo])
        check(len(list(msg3.iter_attachments())) == 0,
              "un adjunto que pasa el tope se saltea y el mail sale igual")
        check("Licitaciones" in msg3.get_body(("html",)).get_content()
              or len(msg3.get_body(("html",)).get_content()) > 100,
              "el cuerpo del mail sigue intacto sin el adjunto")

    # La config valida lo que se puede adjuntar.
    import config as cfg_mod
    import yaml
    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / "c.yaml"
        ruta.write_text(yaml.safe_dump({"adjuntar": ["dashboard", "pdf"]}),
                        encoding="utf-8")
        try:
            cfg_mod.cargar(ruta)
            check(False, "un adjunto invalido en config levanta ConfigInvalida")
        except cfg_mod.ConfigInvalida as exc:
            check("pdf" in str(exc),
                  "un adjunto invalido en config levanta ConfigInvalida y lo nombra")


def probar_novedades_no_se_pierden():
    """Si el mail no sale, las novedades NO pueden darse por avisadas.

    Es la peor falla posible en algo que existe para avisar: el bot queda
    mudo y todo parece normal. Se prueba el caso real que paso el 11/09,
    con las credenciales rechazadas.
    """
    print("\nNovedades pendientes cuando falla el mail")
    import bot as bot_mod
    import fuentes as fuentes_mod
    import yaml

    original = fuentes_mod.traer_todo
    lote = {"datos": preparar(CORRIDA_1)}
    fuentes_mod.traer_todo = lambda *a, **k: (lote["datos"], [])

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = tmp / "config.yaml"
            # Sin credenciales: el envio falla siempre.
            cfg_path.write_text(yaml.safe_dump({
                "fuentes": ["a3"],
                "destinatarios": ["franco@ejemplo.com"],
                "archivo_estado": str(tmp / "estado.json"),
                "archivo_csv": str(tmp / "l.csv"),
                "archivo_xlsx": str(tmp / "l.xlsx"),
                "archivo_dashboard": str(tmp / "d.html"),
                # Estas pruebas no son sobre la moneda y los datos de muestra
                # son casi todos en pesos: sin esto, el filtro de moneda por
                # defecto (solo dolares) los dejaria afuera y estarian midiendo
                # otra cosa. El filtro tiene sus propias pruebas.
                "monedas": [],
            }, allow_unicode=True), encoding="utf-8")
            args = ["--config", str(cfg_path)]

            bot_mod.main(args + ["--init"])          # estado inicial, sin avisar

            # Llega una licitacion nueva y el mail no sale.
            lote["datos"] = preparar(CORRIDA_2)
            code = bot_mod.main(args)
            check(code == 3, "el envio falla con codigo 3", f"codigo {code}")

            estado_tras_fallo = est.cargar(tmp / "estado.json")
            check("a3:-951" not in estado_tras_fallo,
                  "la licitacion no avisada NO queda guardada como conocida",
                  str(sorted(estado_tras_fallo)[:3]))

            # La corrida siguiente tiene que volver a detectarla.
            eventos = est.detectar_novedades(
                estado_tras_fallo, normalizar_todos(preparar(CORRIDA_2)))
            ids = {e["licitacion"]["id"] for e in eventos}
            check(-951 in ids,
                  "la corrida siguiente la vuelve a detectar", str(sorted(ids)))

            # Y un cambio sobre algo ya conocido tambien se reintenta.
            check(-940 in ids,
                  "un cambio no avisado tambien se vuelve a detectar",
                  str(sorted(ids)))

            # Lo que no genero evento si se guarda: no se pierde el avance.
            check(len(estado_tras_fallo) >= 4,
                  "el resto del estado se guarda igual",
                  str(len(estado_tras_fallo)))

            # Con el mail andando, deja de repetirse.
            enviado = {"n": 0}
            import notificar as noti
            orig_enviar = noti.enviar
            noti.enviar = lambda *a, **k: (enviado.update(n=enviado["n"] + 1), True)[1]
            import config as cfg_mod
            orig_puede = cfg_mod.puede_mandar_mail
            cfg_mod.puede_mandar_mail = lambda cfg: (True, "")
            try:
                code = bot_mod.main(args)
                check(code == 0 and enviado["n"] == 1,
                      "con el mail andando, se avisa y sale con 0",
                      f"codigo {code}, envios {enviado['n']}")
                code = bot_mod.main(args)
                check(code == 0 and enviado["n"] == 1,
                      "y ya no se repite en la corrida siguiente",
                      f"envios {enviado['n']}")
            finally:
                noti.enviar = orig_enviar
                cfg_mod.puede_mandar_mail = orig_puede
    finally:
        fuentes_mod.traer_todo = original


def probar_flujo_completo():
    """Corre bot.main() de punta a punta con la fuente simulada.

    Es la prueba que mas importa: verifica que las piezas encajen y, sobre
    todo, que la primera corrida no dispare un mail con toda la ventana y que
    la segunda no repita lo ya avisado.
    """
    print("\nFlujo completo (bot.py)")
    import bot as bot_mod
    import fuentes as fuentes_mod
    import yaml

    original = fuentes_mod.traer_todo
    lote = {"datos": preparar(CORRIDA_1), "errores": []}
    # bot.py llama fuentes_mod.traer_todo, asi que se parchea ahi.
    fuentes_mod.traer_todo = lambda *a, **k: (lote["datos"], lote["errores"])

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = tmp / "config.yaml"
            cfg_path.write_text(yaml.safe_dump({
                "fuentes": ["a3"],
                "destinatarios": ["franco@ejemplo.com"],
                "categorias_aviso": ["ON Corporativa", "ON PyME CNV",
                                     "ON Sostenible (VS/SVS)"],
                "categorias_planilla": ["ON Corporativa", "ON PyME CNV",
                                        "ON Sostenible (VS/SVS)",
                                        "Fideicomiso Financiero", "Deuda Publica", "Otro"],
                "eventos_aviso": ["nueva", "resultado", "cancelada"],
                "archivo_estado": str(tmp / "estado.json"),
                "archivo_csv": str(tmp / "l.csv"),
                "archivo_xlsx": str(tmp / "l.xlsx"),
                "archivo_dashboard": str(tmp / "d.html"),
                # Estas pruebas no son sobre la moneda y los datos de muestra
                # son casi todos en pesos: sin esto, el filtro de moneda por
                # defecto (solo dolares) los dejaria afuera y estarian midiendo
                # otra cosa. El filtro tiene sus propias pruebas.
                "monedas": [],
            }, allow_unicode=True), encoding="utf-8")

            args = ["--config", str(cfg_path)]

            # 1. Primera corrida con --init: nada de mail, todo a disco.
            code = bot_mod.main(args + ["--init"])
            check(code == 0, "la corrida --init termina bien", f"codigo {code}")
            check((tmp / "estado.json").exists(), "--init deja el estado escrito")
            check((tmp / "l.csv").exists(), "--init genera la planilla")
            check((tmp / "d.html").exists(), "--init genera el dashboard")

            html = (tmp / "d.html").read_text(encoding="utf-8")
            check("MIRGOR" in html, "el dashboard incluye los datos embebidos")
            check(html.count("<script") == 1, "el dashboard es un solo bloque de script")

            # 2. Corrida normal sin cambios: no hay nada que avisar, asi que
            #    no importa que falten credenciales.
            code = bot_mod.main(args)
            check(code == 0, "una corrida sin novedades sale con codigo 0",
                  f"codigo {code}")

            # 3. Llega la novedad y no hay credenciales cargadas: el bot tiene
            #    que fallar ruidosamente (3), no fingir que aviso.
            lote["datos"] = preparar(CORRIDA_2)
            code = bot_mod.main(args)
            check(code == 3,
                  "con novedades y sin credenciales devuelve 3 en vez de callarse",
                  f"codigo {code}")

            # 4. La corrida siguiente vuelve a intentarlo: lo que no se pudo
            #    avisar no se da por avisado. (Este check antes esperaba un 0,
            #    o sea daba por bueno que el aviso se perdiera; era el test
            #    documentando un bug en vez de detectarlo.)
            code = bot_mod.main(args)
            check(code == 3, "la corrida posterior reintenta la novedad pendiente",
                  f"codigo {code}")

            texto = (tmp / "l.csv").read_text(encoding="utf-8-sig")
            check("GENNEIA" in texto,
                  "la planilla incorpora la licitacion aunque el aviso no salga")

            # 5. El historico no se poda: la API deja de devolver lo viejo y
            #    el bot lo tiene que conservar igual.
            lote["datos"] = preparar([r for r in CORRIDA_2 if r["id"] == -951])
            bot_mod.main(args)
            texto = (tmp / "l.csv").read_text(encoding="utf-8-sig")
            check("MIRGOR" in texto,
                  "una licitacion que salio de la ventana de la API sigue en la planilla")

            # 6. --dry-run no toca nada.
            antes = (tmp / "estado.json").stat().st_mtime_ns
            lote["datos"] = preparar(CORRIDA_1)
            bot_mod.main(args + ["--dry-run"])
            check((tmp / "estado.json").stat().st_mtime_ns == antes,
                  "--dry-run no modifica el estado")

            # 7. Una fuente caida entre varias: corrida parcial, codigo 4.
            lote["errores"] = ["CNV / AIF: timeout"]
            code = bot_mod.main(args)
            check(code == 4,
                  "si una fuente falla pero otra anda, devuelve 4 (parcial)",
                  f"codigo {code}")
            lote["errores"] = []

            # 8. Si no anda ninguna, codigo 2 y el estado queda intacto.
            antes = (tmp / "estado.json").stat().st_mtime_ns

            def explotar(*a, **k):
                raise fuentes_mod.NingunaFuenteDisponible("simulado")
            fuentes_mod.traer_todo = explotar
            code = bot_mod.main(args)
            check(code == 2, "si no anda ninguna fuente devuelve 2", f"codigo {code}")
            check((tmp / "estado.json").stat().st_mtime_ns == antes,
                  "una caida total no pisa el estado guardado")
    finally:
        fuentes_mod.traer_todo = original


def main() -> int:
    print("=" * 68)
    print("Prueba del monitor de licitaciones (sin red, sin mail)")
    print("=" * 68)

    probar_categorias()
    probar_parseo()
    probar_normalizacion()
    probar_deteccion()
    probar_casos_reales()
    probar_filtros()
    probar_filtro_de_moneda()
    probar_calculadora_de_bonos()
    probar_salidas()
    probar_fuente_cnv()
    probar_dos_fuentes()
    probar_migracion_estado()
    probar_version()
    probar_diagnostico_red()
    probar_informe()
    probar_meta_social()
    probar_nombres_windows()
    probar_contexto_de_mercado()
    probar_diagnostico_credenciales()
    probar_copia_a_red()
    probar_salida_no_fatal()
    probar_novedades_no_se_pierden()
    probar_destinatarios()
    probar_adjuntos()
    probar_flujo_completo()

    print("\n" + "=" * 68)
    if FALLAS:
        print(f"{len(FALLAS)} FALLA(S):")
        for f in FALLAS:
            print(f"  - {f}")
        return 1
    print("Todo en verde.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
