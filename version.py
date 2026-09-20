"""Version del bot.

Existe porque el paquete se entrega como zip y es facil terminar corriendo una
copia vieja sin darse cuenta: el sintoma tipico es un argumento que "no
existe". Con esto se despeja en un comando:

    python bot.py --version
"""

VERSION = "1.14.3"

# Que trajo cada version, de la mas nueva a la mas vieja.
CAMBIOS = [
    ("1.14.3", "El workflow suma un tilde para forzar el envio del mail aunque "
               "no haya novedades, que es la unica forma de probarlo un fin de "
               "semana."),
    ("1.14.2", "Un secreto pegado con un salto de linea al final ya no tumba "
               "la corrida: se limpia al cargar, y un espacio adentro de la "
               "casilla se rechaza nombrando el secreto."),
    ("1.14.1", "El dashboard se lee bien en el celular: la tabla de detalle "
               "pasa a fichas, los campos dejan de disparar el zoom de iOS y "
               "la tabla de la calculadora ya no desborda la pagina."),
    ("1.14.0", "Vuelve a seguir todo el mercado primario: todas las monedas y "
               "todas las categorias, deuda publica incluida, tambien en el "
               "mail de aviso."),
    ("1.13.0", "Calculadora de bonos: TIR, duration, convexidad y sensibilidad "
               "a la tasa, en el dashboard, en el mail y como comando suelto "
               "(python bonos.py)."),
    ("1.12.0", "El monitor sigue solo las monedas configuradas (por defecto "
               "dolares) en el mail, la planilla, el dashboard y el historico."),
    ("1.11.1", "Las tablas de Fideicomisos y PIC tienen encabezados propios y "
               "la descripcion en otra columna: se leian corridas. Y el nombre "
               "de la entidad ya no alcanza para declarar una emision."),
    ("1.11.0", "La CNV se lee entera: las tablas sin fila de encabezados ya no "
               "se ignoran (eran 152 publicaciones de 162) y el filtro deja "
               "afuera rescates, recompras, canjes y avisos de pago."),
    ("1.10.1", "El diagnostico de la CNV ahora lista tambien las publicaciones "
               "que NO pasaron el filtro, que es lo que hace falta para calibrarlo."),
    ("1.10.0", "El aviso dice donde queda la tasa contra el mercado (percentil y "
               "mediana de comparables) y el dashboard suma la curva tasa-plazo."),
    ("1.9.0", "Las novedades que no se pudieron avisar ya no se dan por "
              "avisadas: se reintentan en la corrida siguiente."),
    ("1.8.1", "El diagnostico de credenciales aclara el largo real cuando la "
              "clave trae espacios."),
    ("1.8.0", "Comando --probar-mail y diagnostico de credenciales que dice "
              "que esta mal sin exponer la contrasena."),
    ("1.7.1", "Arreglo de empaquetado: el zip traia una carpeta con un nombre "
              "invalido en Windows que cortaba la extraccion."),
    ("1.7.0", "Vista previa del link en chats (Open Graph) y publicacion en "
              "GitHub Pages activada en el workflow."),
    ("1.6.0", "Mail de informe (--informe): el panorama del mercado escrito "
              "para reenviar, con destinatarios y adjuntos propios."),
    ("1.5.0", "Copia de las salidas a carpetas compartidas (copiar_a); una "
              "salida que falla ya no frena la corrida ni el aviso."),
    ("1.4.0", "Copia y copia oculta en el mail, para avisar a varias personas "
              "sin exponer la lista."),
    ("1.3.0", "Diagnostico de red por capas (--chequear-red), soporte de proxy, "
              "modulo red.py comun a las fuentes."),
    ("1.2.0", "Adjuntar dashboard/planilla al mail, publicacion en GitHub Pages."),
    ("1.1.0", "Fuente CNV/AIF (avisos anticipados), bot multi-fuente, "
              "estado indexado por fuente:id."),
    ("1.0.0", "Monitoreo de A3 Mercados, mail de novedades, planilla y dashboard."),
]


def linea() -> str:
    return f"Monitor de licitaciones {VERSION}"


def detalle() -> str:
    partes = [linea(), ""]
    for v, que in CAMBIOS:
        marca = "->" if v == VERSION else "  "
        partes.append(f"  {marca} {v}  {que}")
    return "\n".join(partes)
