"""
Calculadora de bonos: flujo de fondos, TIR, duration, convexidad y
sensibilidad a la tasa.

Que puede y que no
------------------
Todo lo de aca se calcula sobre un flujo de fondos. Si el flujo es correcto,
los numeros son exactos; si el flujo es una suposicion, los numeros heredan
esa suposicion y hay que decirlo. Por eso cada resultado viene con un campo
'supuestos' y con 'confiable', y el bot solo muestra sin asterisco lo que es
confiable.

  Sirve, y es exacto
    Bono a tasa fija, bullet o con amortizaciones conocidas: TIR, duration
    de Macaulay y modificada, convexidad, precio ante un movimiento de tasa.
    Conversion TNA <-> TEA, que es pura aritmetica.

  Sirve, pero es una estimacion
    Una licitacion recien adjudicada de la que solo sabemos la tasa de corte
    y el vencimiento. Se asume bullet con cupon semestral, que es lo habitual
    en las ON corporativas en dolares. Si el bono amortiza, la duration real
    es MENOR que la que sale de aca: el error va en contra de la prudencia,
    asi que se marca.

  No sirve
    Tasa variable (Badlar + margen, TAMAR, CER). El flujo depende de una tasa
    futura que no conocemos y cualquier TIR seria inventada. Para esos se
    devuelve None y el bot no opina. Es deliberado: mejor un campo vacio que
    un numero que parece calculado.

La convencion de dias es 30/360, que es la que usan las ON locales.

Sobre la duration modificada
----------------------------
Aca la TIR es efectiva anual y la duration modificada es Macaulay / (1 + TIR),
que es la convencion del mercado local: en las pantallas argentinas "TIR"
quiere decir efectiva anual. Una terminal que compone semestralmente reporta
Macaulay / (1 + TNA/2), que da un numero algo mas alto. No es que una este
mal: son dos convenciones, y conviene saber cual se esta mirando antes de
comparar contra otra fuente.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Frecuencias de pago de cupon, en pagos por anio.
FRECUENCIAS = {
    "anual": 1,
    "semestral": 2,
    "trimestral": 4,
    "mensual": 12,
}

# La ON corporativa argentina en dolares paga cupon semestral. Es el supuesto
# por defecto cuando la licitacion no dice nada.
FRECUENCIA_DEFECTO = "semestral"


class DatosInsuficientes(ValueError):
    """No hay con que armar un flujo de fondos."""


@dataclass
class Flujo:
    """Un pago del bono, en unidades de valor nominal (VN = 100)."""
    periodo: int          # numero de periodo de cupon, 1..n
    anios: float          # cuando cae, en anios desde hoy
    renta: float          # intereses
    amortizacion: float   # capital
    saldo: float          # capital vivo DESPUES de este pago

    @property
    def total(self) -> float:
        return self.renta + self.amortizacion


@dataclass
class Resultado:
    precio: float
    tir: float | None                  # efectiva anual, en %
    tna: float | None                  # nominal anual, en %
    duration: float | None             # Macaulay, en anios
    duration_modificada: float | None  # en anios
    convexidad: float | None
    vida_promedio: float | None        # anios, ponderada por amortizacion
    flujos: list[Flujo] = field(default_factory=list)
    supuestos: list[str] = field(default_factory=list)
    confiable: bool = True


# ---------------------------------------------------------------------------
# Flujo de fondos
# ---------------------------------------------------------------------------

def armar_flujos(
    tasa_cupon: float,
    anios: float,
    frecuencia: str = FRECUENCIA_DEFECTO,
    amortizaciones: list[float] | None = None,
    vn: float = 100.0,
) -> list[Flujo]:
    """Arma el flujo de fondos de un bono.

    tasa_cupon    nominal anual, en porcentaje (8.5 = 8,5%)
    anios         plazo hasta el vencimiento
    amortizaciones  porcentaje del VN que amortiza en cada periodo, de largo n.
                    None = bullet (todo el capital al final).

    La renta de cada periodo se calcula sobre el capital vivo, que es lo que
    hace que un bono que amortiza pague cupones decrecientes.
    """
    m = FRECUENCIAS.get(frecuencia)
    if not m:
        raise DatosInsuficientes(
            f"Frecuencia desconocida: {frecuencia!r}. "
            f"Las que hay: {sorted(FRECUENCIAS)}"
        )
    if anios <= 0:
        raise DatosInsuficientes("El plazo tiene que ser mayor que cero.")

    n = max(1, round(anios * m))
    tasa_periodo = (tasa_cupon / 100.0) / m

    if amortizaciones is None:
        amortizaciones = [0.0] * (n - 1) + [100.0]
    if len(amortizaciones) != n:
        raise DatosInsuficientes(
            f"El plan de amortizacion tiene {len(amortizaciones)} cuotas y el "
            f"bono tiene {n} periodos."
        )
    if abs(sum(amortizaciones) - 100.0) > 1e-6:
        raise DatosInsuficientes(
            f"Las amortizaciones suman {sum(amortizaciones):.4f}% del VN y "
            f"tienen que sumar 100%."
        )

    flujos, saldo = [], vn
    for i in range(1, n + 1):
        renta = saldo * tasa_periodo
        amort = vn * amortizaciones[i - 1] / 100.0
        saldo -= amort
        flujos.append(Flujo(
            periodo=i,
            anios=i / m,
            renta=renta,
            amortizacion=amort,
            # El redondeo deja saldos del orden de 1e-14 que se ven feo.
            saldo=0.0 if abs(saldo) < 1e-9 else saldo,
        ))
    return flujos


# ---------------------------------------------------------------------------
# Valuacion
# ---------------------------------------------------------------------------

def valor_presente(flujos: list[Flujo], tir_efectiva: float) -> float:
    """Precio por cada 100 de VN, descontando a la TIR efectiva anual (en %)."""
    y = tir_efectiva / 100.0
    if y <= -1:
        raise DatosInsuficientes("La tasa de descuento no puede ser <= -100%.")
    return sum(f.total / (1 + y) ** f.anios for f in flujos)


def tir(flujos: list[Flujo], precio: float) -> float | None:
    """TIR efectiva anual en %, por biseccion.

    Se usa biseccion y no Newton a proposito: es mas lenta y no importa (son
    microsegundos), pero no diverge. Newton con un flujo raro puede irse a una
    tasa absurda y devolver un numero con toda la cara de ser correcto.
    """
    if precio <= 0 or not flujos:
        return None

    total = sum(f.total for f in flujos)
    if total <= precio:
        # No hay TIR positiva: se cobra menos de lo que se paga.
        return None

    bajo, alto = -0.99, 100.0          # -99% a 10.000% anual
    f_bajo = valor_presente(flujos, bajo * 100) - precio
    f_alto = valor_presente(flujos, alto * 100) - precio
    if f_bajo * f_alto > 0:
        return None

    for _ in range(200):
        medio = (bajo + alto) / 2
        valor = valor_presente(flujos, medio * 100) - precio
        if abs(valor) < 1e-10:
            break
        if valor > 0:
            bajo = medio
        else:
            alto = medio
    # Sin redondear: quien muestra el numero redondea. Redondear aca metia un
    # error en la duration, que se calcula descontando a esta misma tasa.
    return (bajo + alto) / 2 * 100


def duration(flujos: list[Flujo], tir_efectiva: float, precio: float) -> float:
    """Duration de Macaulay en anios: el plazo promedio ponderado por valor
    presente. Es el punto donde el efecto precio y el efecto reinversion se
    compensan."""
    y = tir_efectiva / 100.0
    ponderado = sum(f.anios * f.total / (1 + y) ** f.anios for f in flujos)
    return ponderado / precio


def convexidad(flujos: list[Flujo], tir_efectiva: float, precio: float) -> float:
    """Segunda derivada del precio respecto de la tasa, normalizada.

    Sirve para corregir la duration en movimientos grandes: la duration sola
    subestima la suba de precio cuando la tasa baja y exagera la caida cuando
    sube.
    """
    y = tir_efectiva / 100.0
    suma = sum(f.anios * (f.anios + 1) * f.total / (1 + y) ** (f.anios + 2)
               for f in flujos)
    return suma / precio


def vida_promedio(flujos: list[Flujo]) -> float:
    """Anios promedio ponderados por amortizacion de capital, sin descontar.

    Es lo que distingue de verdad a un bono que amortiza de un bullet: dos
    bonos al mismo plazo tienen vidas promedio muy distintas si uno devuelve
    capital antes.
    """
    capital = sum(f.amortizacion for f in flujos)
    if capital <= 0:
        return 0.0
    return sum(f.anios * f.amortizacion for f in flujos) / capital


# ---------------------------------------------------------------------------
# Conversiones de tasa
# ---------------------------------------------------------------------------

def tna_a_tea(tna: float, frecuencia: str = FRECUENCIA_DEFECTO) -> float:
    """Nominal anual -> efectiva anual, ambas en %.

    Es la diferencia que mas se pasa por alto al comparar dos licitaciones:
    una TNA de 10% semestral es 10,25% efectivo, y contra una mensual la
    brecha se abre mas.
    """
    m = FRECUENCIAS[frecuencia]
    return ((1 + (tna / 100.0) / m) ** m - 1) * 100.0


def tea_a_tna(tea: float, frecuencia: str = FRECUENCIA_DEFECTO) -> float:
    m = FRECUENCIAS[frecuencia]
    return (((1 + tea / 100.0) ** (1 / m)) - 1) * m * 100.0


# ---------------------------------------------------------------------------
# Sensibilidad
# ---------------------------------------------------------------------------

def sensibilidad(
    flujos: list[Flujo],
    tir_base: float,
    saltos_bp: tuple[int, ...] = (-300, -200, -100, -50, 50, 100, 200, 300),
) -> list[dict]:
    """Precio ante movimientos de tasa, exacto y por la aproximacion.

    Se devuelven los dos para que se vea donde la regla de bolsillo
    (duration x variacion) empieza a fallar. En un bono largo, a 300 bp la
    diferencia ya es visible, y esa diferencia es la convexidad.
    """
    precio_base = valor_presente(flujos, tir_base)
    dur = duration(flujos, tir_base, precio_base)
    dur_mod = dur / (1 + tir_base / 100.0)
    conv = convexidad(flujos, tir_base, precio_base)

    filas = []
    for bp in saltos_bp:
        delta = bp / 10000.0
        exacto = valor_presente(flujos, tir_base + bp / 100.0)
        aprox = precio_base * (1 - dur_mod * delta + 0.5 * conv * delta ** 2)
        filas.append({
            "bp": bp,
            "tir": round(tir_base + bp / 100.0, 4),
            "precio": round(exacto, 4),
            "variacion": round((exacto / precio_base - 1) * 100, 4),
            "precio_aprox": round(aprox, 4),
        })
    return filas


# ---------------------------------------------------------------------------
# Punto de entrada: analizar una licitacion del bot
# ---------------------------------------------------------------------------

def analizar(
    tasa_cupon: float | None,
    anios: float | None,
    *,
    precio: float = 100.0,
    frecuencia: str = FRECUENCIA_DEFECTO,
    amortizaciones: list[float] | None = None,
    tasa_es_variable: bool = False,
) -> Resultado | None:
    """Calcula todo para un instrumento. Devuelve None cuando no corresponde
    opinar, que es tan util como calcular bien."""
    if tasa_es_variable:
        return None
    if tasa_cupon is None or anios is None or anios <= 0:
        return None
    if tasa_cupon < 0:
        return None

    supuestos, confiable = [], True
    if amortizaciones is None:
        supuestos.append(
            "Se asume bullet: todo el capital al vencimiento. Si el bono "
            "amortiza antes, la duration real es menor."
        )
        confiable = False
    supuestos.append(f"Cupon {frecuencia}, convencion 30/360.")

    flujos = armar_flujos(tasa_cupon, anios, frecuencia, amortizaciones)
    y = tir(flujos, precio)
    if y is None:
        return None

    dur = duration(flujos, y, precio)
    return Resultado(
        precio=round(precio, 4),
        tir=round(y, 4),
        tna=round(tea_a_tna(y, frecuencia), 4),
        duration=round(dur, 4),
        duration_modificada=round(dur / (1 + y / 100.0), 4),
        convexidad=round(convexidad(flujos, y, precio), 4),
        vida_promedio=round(vida_promedio(flujos), 4),
        flujos=flujos,
        supuestos=supuestos,
        confiable=confiable,
    )


# Textos del campo valor_corte que delatan una tasa que no es fija. Un
# "MARGEN DE CORTE" es un spread sobre una tasa variable (Badlar, TAMAR): el
# flujo futuro depende de algo que no conocemos, y cualquier TIR seria
# inventada.
_MARCAS_VARIABLE = ("margen", "badlar", "tamar", "badcor", "cer", "tm20",
                    "variable", "spread")


def es_tasa_variable(reg: dict) -> bool:
    texto = " ".join(str(reg.get(c) or "") for c in
                     ("valor_corte", "variable_licitar", "titulo")).lower()
    return any(marca in texto for marca in _MARCAS_VARIABLE)


def desde_licitacion(reg: dict, **kw) -> Resultado | None:
    """Analiza una licitacion ya normalizada por modelo.py.

    Devuelve None -- sin explicar nada y sin inventar -- cuando el instrumento
    no admite el calculo: tasa variable, sin tasa de corte todavia, o sin
    fecha de vencimiento. Es el caso mas frecuente y no es un error.
    """
    if es_tasa_variable(reg):
        return None
    return analizar(
        reg.get("tasa_corte"),
        reg.get("plazo_anios"),
        **kw,
    )


def resumen_una_linea(res: Resultado | None) -> str:
    """La linea que va en el mail: lo justo para dimensionar el riesgo tasa."""
    if res is None:
        return ""
    salto = next(f for f in sensibilidad(res.flujos, res.tir, (100,)))
    linea = (f"duration modificada {res.duration_modificada:.2f} anios; "
             f"+100 bp de tasa ~ {salto['variacion']:.1f}% de precio")
    if not res.confiable:
        linea += " (asumiendo bullet)"
    return linea


def amortizacion_lineal(anios: float, frecuencia: str, desde_anio: float = 0.0
                        ) -> list[float]:
    """Plan de amortizacion en cuotas iguales, opcionalmente con periodo de
    gracia. Es la forma mas comun fuera del bullet, sobre todo en ON PyME."""
    m = FRECUENCIAS[frecuencia]
    n = max(1, round(anios * m))
    primera = max(1, round(desde_anio * m) + 1)
    if primera > n:
        raise DatosInsuficientes(
            f"La gracia ({desde_anio} anios) no deja ninguna cuota antes del "
            f"vencimiento ({anios} anios)."
        )
    cuotas = n - primera + 1
    plan = [0.0] * n
    for i in range(primera - 1, n):
        plan[i] = 100.0 / cuotas
    # El redondeo se acomoda en la ultima cuota para que sume exactamente 100.
    plan[-1] += 100.0 - sum(plan)
    return plan


# ---------------------------------------------------------------------------
# Uso suelto, sin el bot
# ---------------------------------------------------------------------------

def _tabla(res: Resultado) -> str:
    lineas = [
        f"  Precio                 {res.precio:>10.4f}  por cada 100 de VN",
        f"  TIR (efectiva anual)   {res.tir:>10.4f} %",
        f"  TNA equivalente        {res.tna:>10.4f} %",
        f"  Duration (Macaulay)    {res.duration:>10.4f}  anios",
        f"  Duration modificada    {res.duration_modificada:>10.4f}  anios",
        f"  Convexidad             {res.convexidad:>10.4f}",
        f"  Vida promedio          {res.vida_promedio:>10.4f}  anios",
        "",
        "  Si la tasa se mueve:",
        "    variacion    TIR      precio    cambio",
    ]
    for f in sensibilidad(res.flujos, res.tir):
        lineas.append(f"    {f['bp']:+5d} bp  {f['tir']:6.2f}%  {f['precio']:9.4f}"
                      f"  {f['variacion']:+7.2f}%")
    lineas += ["", "  Supuestos:"]
    lineas += [f"    - {s}" for s in res.supuestos]
    return "\n".join(lineas)


def _flujo_texto(res: Resultado, maximo: int = 24) -> str:
    lineas = ["  periodo   anios     renta   amortiz.     total     saldo"]
    for f in res.flujos[:maximo]:
        lineas.append(f"  {f.periodo:>7d}  {f.anios:>6.2f}  {f.renta:>8.4f}"
                      f"  {f.amortizacion:>9.4f}  {f.total:>8.4f}  {f.saldo:>8.4f}")
    if len(res.flujos) > maximo:
        lineas.append(f"  ... y {len(res.flujos) - maximo} periodos mas")
    return "\n".join(lineas)


def main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        description="Calculadora de bonos: TIR, duration, convexidad y "
                    "sensibilidad a la tasa.",
        epilog="Ejemplo:  python bonos.py --tasa 9.5 --anios 5 --precio 98.5",
    )
    p.add_argument("--tasa", type=float, required=True,
                   help="tasa de cupon nominal anual, en %% (9.5 = 9,5%%)")
    p.add_argument("--anios", type=float, required=True,
                   help="plazo hasta el vencimiento")
    p.add_argument("--precio", type=float, default=100.0,
                   help="precio por cada 100 de VN (por defecto 100, a la par)")
    p.add_argument("--frecuencia", default=FRECUENCIA_DEFECTO,
                   choices=sorted(FRECUENCIAS),
                   help=f"pagos de cupon por anio (por defecto {FRECUENCIA_DEFECTO})")
    p.add_argument("--amortiza-desde", type=float, default=None, metavar="ANIOS",
                   help="amortiza en cuotas iguales a partir de este anio; "
                        "sin esto se asume bullet")
    p.add_argument("--flujo", action="store_true",
                   help="ademas imprime el flujo de fondos periodo por periodo")
    args = p.parse_args(argv)

    plan = None
    if args.amortiza_desde is not None:
        plan = amortizacion_lineal(args.anios, args.frecuencia, args.amortiza_desde)

    res = analizar(args.tasa, args.anios, precio=args.precio,
                   frecuencia=args.frecuencia, amortizaciones=plan)
    if res is None:
        print("No se puede calcular con esos datos.")
        return 1

    forma = "bullet" if plan is None else f"amortiza desde el anio {args.amortiza_desde:g}"
    print(f"\nBono {args.tasa:g}% {args.frecuencia}, {args.anios:g} anios, {forma}\n")
    print(_tabla(res))
    if args.flujo:
        print("\n  Flujo de fondos por cada 100 de VN:")
        print(_flujo_texto(res))
    print()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
