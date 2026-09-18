# Monitor de licitaciones de ON

Vigila el mercado primario argentino, avisa por mail cuando aparece una
licitación nueva o se publica un resultado, y deja una planilla y un dashboard
actualizados.

## Qué mira

Dos fuentes, que se complementan:

| Fuente | Qué aporta | Cuándo llega |
|---|---|---|
| **A3 Mercados** (ex MAE) | La licitación completa: monto, tasa o margen de corte, adjudicado, colocadores | El día que abre la rueda |
| **CNV / AIF** | El aviso de suscripción, con el link a la publicación. Sin monto ni tasa | Uno a tres días hábiles antes |

A3 es el grueso; la CNV es la que da tiempo a reaccionar. Con las dos activas
vas a ver la misma emisión dos veces: primero el aviso, después la licitación.
Es a propósito, y el mail las separa en secciones distintas. Se apagan
individualmente en `fuentes:` dentro de `config.yaml`.

### Moneda

El monitor sigue solo las monedas que estén en `monedas:`. **Por defecto la
lista está vacía, o sea que no filtra**: entra el mercado primario completo, en
pesos, dólares, dólar-linked y UVA. Poniendo `USD` y `USD linked` se queda solo
con lo que tiene riesgo dólar, y lo que no esté en la lista queda afuera del
mail, de la planilla, del dashboard y del histórico guardado.

Si activás el filtro, aparece un detalle: **los avisos de la CNV no traen campo
de moneda**. A veces el título la dice y la mayoría de las veces no. Descartar
lo que no se puede clasificar apagaría justamente la anticipación de uno a tres
días que es lo único que aporta esa fuente, así que por defecto esos avisos
pasan y se marcan *Moneda: a confirmar* — una aclaración que solo aparece
cuando el filtro está puesto, porque sin filtro no explica nada. Cuando la
misma emisión aparece en A3 un día después, ahí sí viene la moneda; si es en
pesos, deja de figurar sola. Quien
prefiera el filtro estricto pone `moneda_desconocida: ignorar` y se queda solo
con lo que declara la moneda — en la práctica, casi solo A3.

`USD linked` está separada de `USD` a propósito: se integra y se paga en pesos
al dólar oficial, así que no es lo mismo que una hard dollar. Sacarla de la
lista deja solo las que se suscriben en dólares.

### Calculadora de bonos

`bonos.py` calcula sobre un flujo de fondos: TIR, duration de Macaulay y
modificada, convexidad, vida promedio y qué pasa con el precio si la tasa se
mueve. Se usa de tres formas: suelta desde la terminal, como panel del
dashboard, y como una línea en el mail de cada licitación que lo admita.

```
python bonos.py --tasa 9.5 --anios 5 --precio 98.5
python bonos.py --tasa 8 --anios 10 --amortiza-desde 3 --flujo
```

Lo que importa es **cuándo no calcula**:

| Caso | Qué hace |
|---|---|
| Tasa fija con plan de amortización cargado | Exacto |
| Tasa fija sin plan (lo normal en una licitación recién adjudicada) | Estima asumiendo *bullet* y lo dice. Si el bono amortiza, la duration real es **menor** |
| Margen de corte, Badlar, TAMAR, CER | **No calcula**: el flujo futuro depende de una tasa que nadie conoce. La fila no aparece en el mail |
| Sin tasa de corte o sin vencimiento | No calcula |

La TIR es efectiva anual y la duration modificada es Macaulay / (1 + TIR), que
es la convención de las pantallas locales. Una terminal que compone
semestralmente informa un número algo mayor; no es que una esté mal, son dos
convenciones.

El panel del dashboard es un port a JavaScript de las mismas fórmulas, para
poder recalcular sin regenerar el archivo. `autotest.py` corre las dos
implementaciones contra los mismos casos y falla si se separan más de 1e-8.

Sumar una fuente nueva es escribir un módulo con una función
`traer_licitaciones()` que devuelva dicts con los campos del modelo, y
agregarlo a `FUENTES` en `fuentes.py`. Ningún otro archivo se toca: el resto
del bot trabaja sobre el modelo normalizado y no sabe de dónde salió cada
registro.

### A3 Mercados

Una API JSON pública sin autenticación:

```
GET https://api.marketdata.mae.com.ar/api/mercado/licitacionesporestado/Todos
    ?oTitulo={"estado":"A","fechaDesde":"...","fechaHasta":"..."}
```

Es la misma fuente que alimenta `marketdata.mae.com.ar/licitaciones`, y cubre
obligaciones negociables (corporativas, PyME CNV bajo y mediano impacto, y
vinculadas a sostenibilidad), fideicomisos financieros y deuda pública
nacional, provincial y municipal. Trae 30 campos por licitación, incluidos
monto adjudicado y tasa o margen de corte.

### Cosas de la API que conviene saber

Estas se verificaron contra la API real y están explicadas en los comentarios
de `fuente_a3.py`. Importan si algún día algo deja de andar:

1. **Los parámetros de fecha se ignoran.** La API siempre devuelve una ventana
   móvil de unos 30 días, sin importar el rango que se pida. Por eso el
   histórico se acumula en `datos/estado.json`, que nunca se poda: si el bot
   deja de correr más de un mes, ese hueco no se puede recuperar.
2. **El campo `estado` del payload siempre dice "Activa"**, incluso en
   licitaciones cerradas hace semanas. Está roto. El estado real es el bucket
   con el que se consultó (A, C o F).
3. **Un mismo id puede venir en dos buckets a la vez** (3 de 84 registros en la
   muestra del 10/09/2026). Se deduplica quedándose con el estado más avanzado.
4. **"DESIERTA" no tiene campo propio**: viene escrito adentro de
   `valor_Corte`. Se detecta por texto y cuenta como resultado.
5. La API **no expone los PDF** de los avisos de suscripción.

### CNV / AIF

Se lee `https://www.cnv.gov.ar/SitioWeb/HechosRelevantes`, que es HTML
renderizado en el servidor: cinco tablas con fecha, entidad, descripción y
número de documento, más un enlace por fila a la publicación completa en
`aif2.cnv.gov.ar`.

- El parser **no se apoya en ids ni clases de CSS**. Recorre las tablas, lee
  los encabezados y mapea las columnas por nombre, así que sobrevive a un
  rediseño del sitio. Si no encuentra ninguna tabla reconocible **falla con un
  error explícito** en vez de devolver una lista vacía: un feed tranquilo y un
  parser roto se ven igual desde afuera, y confundirlos significa dejar de
  avisar sin que nadie se entere.
- El feed trae de todo (designaciones, actas, garantías). Solo pasan las
  publicaciones que parecen emisión de deuda, y quedan afuera las que mencionan
  ON pero no son colocaciones: ofertas de compra, rescates, canjes, pagos de
  renta. Los patrones están arriba de `fuente_cnv.py` y se ajustan ahí.
- No hay paginación ni filtro de fechas: la página muestra las últimas
  publicaciones y nada más.

Validada contra el sitio real el 11/09/2026: leyó 186 publicaciones en una
corrida. Para ver el detalle de qué parsea y qué filtra:

```bash
python fuente_cnv.py --diagnostico
```

Imprime paso a paso: cuánto HTML bajó, qué tablas encontró, qué columnas
mapeó, qué publicaciones parseó y cuáles pasaron el filtro de emisión.

**Ese último número es el que conviene mirar.** El feed trae de todo y el
filtro deja pasar poco por diseño, pero si de 180 publicaciones pasa sólo una
o dos, puede ser que `PATRONES_EMISION` se esté quedando corto para cómo
redacta la CNV sus títulos. El diagnóstico muestra las publicaciones que NO
pasaron: si entre ellas hay avisos de suscripción o suplementos de prospecto,
hay que agregar el patrón que les falta.

## Instalación

```bash
pip install -r requirements.txt
```

Python 3.10 o superior.

## Puesta en marcha, paso a paso

Los comandos son de PowerShell en Windows. Si `python` no está en el PATH,
reemplazalo por la ruta completa al ejecutable.

**1. Descomprimir e instalar las dependencias.**

```powershell
cd C:\Users\fgalante\licitaciones-bot
python -m pip install -r requirements.txt
```

**2. Probar que la máquina llega a las fuentes.** Sin configurar nada todavía:

```powershell
python bot.py --chequear-red
```

Prueba fuente por fuente y, si alguna falla, hace un chequeo por capas — DNS
general, resolución de ese dominio, conexión TCP, proxy — y dice en cuál se
corta. Es lo primero que conviene correr en una máquina nueva: descarta que el
problema sea de red antes de ponerse a mirar la configuración del mail.

Si todo responde, podés ver qué avisaría sin mandar nada:

```powershell
python bot.py --dry-run -v
```

**3. Cargar las credenciales del mail** como variables de entorno del usuario.
`setx` las deja guardadas para siempre; hay que **abrir una terminal nueva**
después, porque no afecta a la que está abierta.

```powershell
setx SMTP_USUARIO "franco.galante@crecersgr.com.ar"
setx SMTP_PASSWORD "las16letrasdelapp"
```

En Gmail o Google Workspace `SMTP_PASSWORD` **no es la clave de la cuenta**:
hay que generar una contraseña de aplicación de 16 caracteres, y para eso la
cuenta necesita verificación en dos pasos activada. Si el administrador de
`@crecersgr.com.ar` no permite contraseñas de aplicación, apuntar `smtp_host`
en `config.yaml` al servidor de correo de la empresa.

**4. Ajustar `config.yaml`**: a quién le llega, qué categorías disparan mail,
qué fuentes usar. Los valores por defecto ya sirven.

**5. Primera corrida, en modo inicial.**

```powershell
python bot.py --init
```

Llena el estado con la ventana vigente **sin mandar mail**. Sin esto, el primer
correo llegaría con las ~80 licitaciones de la ventana de golpe.

**6. Probar que el mail sale.**

```powershell
python bot.py --resumen
```

`--resumen` fuerza el envío aunque no haya novedades. Si llega, está andando.

**7. De ahí en adelante**, la corrida normal es simplemente:

```powershell
python bot.py
```

que solo avisa lo que cambió desde la corrida anterior. El paso siguiente es
programarla — ver más abajo.

### Variables de entorno

| Variable | Para qué |
|---|---|
| `SMTP_USUARIO` | casilla desde la que sale el mail |
| `SMTP_PASSWORD` | contraseña de aplicación |
| `BOT_DESTINATARIOS` | opcional; pisa la lista de `config.yaml`, separada por comas |

## Uso

| Comando | Qué hace |
|---|---|
| `python bot.py` | corrida normal: avisa sólo lo que cambió |
| `python bot.py --init` | llena el estado sin avisar |
| `python bot.py --dry-run` | muestra el mail por pantalla, no envía ni guarda |
| `python bot.py --resumen` | manda el aviso aunque no haya novedades |
| `python bot.py --informe` | manda el informe: el panorama, para reenviar |
| `python bot.py --guardar-crudo` | además deja el JSON crudo, para diagnóstico |
| `python bot.py --chequear-red` | prueba si esta máquina llega a cada fuente |
| `python bot.py --probar-mail` | prueba sólo las credenciales del correo |
| `python bot.py -v` | log detallado |
| `python autotest.py` | prueba todo sin red ni mail |
| `python demo.py` | dashboard de muestra con datos fabricados |
| `python fuente_cnv.py --diagnostico` | valida la fuente de la CNV paso a paso |

Códigos de salida: `0` ok · `1` error de configuración o escritura ·
`2` no se pudo consultar **ninguna** fuente · `3` había novedades pero falló el
mail · `4` corrida parcial, alguna fuente no respondió.

El `4` existe para que una fuente caída se note. Si la CNV deja de responder,
el bot sigue avisando lo de A3 y todo parece normal — salvo que dejaste de
anticipar. El mail también lo dice, arriba del pie.

## Qué genera

```
datos/
  estado.json         memoria del bot (el histórico completo; no borrar)
                      indexado por "fuente:id"; un estado de la versión
                      anterior se migra solo, sin re-avisar nada
  licitaciones.csv    planilla, formato argentino, delimitador ;
  licitaciones.xlsx   la misma planilla con tabla y filtros
  dashboard.html      dashboard autocontenido, se abre con doble clic
```

El dashboard no necesita internet ni servidor: los datos van embebidos en el
archivo. Tiene tiles de resumen, un gráfico de tasas de corte de ON (filtrable
por moneda, porque una tasa en pesos y una en dólares no se leen en el mismo
eje), colocaciones por semana y una tabla filtrable y ordenable.

## Los dos mails

El bot manda dos correos distintos, para dos usos distintos.

| | Aviso de novedades | Informe |
|---|---|---|
| Comando | `python bot.py` | `python bot.py --informe` |
| Qué dice | Lo que cambió desde la corrida anterior | El panorama del mercado |
| Para quién | Vos, que seguís el mercado | Cualquiera, incluso sin contexto |
| Cuándo | Cada 2 horas durante la rueda | Una vez por día, al cierre |

**El informe está escrito para reenviar.** Se lee completo en el cuerpo del
mail —qué está en rueda, qué se anunció, qué cerró y a qué tasa— sin abrir
ningún archivo, y trae al pie una explicación de qué es y de dónde salen los
datos, porque quien lo recibe reenviado no sabe que este bot existe.

Esa decisión es deliberada: muchos filtros corporativos bloquean los adjuntos
`.html` de mails que vienen de afuera, así que un informe que dependiera del
adjunto no llegaría. El adjunto es un extra para quien quiera hurgar, nunca la
única forma de enterarse.

Se configura aparte del aviso, con sus propios destinatarios:

```yaml
informe_dias: 7
asunto_informe: "Licitaciones ON - {fecha}"
nombre_remitente: "Monitor de licitaciones"

destinatarios_informe: []      # vacío = los mismos del aviso
copia_informe: []
copia_oculta_informe: []
adjuntar_informe: []           # dashboard | xlsx | csv
```

Dejando `destinatarios_informe` vacío te llega a vos y lo reenviás a mano,
que es lo más simple para arrancar. Cuando confíes en el formato, podés sumar
a tus colegas en `copia_oculta_informe` y que les llegue solo.

Para programarlo al cierre de la rueda, una tarea aparte:

```
schtasks /create /tn "Informe licitaciones ON" /tr "C:\...\run_informe.bat" /sc weekly /d MON,TUE,WED,THU,FRI /st 17:00
```

## Cómo ver el dashboard

No hay nada que desplegar: es un archivo HTML autocontenido. Doble clic y
listo. Las opciones de abajo son solo para que lo vea alguien más.

### Que llegue por mail

La forma más simple, y la que sirve también para compartirlo con otros. En
`config.yaml`:

```yaml
destinatarios:
  - franco.galante@crecersgr.com.ar
copia:
  - mesa@crecersgr.com.ar
copia_oculta:
  - inversor1@ejemplo.com
  - inversor2@ejemplo.com

adjuntar:
  - dashboard
```

En `destinatarios` y `copia` las direcciones se ven entre sí; en
`copia_oculta`, no — cada uno recibe el mail sin ver al resto de la lista. Para
avisarle a varios inversores o socios, van ahí.

Cada aviso viaja con el HTML pegado, actualizado. Sin servidor ni carpetas
compartidas. Dos advertencias: algunos filtros corporativos bloquean los
adjuntos `.html` (si no llega, probá con `xlsx`), y si el archivo supera los
8 MB el bot lo saltea y lo dice en el log, para no chocar contra el límite del
servidor de correo.

### Carpeta de red compartida

La opción para el equipo interno. En `config.yaml`:

```yaml
copiar_a:
  - \\servidor\finanzas\licitaciones
```

Después de cada corrida, el dashboard y las dos planillas se copian ahí. El
resto del equipo abre `\\servidor\finanzas\licitaciones\dashboard.html` —esa
ruta se pega en un mail o un chat y abre con un clic— y el control de acceso es
el de la carpeta. Cero infraestructura y nada sale de la empresa.

Las salidas se generan **siempre local primero** y después se copian, así que
si el servidor no responde igual tenés tu copia, el mail sale lo mismo y el
problema se reporta en el log y en el pie del correo. La copia va a un
temporal y recién ahí se renombra, para que nadie abra un dashboard a medio
escribir.

Para verificar que la ruta y los permisos están bien, antes de programar nada:

```powershell
python bot.py --resumen
dir \\servidor\finanzas\licitaciones
```

#### La trampa del Programador de tareas

**Una tarea que corre como SYSTEM no ve las unidades de red.** Es la causa
número uno de "anda a mano pero no anda programado": la corrida manual usa tu
sesión, que tiene el share montado, y la programada no.

Al crear la tarea hay que elegir tu usuario, no SYSTEM ni "Usuarios". Desde el
Programador gráfico: pestaña *General* → *Cambiar usuario o grupo* → tu
usuario, y dejar marcado **"Ejecutar sólo cuando el usuario haya iniciado
sesión"**. Si necesitás que corra con la sesión cerrada, hay que marcar la otra
opción y guardar la contraseña de Windows, y además usar la ruta UNC completa
—nunca una letra de unidad mapeada (`Z:\`), porque los mapeos son por sesión y
la tarea no los tiene.

Por eso `run_bot.bat` usa `pushd` en lugar de `cd`: `cd` no funciona con rutas
UNC.

### Un link para compartir (GitHub Pages)

Es la única forma de tener un link que abra desde un celular, un WhatsApp o
fuera de la oficina: una ruta `\\servidor\...` no sirve para nada de eso.

Publicar en Pages implica que el bot corra en GitHub Actions, porque es quien
publica. De paso resuelve dos problemas: deja de depender de que tu máquina
esté prendida, y los runners de GitHub **sí llegan a la CNV**, así que
recuperás los avisos anticipados que tu red bloquea.

**Antes de empezar, lo que hay que tener claro:** una GitHub Page es **pública
y sin contraseña**, aunque el repo sea privado. Un link compartido por WhatsApp
se reenvía solo. Lo que queda expuesto son licitaciones, montos y tasas de
corte —datos de mercado que ya son públicos, sin ningún dato de clientes ni de
la SGR—, pero la decisión es tuya.

Los pasos:

1. **Creá un repositorio privado** en GitHub y subí esta carpeta.
2. **Cargá los secretos** en *Settings → Secrets and variables → Actions*:
   `SMTP_USUARIO`, `SMTP_PASSWORD` y, si querés, `BOT_DESTINATARIOS`.
3. **Activá Pages** en *Settings → Pages → Source: **GitHub Actions***. Este
   paso va antes del primer run: sin él, el job `publicar` falla (el mail sale
   igual, porque es otro job).
4. **Poné la URL en `config.yaml`** — es predecible, `https://<usuario>.github.io/<repo>/`:

   ```yaml
   url_publica: "https://fgalante.github.io/licitaciones-bot/"
   ```

   Sirve para la vista previa del link en los chats. Commiteá el cambio.
5. **Primera corrida a mano**: pestaña *Actions* → *Monitor de licitaciones* →
   *Run workflow*, marcando **init** para que llene el estado sin mandar un
   mail con toda la ventana.

De ahí en más corre solo cada dos horas en días hábiles y republica el
dashboard en cada corrida.

#### La vista previa en los chats

El dashboard se genera con las etiquetas Open Graph que leen WhatsApp, Slack y
Teams, así que el link no aparece como una URL pelada: muestra el título y una
descripción armada con los datos de esa corrida, del estilo
*"12 en rueda · corte mediano 38,20% · actualizado 10/09/2026 16:00"*.

Quien recibe el link ve algo útil aunque no entre. El dashboard además está
hecho para el teléfono: a 400px de ancho las tarjetas se apilan de a dos y los
gráficos se deslizan en lugar de comprimirse.

#### Sin encender Pages

El workflow igual sube el dashboard y el xlsx como artefacto de cada corrida:
se bajan desde la pestaña Actions y quedan 30 días. Sirve para el equipo, no
para compartir un link.

### Lo que no sirve

Google Drive y OneDrive **no** renderizan un HTML: te lo hacen descargar en vez
de mostrarlo. Como carpeta sincronizada andan bien, pero el link compartido no
abre el dashboard en el navegador.

## Cada cuánto correrlo

Las licitaciones abren cerca de las 10:00 y cierran entre las 16:00 y las
16:30, hora Argentina, y la ventana entera transcurre el mismo día. Para
enterarse mientras todavía se puede entrar, hay que correrlo **durante la
rueda**: cada una o dos horas de 10 a 18, días hábiles.

Correrlo una vez por día sirve para llevar el registro y ver resultados, pero
llega tarde para operar.

### Windows (Programador de tareas)

`run_bot.bat` ya está armado; hay que editar las dos primeras líneas con la
ruta de Python y la carpeta. Después, en una terminal **como administrador**:

```
schtasks /create /tn "Monitor licitaciones ON" /tr "C:\Users\fgalante\licitaciones-bot\run_bot.bat" /sc weekly /d MON,TUE,WED,THU,FRI /st 10:00 /ri 120 /du 08:00
```

Eso arranca a las 10:00 de lunes a viernes y repite cada 120 minutos durante 8
horas: 10, 12, 14, 16 y 18. Para verificar que quedó bien:

```
schtasks /query /tn "Monitor licitaciones ON" /v /fo LIST
```

Y para probarla a mano sin esperar al horario:

```
schtasks /run /tn "Monitor licitaciones ON"
```

Cada corrida deja su log en `datos\bot.log`. Si preferís no pelear con la línea
de comandos, la misma tarea se arma desde el Programador de tareas gráfico:
disparador semanal, lunes a viernes, a las 10:00, con "repetir cada 2 horas
durante 8 horas".

Corre sólo con la máquina prendida y sin suspender.

### GitHub Actions

`.github/workflows/monitor.yml` corre cada dos horas en días hábiles, sin
depender de ninguna máquina. Hay que cargar `SMTP_USUARIO`, `SMTP_PASSWORD` y
`BOT_DESTINATARIOS` en *Settings → Secrets and variables → Actions*. El
workflow commitea `datos/` de vuelta al repo, que es como sobrevive el estado
entre corridas.

Ojo con esto: si el repo es público, el histórico de licitaciones queda
público. Para un repo privado no hay problema.

## Si algo falla

Ante cualquier duda, el primer comando es `python bot.py --chequear-red`.

### Problemas de red

Los tres fallos se parecen en pantalla pero se arreglan distinto:

**`getaddrinfo failed` / `NameResolutionError` / `Failed to resolve`** — es
**DNS**: la máquina no logró traducir el nombre a una IP y nunca llegó a
intentar la conexión. No es el sitio bloqueándote. Para ubicar la causa:

```powershell
ping 8.8.8.8                      # ¿hay internet?
nslookup www.cnv.gov.ar           # ¿resuelve con el DNS de la empresa?
nslookup www.cnv.gov.ar 8.8.8.8   # ¿resuelve con un DNS público?
```

Si el último anda y el anterior no, el DNS corporativo bloquea el dominio. Si
el navegador entra al sitio pero Python no, casi seguro hay un **proxy** que
Chrome usa por política y Python no ve: buscalo en *Configuración → Red e
Internet → Proxy* y declaralo.

**`ProxyError` / `Tunnel connection failed: 403`** — hay proxy y rechaza ese
destino: el dominio no está en la lista blanca, o faltan credenciales. Es una
decisión de red, no algo que el bot pueda esquivar. Hay que pedir que lo
habiliten o correr el bot desde otra red.

**`certificate verify failed`** — red con inspección SSL: el proxy firma el
tráfico con su propio certificado. Apuntá `REQUESTS_CA_BUNDLE` al `.pem` de la
empresa.

### Configurar el proxy

```powershell
setx BOT_PROXY "http://usuario:clave@proxy.empresa:8080"
```

`BOT_PROXY` tiene prioridad sobre todo lo demás y es donde van las credenciales
—  nunca en `config.yaml`. Para un proxy sin autenticación alcanza con la opción
`proxy` de `config.yaml`. Si no se declara ninguno, se respeta la variable
`HTTPS_PROXY` del sistema. El bot muestra el proxy en uso con la contraseña
tapada al arrancar el chequeo.

### Otros

**Sale con código 4** — una fuente no respondió y las demás sí. El log y el
mail dicen cuál. Si se repite varias corridas seguidas, revisar esa fuente.

**`FuenteNoDisponible` con 403** — la red desde la que corre no llega a
`api.marketdata.mae.com.ar`. Pasa detrás de proxies corporativos con lista
blanca. Se prueba con `curl -I https://api.marketdata.mae.com.ar`.

**La CNV devuelve "No encontré ninguna tabla"** — cambió la estructura de la
página. Correr `python fuente_cnv.py --diagnostico` para ver qué tablas hay y
qué columnas detecta, y ajustar los alias en `_indice_columnas`.

**Llegan avisos de la CNV que no son emisiones** (o faltan los que sí) — el
filtro son las listas `PATRONES_EMISION` y `PATRONES_EXCLUIR` arriba de
`fuente_cnv.py`. Agregar o sacar patrones ahí; el diagnóstico muestra qué pasa
y qué no.

**`FuenteNoDisponible` con 400** — cambió el formato del parámetro `oTitulo`.
Hay que abrir `marketdata.mae.com.ar/licitaciones` con la consola del
navegador en la pestaña de red y mirar cómo quedó la llamada.

**El mail no sale, error de autenticación** — casi siempre es que
`SMTP_PASSWORD` tiene la clave de la cuenta en vez de una contraseña de
aplicación.

**Llegan avisos repetidos** — se borró o se corrompió `datos/estado.json`. Si
se corrompió, el bot lo deja como `estado.json.corrupto` y arranca de cero;
conviene correr una vez con `--init` para no recibir el aluvión.

**No llega nada nunca** — verificar con `--dry-run` que haya eventos, y
después revisar `categorias_aviso` y `eventos_aviso` en `config.yaml`. Por
defecto los fideicomisos y la deuda pública van a la planilla pero no al mail.

## Qué no cubre

- **Las colocaciones que van exclusivamente por BYMA o por el MAV.** La mayoría
  del primario de renta fija pasa por A3 o se publica en la CNV, pero no todo.
- **Los PDF en sí.** De la CNV llega el link a la publicación, no el archivo.
- **Vincular el aviso con su licitación.** El aviso de la CNV y la licitación de
  A3 de la misma emisión conviven como dos registros separados; el bot no
  intenta emparejarlos. En el dashboard, los conteos y los volúmenes excluyen
  los avisos para no contar dos veces la misma emisión.

Las dos primeras se resuelven agregando fuentes nuevas, como se explica arriba.
