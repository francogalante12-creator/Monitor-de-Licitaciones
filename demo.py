#!/usr/bin/env python3
"""
Genera un dashboard de muestra sin tocar la red.

Fabrica licitaciones plausibles (emisores y formatos tomados de colocaciones
reales del mercado local) para poder revisar el aspecto y el comportamiento
del dashboard antes de la primera corrida de verdad.

    python demo.py [salida.html]
"""

from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import dashboard
import salidas
from modelo import normalizar_todos

random.seed(7)

CORPORATIVOS = [
    ("YPF ENERGIA ELECTRICA S.A.", "ON YPF ENERGIA ELECTRICA CLASE {r}", "USD"),
    ("PAMPA ENERGIA S.A.", "ON PAMPA ENERGIA CLASE {n}", "USD"),
    ("BANCO GALICIA", "ON BANCO GALICIA CLASE {r}", "ARS PESOS"),
    ("BANCO COMAFI S.A.", "ON BANCO COMAFI CLASE {r}", "ARS PESOS"),
    ("MIRGOR S.A.C.I.F.I.A.", "ON MIRGOR CLASE {r}", "USD"),
    ("CRESUD S.A.C.I.F. Y A.", "ON CRESUD SERIE {n} CLASE {r}", "USD LINK"),
    ("COMPANIA GENERAL DE COMBUSTIBLES", "ON CGC CLASE {n}", "USD"),
    ("BANCO HIPOTECARIO S.A.", "ON BANCO HIPOTECARIO CLASE {n}", "ARS PESOS"),
    ("PROFERTIL S.A.", "ON PROFERTIL CLASE {n}", "USD"),
    ("SCANIA CREDIT ARGENTINA", "ON SCANIA CREDIT ARGENTINA CLASE {n}", "ARS PESOS"),
    ("CREDICUOTAS CONSUMO S.A.", "ON CREDICUOTAS CONSUMO SERIE {r}", "ARS PESOS"),
    ("ICBC ARGENTINA", "ON ICBC CLASE {r}", "ARS PESOS"),
]
PYME = [
    ("AGROPECUARIA SUR S.A.", "ON BAJO IMPACTO AGROPECUARIA SUR SERIE {r}"),
    ("INDAVE S.A.", "ON BAJO IMPACTO INDAVE SERIE {r}"),
    ("SION S.A.", "ON PYME CNV MEDIANO IMPACTO SION SERIE {r}"),
    ("RDA RENTING S.A.", "ON BAJO IMPACTO RDA RENTING SERIE {r}"),
    ("LODISER S.A.", "ON BAJO IMPACTO LODISER CLASE {r}"),
    ("DEMAGRO S.A.", "ON BAJO IMPACTO DEMAGRO SERIE {r}"),
    ("PEDROLGA S.A.", "ON BAJO IMPACTO PEDROLGA SERIE {r}"),
    ("CHAMICAL SOLAR I S.A.", "ON MEDIANO IMPACTO CHAMICAL SOLAR SERIE {r}"),
    ("RUTA 3 AUTOMOTORES S.A.", "ON BAJO IMPACTO RUTA 3 AUTOMOTORES SERIE {r}"),
    ("CENTRO INTERACCION MULTIMEDIA", "ON MEDIANO IMPACTO CENTRO INTERACCION {r}"),
    ("PIMENTO S.A.", "ON BAJO IMPACTO PIMENTO SERIE {r}"),
    ("CEREALERA PUNTANA S.A.", "ON PYME CNV GARANTIZADA CEREALERA PUNTANA SERIE {r}"),
]
FF = [
    ("BANCO PATAGONIA S.A.", "FF MERCADO CREDITO {r}"),
    ("TMF Trust Company (Argentina) S.A.", "FF MEDIANO IMPACTO GOCREDITOS SERIE {r}"),
    ("TMF Trust Company (Argentina) S.A.", "FF MEGABONO {n}"),
    ("BANCO DE VALORES S.A.", "FF CUOTAS CENCOSUD SERIE {r}"),
]
PUBLICOS = [
    ("MINISTERIO DE ECONOMIA", "LETRA DEL TESORO NACIONAL CAPITALIZABLE VTO. {n}"),
    ("MINISTERIO DE ECONOMIA", "BONO DEL TESORO NACIONAL EN DOLARES VTO. {n}"),
    ("PROVINCIA DE BUENOS AIRES", "LETRAS DEL TESORO DE LA PCIA DE BS AS CLASE {n}"),
    ("PROVINCIA DEL CHACO", "LETRAS DE TESORERIA DE LA PROVINCIA DEL CHACO CLASE {n}"),
]
COLOCADORES = [
    "BANCO SANTANDER ARGENTINA S.A. BALANZ CAPITAL VALORES S.A.U.",
    "ALLARIA S.A. INVERTIRONLINE S.A.U.",
    "STONEX SECURITIES S.A.",
    "MACRO SECURITIES S.A.U. PUENTE HNOS S.A.",
    "BANCO DE GALICIA Y BUENOS AIRES S.A.U. SBS TRADING S.A.",
]
ROMANOS = ["I","II","III","IV","V","VI","VII","VIII","IX","X","XI","XII",
           "XIII","XIV","XV","XX","XXII","XXIV","XXVI","XXVIII","XXXIV","XLV"]


def _titulo(plantilla: str) -> str:
    return plantilla.format(r=random.choice(ROMANOS), n=random.randint(1, 60))


def fabricar(n: int = 78) -> list[dict]:
    hoy = datetime.now(timezone.utc).replace(hour=13, minute=0, second=0, microsecond=0)
    regs: list[dict] = []
    idx = -600

    for i in range(n):
        dias = int((n - i) * 0.75)
        inicio = hoy - timedelta(days=dias)
        if inicio.weekday() >= 5:                      # el mercado no opera finde
            inicio -= timedelta(days=inicio.weekday() - 4)

        r = random.random()
        if r < 0.34:
            emisor, plantilla, moneda = random.choice(CORPORATIVOS)
            tipo, cat = "Privada - ON", "corp"
        elif r < 0.62:
            emisor, plantilla = random.choice(PYME)
            moneda = random.choice(["ARS PESOS", "ARS PESOS", "USD"])
            tipo = random.choice(["Privada - ON/VCP BAJO IMPACTO",
                                  "Privada - ON/VCP MEDIANO IMPACTO"])
            cat = "pyme"
        elif r < 0.70:
            emisor = "ASOCIACION CIVIL SUMATORIA PARA UNA NUEVA ECONOMIA"
            plantilla = "ON BAJO IMPACTO SVS SUMATORIA CLASE {r}"
            moneda, tipo, cat = "ARS PESOS", "Privada - ON VS/SVS", "sost"
        elif r < 0.85:
            emisor, plantilla = random.choice(FF)
            moneda = "ARS PESOS"
            tipo = random.choice(["Privada - FF", "Privada – FF MEDIANO IMPACTO"])
            cat = "ff"
        else:
            emisor, plantilla = random.choice(PUBLICOS)
            moneda = random.choice(["ARS PESOS", "USD", "UVA"])
            tipo, cat = "Pública", "pub"

        activa = dias <= 0
        desierta = (not activa) and random.random() < 0.05

        if moneda.startswith("ARS"):
            monto = random.choice([500e6, 1e9, 2.5e9, 5e9, 15e9, 40e9]) * random.uniform(.6, 1.6)
            tasa = {"corp": random.uniform(28, 40), "pyme": random.uniform(32, 48),
                    "sost": random.uniform(30, 42), "ff": random.uniform(29, 38),
                    "pub": random.uniform(25, 34)}[cat]
        else:
            monto = random.choice([1e6, 5e6, 20e6, 50e6, 120e6]) * random.uniform(.6, 1.5)
            tasa = {"corp": random.uniform(5, 9), "pyme": random.uniform(7, 12),
                    "sost": random.uniform(6, 9), "ff": random.uniform(6, 10),
                    "pub": random.uniform(4.5, 7)}[cat]

        monto = round(monto, -3)
        adjudicado = 0.0 if (activa or desierta) else round(monto * random.uniform(.55, 1.9), -3)

        if activa:
            corte = ""
        elif desierta:
            corte = "DESIERTA"
        elif cat == "ff":
            corte = f"MARGEN DE CORTE VDFA: {tasa:.2f}% / PRECIO DE CORTE CP: %".replace(".", ",")
        else:
            etiqueta = "TASA DE CORTE" if random.random() < .6 else "MARGEN DE CORTE"
            corte = f"{etiqueta}: {tasa:.2f}%".replace(".", ",")

        plazo_anios = random.choice([1, 2, 2, 3, 5])
        regs.append({
            "id": idx, "titulo": _titulo(plantilla), "emisor": emisor + "  ",
            "tipo": tipo, "moneda": moneda, "monedaMonto": moneda,
            "montoaLicitar": monto, "monto_Adjudicado": adjudicado,
            "valor_Corte": corte,
            "variableLicitar": random.choice(["TASA", "MARGEN", "PRECIO"]),
            "sistema_Adjudicacion": random.choice(["Holandés", "Americano"]),
            "ampliableHasta": random.choice(["NO AMPLIABLE", "AMPLIABLE"]),
            "colocador": random.choice(COLOCADORES),
            "liquidador": random.choice(["Clear", "MAECLEAR"]),
            "rueda": f"R{random.randint(100, 999)}", "modalidad": "Abierta",
            "industria": "", "observaciones": "", "comentario": "",
            "duration": "", "plazoEspecie": "", "existeArchivo": 0, "archivos": [],
            "fechaInicio": inicio.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fechaFin": (inicio + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fechaLiquidacion": (inicio + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fechaVencimiento": "0001-01-01T00:00:00",
            "fechaVencimientoEspecie": (
                (inicio + timedelta(days=365 * plazo_anios)).strftime("%Y-%m-%dT%H:%M:%SZ")
                if random.random() < .85 else "0001-01-01T00:00:00"
            ),
            "fechaModificacion": (inicio + timedelta(hours=9)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "estado": "Activa",
            "_estado_cod": "A" if activa else ("C" if random.random() < .03 else "F"),
            "_consultado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
        idx -= 1

    for r in regs:
        r["_estado_real"] = {"A": "Activa", "C": "Cancelada/Suspendida",
                             "F": "Finalizada"}[r["_estado_cod"]]
    return regs


def avisos_cnv(n: int = 5) -> list[dict]:
    """Publicaciones de la CNV: emisiones anunciadas que todavia no licitaron."""
    hoy = datetime.now(timezone.utc).replace(hour=12, minute=30, second=0, microsecond=0)
    salida = []
    for i in range(n):
        emisor, plantilla, *_ = random.choice(CORPORATIVOS + [
            (e, p, "ARS PESOS") for e, p in PYME
        ])
        fecha = hoy - timedelta(days=i, hours=random.randint(0, 6))
        titulo = _titulo(plantilla)
        salida.append({
            "id": str(3568000 + i * 37),
            "titulo": f"AVISO DE SUSCRIPCION - {titulo}",
            "emisor": emisor,
            "tipo": "CNV - ON/VCP PYME" if "BAJO IMPACTO" in titulo
                    or "PYME" in titulo else "CNV - ON",
            "moneda": "", "monedaMonto": "",
            "montoaLicitar": None, "monto_Adjudicado": None,
            "valor_Corte": "", "variableLicitar": "", "sistema_Adjudicacion": "",
            "ampliableHasta": "", "colocador": "", "liquidador": "", "rueda": "",
            "modalidad": "", "industria": "", "duration": "", "plazoEspecie": "",
            "observaciones": f"AVISO DE SUSCRIPCION - {titulo}",
            "comentario": "", "existeArchivo": 1,
            "archivos": [],
            "fechaInicio": fecha.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fechaFin": None, "fechaLiquidacion": None,
            "fechaVencimiento": "0001-01-01T00:00:00",
            "fechaVencimientoEspecie": "0001-01-01T00:00:00",
            "fechaModificacion": fecha.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "estado": "Anunciada", "_estado_cod": "N", "_estado_real": "Anunciada",
            "_fuente": "cnv",
            "_enlace": f"https://aif2.cnv.gov.ar/Presentations/publicview/DEMO{i:04d}",
            "_consultado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })
    return salida


def main() -> int:
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("datos/dashboard_demo.html")
    regs = normalizar_todos(fabricar() + avisos_cnv())
    ahora = datetime.now(timezone.utc)

    dashboard.escribir(regs, destino, ahora)
    salidas.escribir_csv(regs, destino.with_name("demo.csv"))

    activas = sum(1 for r in regs if r["estado"] == "Activa")
    anunciadas = sum(1 for r in regs if r["estado"] == "Anunciada")
    con_tasa = sum(1 for r in regs if r["tasa_corte"] is not None)
    print(f"{len(regs)} registros ({activas} activas, {anunciadas} anunciadas en la "
          f"CNV, {con_tasa} con corte numerico)")
    print(f"Dashboard: {destino.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
