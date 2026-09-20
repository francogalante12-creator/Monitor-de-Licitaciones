"""
Genera un dashboard HTML autocontenido con el historico de licitaciones.

Un solo archivo, sin dependencias externas ni CDN: se abre con doble clic,
funciona sin internet y se puede mandar por mail o dejar en una carpeta
compartida. Los datos van embebidos como JSON y todo el render lo hace el
JavaScript de la propia pagina.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from html import escape as html_escape
from pathlib import Path
from typing import Iterable

from formato import a_local, fecha_hora, formato_ar

log = logging.getLogger(__name__)


def preparar_datos(registros: Iterable[dict]) -> list[dict]:
    """Pasa los registros a algo serializable, con fechas locales en ISO."""
    salida = []
    for r in registros:
        inicio = a_local(r.get("fecha_inicio"))
        venc = a_local(r.get("fecha_vencimiento"))
        liq = a_local(r.get("fecha_liquidacion"))
        licitado = r.get("monto_licitar")
        adjudicado = r.get("monto_adjudicado")

        salida.append({
            "id": r.get("id"),
            "fuente": r.get("fuente", "a3"),
            "enlace": r.get("enlace", ""),
            "titulo": r.get("titulo", ""),
            "emisor": r.get("emisor", ""),
            "categoria": r.get("categoria", ""),
            "tipo": r.get("tipo", ""),
            "estado": r.get("estado", ""),
            "moneda": r.get("moneda_monto") or r.get("moneda") or "",
            "licitado": licitado,
            "adjudicado": adjudicado,
            "ratio": (adjudicado / licitado) if licitado and adjudicado else None,
            "tasa": r.get("tasa_corte"),
            "corte": r.get("valor_corte", ""),
            "desierta": bool(r.get("desierta")),
            # Plazo en años desde la liquidación hasta el vencimiento: es el
            # eje que importa para leer una curva de crédito.
            "plazo": round((venc - (liq or inicio)).days / 365.25, 2)
                     if venc and (liq or inicio) and venc > (liq or inicio) else None,
            "inicio": inicio.strftime("%Y-%m-%d") if inicio else "",
            "hora": inicio.strftime("%H:%M") if inicio else "",
            "liquidacion": liq.strftime("%Y-%m-%d") if liq else "",
            "vencimiento": venc.strftime("%Y-%m-%d") if venc else "",
            "colocador": r.get("colocador", ""),
            "sistema": r.get("sistema_adjudicacion", ""),
            "variable": r.get("variable_licitar", ""),
            "obs": r.get("observaciones", ""),
        })
    return salida


def _meta_social(datos: list[dict], generado: datetime, url: str = "") -> str:
    """Las etiquetas que WhatsApp, Slack y Teams leen para armar la vista previa.

    Sin esto, un link pegado en un chat se ve como una URL pelada y nadie lo
    abre. Con la descripcion armada a partir de los datos de la corrida, la
    previa ya dice algo util aunque el otro no entre.
    """
    activas = [r for r in datos if r.get("estado") == "Activa"]
    on = [r for r in datos if r.get("categoria", "").startswith("ON")]
    tasas = sorted(r["tasa"] for r in on
                   if r.get("tasa") is not None
                   and str(r.get("moneda", "")).startswith("ARS"))
    partes = []
    if activas:
        partes.append(f"{len(activas)} en rueda")
    if tasas:
        m = tasas[len(tasas) // 2] if len(tasas) % 2 else \
            (tasas[len(tasas) // 2 - 1] + tasas[len(tasas) // 2]) / 2
        partes.append(f"corte mediano {formato_ar(m, 2)}%")
    partes.append(f"actualizado {fecha_hora(generado)}")

    desc = html_escape(" · ".join(partes))
    titulo = "Licitaciones de obligaciones negociables"

    etiquetas = [
        f'<meta name="description" content="{desc}">',
        f'<meta property="og:title" content="{titulo}">',
        f'<meta property="og:description" content="{desc}">',
        '<meta property="og:type" content="website">',
        '<meta property="og:locale" content="es_AR">',
        '<meta name="twitter:card" content="summary">',
        '<meta name="theme-color" content="#1a5f8a">',
    ]
    if url:
        etiquetas.append(f'<meta property="og:url" content="{html_escape(url)}">')

    return "\n".join(etiquetas)


def escribir(registros: Iterable[dict], ruta: Path, generado: datetime,
             url_publica: str = "") -> Path:
    datos = preparar_datos(registros)
    # </script> dentro de una cadena JSON cerraria el bloque antes de tiempo.
    payload = json.dumps(datos, ensure_ascii=False).replace("</", "<\\/")

    try:
        from version import VERSION
    except ImportError:
        VERSION = "?"

    html = (
        PLANTILLA
        .replace("__META__", _meta_social(datos, generado, url_publica))
        .replace("__DATOS__", payload)
        .replace("__GENERADO__", fecha_hora(generado))
        .replace("__VERSION__", VERSION)
    )

    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(html, encoding="utf-8")
    log.info("Dashboard escrito: %s (%d licitaciones).", ruta, len(datos))
    return ruta


PLANTILLA = r"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Licitaciones de obligaciones negociables</title>
__META__
<style>
:root{
  color-scheme: light;
  --page:#f9f9f7; --surface:#fcfcfb;
  --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,.10);
  --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100; --s5:#e87ba4; --s6:#008300;
  --good:#0ca30c; --critical:#d03b3b; --warning:#fab219;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    color-scheme: dark;
    --page:#0d0d0d; --surface:#1a1a19;
    --ink:#ffffff; --ink2:#c3c2b7; --muted:#898781;
    --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
    --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --s5:#d55181; --s6:#008300;
  }
}
:root[data-theme="dark"]{
  color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19;
  --ink:#ffffff; --ink2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
  --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --s5:#d55181; --s6:#008300;
}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);
  font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding-block:24px;padding-inline:16px}
.wrap{max-width:1180px;margin:0 auto}
header{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap;
  border-bottom:2px solid var(--ink);padding-bottom:12px;margin-bottom:20px}
h1{font-size:20px;margin:0;font-weight:700;letter-spacing:-.01em}
.sub{font-size:12px;color:var(--muted);margin-top:4px}
button.tema{background:none;border:1px solid var(--border);color:var(--ink2);
  border-radius:6px;padding:6px 12px;font-size:12px;cursor:pointer;font-family:inherit}
button.tema:hover{border-color:var(--axis)}

.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:10px;margin-bottom:22px}
.tile{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:13px 15px}
.tile .lab{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px;
  margin-bottom:5px;line-height:1.3;min-height:2.6em}
.tile .val{font-size:22px;font-weight:700;line-height:1.15;letter-spacing:-.02em;
  white-space:nowrap}
.tile .note{font-size:11px;color:var(--ink2);margin-top:3px}

.panel{background:var(--surface);border:1px solid var(--border);border-radius:8px;
  padding:16px 18px;margin-bottom:18px}
.panel h2{font-size:13px;margin:0 0 2px;font-weight:700}
.panel .desc{font-size:11.5px;color:var(--muted);margin-bottom:14px}

.filtros{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:16px}
.filtros select,.filtros input{background:var(--surface);color:var(--ink);
  border:1px solid var(--border);border-radius:6px;padding:7px 10px;font-size:12.5px;font-family:inherit}
.filtros input{min-width:200px;flex:1}
.campo{display:flex;flex-direction:column;gap:4px;font-size:11px;
  color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.campo input{min-width:120px;flex:none}
.calcgrid{display:flex;flex-wrap:wrap;gap:22px;margin:4px 0 16px}
.calcdato{min-width:120px}
.calcdato b{display:block;font-size:19px;font-weight:600;color:var(--ink);
  font-variant-numeric:tabular-nums}
.calcdato span{font-size:11px;color:var(--muted);text-transform:uppercase;
  letter-spacing:.04em}
.nota{font-size:12px;color:var(--ink2);margin-top:10px;line-height:1.5}
.filtros select:focus,.filtros input:focus{outline:2px solid var(--s1);outline-offset:-1px}

.leyenda{display:flex;gap:14px;flex-wrap:wrap;font-size:11.5px;color:var(--ink2);margin-bottom:10px}
.leyenda span{display:inline-flex;align-items:center;gap:5px}
.sw{width:9px;height:9px;border-radius:2px;flex:none}

.chartbox{position:relative;overflow-x:auto}
svg{display:block;max-width:100%;height:auto}
.tt{position:fixed;pointer-events:none;background:var(--surface);color:var(--ink);
  border:1px solid var(--axis);border-radius:6px;padding:8px 10px;font-size:11.5px;
  box-shadow:0 4px 14px rgba(0,0,0,.16);max-width:290px;z-index:50;line-height:1.45}
.tt b{display:block;margin-bottom:3px;font-size:12px}
.tt .k{color:var(--muted)}

.tablabox{overflow-x:auto;border:1px solid var(--border);border-radius:8px;background:var(--surface)}
table{border-collapse:collapse;width:100%;font-size:12.5px}
/* El ancho minimo es SOLO de la tabla de detalle, que tiene diez columnas.
   Cuando estaba en el selector generico, la tablita de sensibilidad de la
   calculadora heredaba 1040px y empujaba la pagina entera a lo ancho en el
   celular. */
#tabla{min-width:1040px;table-layout:fixed}
th{text-align:left;font-weight:600;color:var(--ink2);background:var(--page);
  padding:9px 10px;border-bottom:1px solid var(--axis);position:sticky;top:0;
  white-space:nowrap;cursor:pointer;user-select:none}
th:hover{color:var(--ink)}
th .ord{color:var(--muted);font-size:10px}
td{padding:8px 10px;border-bottom:1px solid var(--grid);vertical-align:top}
tr:last-child td{border-bottom:none}
tbody tr:hover{background:color-mix(in srgb,var(--s1) 6%,transparent)}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.cat{display:inline-flex;align-items:center;gap:5px;font-size:11.5px;line-height:1.3}
.tit{font-weight:600;line-height:1.35}
.em{color:var(--ink2);font-size:11.5px;font-weight:400;margin-top:2px}
td.nw{white-space:nowrap}
.badge{display:inline-block;padding:1px 6px;border-radius:4px;font-size:10.5px;
  font-weight:600;white-space:nowrap;border:1px solid transparent}
.b-act{color:var(--good);border-color:var(--good)}
.b-fin{color:var(--ink2);border-color:var(--axis)}
.b-can{color:var(--critical);border-color:var(--critical)}
.b-anu{color:var(--s1);border-color:var(--s1)}
a.doc{color:var(--s1);text-decoration:none;font-size:11px;font-weight:600}
a.doc:hover{text-decoration:underline}
.b-des{color:var(--critical);font-weight:700}
.vacio{padding:36px;text-align:center;color:var(--muted);font-size:13px}
footer{margin-top:22px;padding-top:12px;border-top:1px solid var(--border);
  font-size:11px;color:var(--muted)}
@media (max-width:720px){
  .tile .val{font-size:19px}
  header{align-items:flex-start}
  /* Comprimir el grafico a 400px lo vuelve ilegible: mejor que se deslice. */
  .chartbox svg{min-width:600px}
  .filtros input{min-width:100%}
}

/* --- Telefono -------------------------------------------------------------
   Abajo de 640px la tabla de diez columnas deja de ser una tabla: cada fila
   pasa a ser una ficha con la etiqueta al lado del dato. Una tabla de 1040px
   en una pantalla de 390px se lee con lupa y deslizando en dos ejes, que es
   la peor forma de mirar datos. */
.hint-desliza{display:none}
#fordenar{display:none}

@media (max-width:640px){
  body{padding-inline:12px}
  .panel{padding:14px;border-radius:10px}
  .tiles{grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px}
  .tile{padding:11px 12px}
  .tile .lab{min-height:0;margin-bottom:3px}

  /* 16px es el umbral abajo del cual iOS hace zoom solo al tocar un campo, y
     despues deja la pagina corrida. No es un capricho de tamano. */
  .filtros select,.filtros input,.campo input,.campo select{
    font-size:16px;min-height:44px;padding:8px 12px}
  .filtros{gap:6px}
  .filtros select{flex:1 1 calc(50% - 6px);min-width:0}
  .campo input,.campo select{width:100%}
  .campo{flex:1 1 calc(50% - 8px);min-width:0}

  .hint-desliza{display:block;font-size:11px;color:var(--muted);margin-bottom:6px}
  #fordenar{display:block;width:100%}

  .tablabox{overflow-x:visible;border:none;border-radius:0;background:none}
  #tabla{min-width:0;table-layout:auto;display:block;font-size:13px}
  #tabla colgroup,#tabla thead{display:none}
  #tabla tbody,#tabla tr,#tabla td{display:block;width:auto}
  #tabla tr{background:var(--surface);border:1px solid var(--border);
    border-radius:10px;padding:12px 14px;margin-bottom:8px;
    display:flex;flex-direction:column}
  /* El titulo encabeza la ficha aunque en la tabla sea la tercera columna. */
  #tabla td.tit{order:-1}
  #tabla tbody tr:hover{background:var(--surface)}
  #tabla td{border:none;padding:3px 0;display:flex;justify-content:space-between;
    align-items:baseline;gap:14px;text-align:right}
  #tabla td::before{content:attr(data-l);color:var(--muted);font-size:11px;
    text-transform:uppercase;letter-spacing:.04em;text-align:left;flex:none}
  /* El titulo y el emisor son el encabezado de la ficha, no un dato mas. */
  #tabla td.tit{display:block;text-align:left;font-size:14px;
    padding:0 0 8px;margin-bottom:6px;border-bottom:1px solid var(--grid)}
  #tabla td.tit::before{display:none}
  /* Un "-" repetido ocho veces es ruido: en la ficha directamente no va. */
  #tabla td.vac{display:none}
  .vacio{padding:28px 12px}
}
</style>
</head>
<body>
<div class="wrap">
<header>
  <div>
    <h1>Monitor de licitaciones</h1>
    <div class="sub">Mercado primario argentino &middot; actualizado __GENERADO__ h &middot; fuente A3 Mercados</div>
  </div>
  <button class="tema" id="btnTema" type="button">Tema</button>
</header>

<div class="tiles" id="tiles"></div>

<div class="panel">
  <h2>Tasas y m&aacute;rgenes de corte de obligaciones negociables</h2>
  <div class="desc">Cada punto es una ON adjudicada. Solo se grafican las que informaron un corte con n&uacute;mero.</div>
  <div class="filtros" style="margin-bottom:10px">
    <select id="fmonG">
      <option value="ars">Cortes en pesos</option>
      <option value="ext">Cortes en moneda extranjera</option>
      <option value="">Todas las monedas juntas</option>
    </select>
  </div>
  <div class="leyenda" id="legScatter"></div>
  <div class="hint-desliza">Desliz&aacute; el gr&aacute;fico para verlo completo &rarr;</div>
  <div class="chartbox" id="scatter"></div>
</div>

<div class="panel">
  <h2>Curva: tasa de corte contra plazo</h2>
  <div class="desc">Cada punto es una ON adjudicada, ubicada seg&uacute;n cu&aacute;nto falta para su vencimiento. As&iacute; se ve la curva de cr&eacute;dito y qu&eacute; emisi&oacute;n qued&oacute; fuera de l&iacute;nea: dos ON al mismo plazo deber&iacute;an pagar parecido.</div>
  <div class="filtros" style="margin-bottom:10px">
    <select id="fmonC">
      <option value="ars">Cortes en pesos</option>
      <option value="ext">Cortes en moneda extranjera</option>
    </select>
  </div>
  <div class="leyenda" id="legCurva"></div>
  <div class="hint-desliza">Desliz&aacute; el gr&aacute;fico para verlo completo &rarr;</div>
  <div class="chartbox" id="curva"></div>
</div>

<div class="panel">
  <h2>Calculadora de bonos</h2>
  <div class="desc">Qu&eacute; pasa con el precio si se mueve la tasa. Cargando la tasa de corte y el plazo de cualquier licitaci&oacute;n de la tabla de abajo se ve su duration y cu&aacute;nto perder&iacute;a o ganar&iacute;a ante un movimiento de tasa. Sirve para tasa fija: con Badlar o CER el flujo futuro depende de una tasa que nadie conoce.</div>
  <div class="filtros" style="margin-bottom:10px">
    <label class="campo">Tasa de cup&oacute;n (TNA %)<input type="number" id="cTasa" value="9.5" step="0.25" min="0"></label>
    <label class="campo">Plazo (a&ntilde;os)<input type="number" id="cAnios" value="5" step="0.5" min="0.25"></label>
    <label class="campo">Precio (por 100 VN)<input type="number" id="cPrecio" value="100" step="0.5" min="1"></label>
    <label class="campo">Cup&oacute;n<select id="cFrec">
      <option value="2" selected>semestral</option>
      <option value="1">anual</option>
      <option value="4">trimestral</option>
      <option value="12">mensual</option>
    </select></label>
    <label class="campo">Amortiza<select id="cAmort">
      <option value="-1" selected>bullet (todo al final)</option>
      <option value="0">cuotas iguales, desde el inicio</option>
      <option value="1">cuotas iguales, gracia 1 a&ntilde;o</option>
      <option value="2">cuotas iguales, gracia 2 a&ntilde;os</option>
      <option value="3">cuotas iguales, gracia 3 a&ntilde;os</option>
    </select></label>
  </div>
  <div id="calcSalida"></div>
</div>

<div class="panel">
  <h2>Licitaciones por semana</h2>
  <div class="desc">Cantidad de colocaciones efectivamente licitadas, no monto: los importes vienen en pesos, d&oacute;lares, UVA y d&oacute;lar-linked, y sumarlos no significar&iacute;a nada. No incluye los avisos de la CNV, para no contar dos veces la misma emisi&oacute;n.</div>
  <div class="leyenda" id="legBarras"></div>
  <div class="hint-desliza">Desliz&aacute; el gr&aacute;fico para verlo completo &rarr;</div>
  <div class="chartbox" id="barras"></div>
</div>

<div class="panel">
  <h2>Detalle</h2>
  <div class="desc">Clic en el encabezado para ordenar.</div>
  <div class="filtros">
    <input type="search" id="q" placeholder="Buscar emisor, t&iacute;tulo, colocador...">
    <select id="fcat"></select>
    <select id="fest"></select>
    <select id="fmon"></select>
    <select id="ffue"></select>
    <select id="fordenar" aria-label="Ordenar por"></select>
  </div>
  <div class="tablabox"><table id="tabla"><thead></thead><tbody></tbody></table>
    <div class="vacio" id="vacio" hidden>No hay licitaciones que cumplan el filtro.</div>
  </div>
</div>

<footer>
  Monitor de licitaciones v__VERSION__ &middot;
  Datos informativos tomados de la grilla de licitaciones de A3 Mercados (ex MAE).
  El mercado puede corregirlos despu&eacute;s de publicados: verificar contra el aviso de
  suscripci&oacute;n antes de operar.
</footer>
</div>

<script>
const DATOS = __DATOS__;

/* Paleta: slots categoricos en orden fijo, nunca ciclados. Las tres
   categorias de ON toman los tres primeros slots, que son los unicos
   validados para comparacion de todos contra todos (el scatter). */
const CATS = ["ON Corporativa","ON PyME CNV","ON Sostenible (VS/SVS)",
              "Fideicomiso Financiero","Deuda Publica","Otro"];
const VAR = ["--s1","--s2","--s3","--s4","--s5","--s6"];
const CATS_ON = CATS.slice(0,3);
const color = c => `var(${VAR[Math.max(0,CATS.indexOf(c))]})`;

/* Las categorias se guardan sin acentos (son claves de config.yaml y de la
   planilla); aca se muestran bien escritas. */
const NOMBRE = {"Deuda Publica":"Deuda pública",
                "ON PyME CNV":"ON PyME CNV",
                "ON Sostenible (VS/SVS)":"ON sostenible"};
const nombreCat = c => NOMBRE[c] || c;
const MON_CORTA = {"ARS PESOS":"ARS","USD LINK":"USD link"};
const monCorta = m => MON_CORTA[m] || m;

const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, m =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[m]));

const nf = (v,d=0) => v==null||v===""||isNaN(v) ? "-"
  : Number(v).toLocaleString("es-AR",{minimumFractionDigits:d,maximumFractionDigits:d});

const SIM = {"ARS PESOS":"$","ARS":"$","USD":"US$","USD LINK":"US$ link","UVA":"UVA"};
function montoCorto(v, mon){
  if(v==null||!v) return "-";
  const s = SIM[mon] ? SIM[mon]+" " : "";
  const a = Math.abs(v);
  if(a>=1e9) return s+nf(v/1e9,1)+" MM";
  if(a>=1e6) return s+nf(v/1e6,1)+" M";
  if(a>=1e3) return s+nf(v/1e3,0)+" k";
  return s+nf(v,0);
}
const fechaCorta = f => f ? f.slice(8,10)+"/"+f.slice(5,7) : "-";
const fechaAr = f => f ? f.slice(8,10)+"/"+f.slice(5,7)+"/"+f.slice(0,4) : "-";

/* ---------------- tooltip ---------------- */
const tt = document.createElement("div");
tt.className = "tt"; tt.hidden = true; document.body.appendChild(tt);
function mostrarTT(html, ev){
  tt.innerHTML = html; tt.hidden = false;
  const r = tt.getBoundingClientRect();
  let x = ev.clientX + 14, y = ev.clientY + 14;
  if(x + r.width > innerWidth - 8) x = ev.clientX - r.width - 14;
  if(y + r.height > innerHeight - 8) y = ev.clientY - r.height - 14;
  tt.style.left = Math.max(8,x)+"px"; tt.style.top = Math.max(8,y)+"px";
}
const ocultarTT = () => { tt.hidden = true; };

/* ---------------- tiles ---------------- */
function tiles(){
  // Los avisos de la CNV no son licitaciones: no entran en los conteos ni en
  // los volumenes, o se contaria dos veces la misma emision.
  const licitadas = DATOS.filter(d => d.fuente !== "cnv");
  const on = licitadas.filter(d => CATS_ON.includes(d.categoria));
  const activas = licitadas.filter(d => d.estado === "Activa");
  const anunciadas = DATOS.filter(d => d.estado === "Anunciada").length;
  const conTasa = on.filter(d => d.tasa != null);
  const ars = on.filter(d => d.moneda === "ARS PESOS" && d.licitado)
                .reduce((a,d) => a + d.licitado, 0);
  const usd = on.filter(d => String(d.moneda).startsWith("USD") && d.licitado)
                .reduce((a,d) => a + d.licitado, 0);
  const mediana = arr => {
    if(!arr.length) return null;
    const s = [...arr].sort((a,b)=>a-b), m = s.length>>1;
    return s.length%2 ? s[m] : (s[m-1]+s[m])/2;
  };
  const medPesos = mediana(conTasa.filter(d=>d.moneda==="ARS PESOS").map(d=>d.tasa));
  const medUsd = mediana(conTasa.filter(d=>String(d.moneda).startsWith("USD")).map(d=>d.tasa));
  const desiertas = on.filter(d => d.desierta).length;

  // Seis tiles fijos: un septimo cae solo a una segunda fila y queda feo.
  // El dato de desiertas viaja como nota del primero.
  const t = [
    ["ON registradas", nf(on.length),
     `${licitadas.length} colocaciones` + (desiertas ? ` &middot; ${desiertas} desierta${desiertas>1?"s":""}` : " en total")],
    ["Abiertas ahora", nf(activas.length),
     anunciadas ? `${anunciadas} anunciada${anunciadas>1?"s":""} en la CNV`
                : (activas.length ? "en rueda" : "ninguna en rueda")],
    ["Ofrecido en $", montoCorto(ars,"ARS PESOS"), "acumulado en ON"],
    ["Ofrecido en US$", montoCorto(usd,"USD"), "acumulado en ON"],
    ["Corte mediano $", medPesos!=null ? nf(medPesos,2)+"%" : "-", "ON en pesos"],
    ["Corte mediano US$", medUsd!=null ? nf(medUsd,2)+"%" : "-", "ON en d&oacute;lares"],
  ];

  $("#tiles").innerHTML = t.map(([l,v,n]) =>
    `<div class="tile"><div class="lab">${l}</div><div class="val">${v}</div>
     <div class="note">${n}</div></div>`).join("");
}

/* ---------------- scatter de tasas ---------------- */
function scatter(){
  /* Una tasa en pesos (30-45%) y una en dolares (5-10%) son la misma unidad
     pero regimenes distintos: en un mismo eje quedan dos nubes separadas y no
     se lee ninguna. Por eso el grafico arranca filtrado por moneda. */
  const mon = $("#fmonG").value;
  const pts = DATOS.filter(d =>
    CATS_ON.includes(d.categoria) && d.tasa != null && d.inicio &&
    (!mon || (mon === "ars" ? String(d.moneda).startsWith("ARS")
                            : !String(d.moneda).startsWith("ARS"))));
  const cont = $("#scatter");

  const usadas = CATS_ON.filter(c => pts.some(p => p.categoria === c));
  $("#legScatter").innerHTML = usadas.map(c =>
    `<span><i class="sw" style="background:${color(c)}"></i>${esc(nombreCat(c))}</span>`).join("")
    + (mon === "ars" ? ""
       : `<span style="color:var(--muted)">Marcador hueco: en moneda extranjera</span>`);

  if(pts.length < 2){
    cont.innerHTML = `<div class="vacio">Todav&iacute;a no hay suficientes ON con corte publicado
      en esta moneda para dibujar la serie. Se llena solo a medida que el bot corre.</div>`;
    return;
  }

  const W = 1100, H = 320, m = {t:14,r:18,b:34,l:52};
  const iw = W-m.l-m.r, ih = H-m.t-m.b;

  const xs = pts.map(p => Date.parse(p.inicio));
  let x0 = Math.min(...xs), x1 = Math.max(...xs);
  if(x0 === x1){ x0 -= 864e5; x1 += 864e5; }
  const ys = pts.map(p => p.tasa);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = (y1-y0)*0.12 || 1;
  y0 -= pad; y1 += pad;

  const X = v => m.l + (v-x0)/(x1-x0)*iw;
  const Y = v => m.t + ih - (v-y0)/(y1-y0)*ih;

  let g = "";
  const nY = 5;
  for(let i=0;i<=nY;i++){
    const v = y0 + (y1-y0)*i/nY, y = Y(v);
    g += `<line x1="${m.l}" y1="${y.toFixed(1)}" x2="${W-m.r}" y2="${y.toFixed(1)}"
           stroke="var(--grid)" stroke-width="1"/>
          <text x="${m.l-8}" y="${(y+4).toFixed(1)}" text-anchor="end" font-size="11"
           fill="var(--muted)" style="font-variant-numeric:tabular-nums">${nf(v,1)}%</text>`;
  }
  const nX = Math.min(6, pts.length);
  for(let i=0;i<=nX;i++){
    const v = x0 + (x1-x0)*i/nX, x = X(v);
    const d = new Date(v);
    g += `<text x="${x.toFixed(1)}" y="${H-12}" text-anchor="middle" font-size="11"
           fill="var(--muted)">${String(d.getDate()).padStart(2,"0")}/${String(d.getMonth()+1).padStart(2,"0")}</text>`;
  }
  g += `<line x1="${m.l}" y1="${m.t+ih}" x2="${W-m.r}" y2="${m.t+ih}"
         stroke="var(--axis)" stroke-width="1"/>`;

  // Los puntos van con un aro del color de la superficie para que no se
  // fundan entre si cuando dos ON cortan casi igual el mismo dia.
  const marks = pts.map((p,i) => {
    const ext = !String(p.moneda).startsWith("ARS");
    return `<circle cx="${X(Date.parse(p.inicio)).toFixed(1)}" cy="${Y(p.tasa).toFixed(1)}" r="5.5"
      fill="${ext ? "var(--surface)" : color(p.categoria)}"
      stroke="${color(p.categoria)}" stroke-width="${ext ? 2 : 1.5}"
      data-i="${i}" style="cursor:pointer"/>`;
  }).join("");

  cont.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img"
    aria-label="Tasas y margenes de corte de obligaciones negociables en el tiempo">
    ${g}<g id="pts">${marks}</g></svg>`;

  cont.querySelectorAll("circle[data-i]").forEach(el => {
    const p = pts[+el.dataset.i];
    const ver = ev => mostrarTT(
      `<b>${esc(p.titulo)}</b>
       <span class="k">Corte</span> <b style="display:inline">${nf(p.tasa,2)}%</b><br>
       <span class="k">Fecha</span> ${fechaAr(p.inicio)}<br>
       <span class="k">Moneda</span> ${esc(p.moneda)}<br>
       <span class="k">Adjudicado</span> ${montoCorto(p.adjudicado,p.moneda)}<br>
       <span class="k">${esc(p.corte)}</span>`, ev);
    el.addEventListener("mouseenter", ver);
    el.addEventListener("mousemove", ver);
    el.addEventListener("mouseleave", ocultarTT);
  });
}

/* ---------------- curva: tasa contra plazo ---------------- */
function curva(){
  const mon = $("#fmonC").value;
  const pts = DATOS.filter(d =>
    CATS_ON.includes(d.categoria) && d.tasa != null && d.plazo != null &&
    d.plazo > 0 &&
    (mon === "ars" ? String(d.moneda).startsWith("ARS")
                   : !String(d.moneda).startsWith("ARS")));
  const cont = $("#curva");

  const usadas = CATS_ON.filter(c => pts.some(p => p.categoria === c));
  $("#legCurva").innerHTML = usadas.map(c =>
    `<span><i class="sw" style="background:${color(c)}"></i>${esc(nombreCat(c))}</span>`).join("")
    + `<span style="color:var(--muted)">Tama&ntilde;o del punto: monto adjudicado</span>`;

  if(pts.length < 3){
    cont.innerHTML = `<div class="vacio">Hacen falta al menos 3 ON con corte y
      vencimiento conocidos en esta moneda. Se llena solo con cada corrida.</div>`;
    return;
  }

  const W = 1100, H = 320, m = {t:14,r:18,b:40,l:52};
  const iw = W-m.l-m.r, ih = H-m.t-m.b;

  const xs = pts.map(p => p.plazo);
  let x0 = 0, x1 = Math.max(...xs) * 1.05;
  const ys = pts.map(p => p.tasa);
  let y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = (y1-y0)*0.12 || 1;
  y0 -= pad; y1 += pad;

  const X = v => m.l + (v-x0)/(x1-x0)*iw;
  const Y = v => m.t + ih - (v-y0)/(y1-y0)*ih;

  // El radio codifica el monto: una emision grande fuera de curva importa
  // mas que una chica. Escala por raiz para que el area sea proporcional.
  const montos = pts.map(p => p.adjudicado || p.licitado || 0).filter(Boolean);
  const maxM = montos.length ? Math.max(...montos) : 1;
  const radio = p => {
    const v = p.adjudicado || p.licitado || 0;
    return v ? 4 + Math.sqrt(v / maxM) * 7 : 4;
  };

  let g = "";
  for(let i=0;i<=5;i++){
    const v = y0 + (y1-y0)*i/5, y = Y(v);
    g += `<line x1="${m.l}" y1="${y.toFixed(1)}" x2="${W-m.r}" y2="${y.toFixed(1)}"
           stroke="var(--grid)" stroke-width="1"/>
          <text x="${m.l-8}" y="${(y+4).toFixed(1)}" text-anchor="end" font-size="11"
           fill="var(--muted)" style="font-variant-numeric:tabular-nums">${nf(v,1)}%</text>`;
  }
  const pasosX = Math.min(6, Math.ceil(x1));
  for(let i=0;i<=pasosX;i++){
    const v = x1*i/pasosX, x = X(v);
    g += `<text x="${x.toFixed(1)}" y="${H-18}" text-anchor="middle" font-size="11"
           fill="var(--muted)">${nf(v,1)}</text>`;
  }
  g += `<line x1="${m.l}" y1="${m.t+ih}" x2="${W-m.r}" y2="${m.t+ih}"
         stroke="var(--axis)" stroke-width="1"/>
        <text x="${(m.l+iw/2).toFixed(0)}" y="${H-3}" text-anchor="middle"
         font-size="11" fill="var(--muted)">a&ntilde;os hasta el vencimiento</text>`;

  const marks = pts.map((p,i) =>
    `<circle cx="${X(p.plazo).toFixed(1)}" cy="${Y(p.tasa).toFixed(1)}"
      r="${radio(p).toFixed(1)}" fill="${color(p.categoria)}" fill-opacity="0.75"
      stroke="var(--surface)" stroke-width="1.5"
      data-i="${i}" style="cursor:pointer"/>`).join("");

  cont.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img"
    aria-label="Tasa de corte contra plazo hasta el vencimiento">
    ${g}<g>${marks}</g></svg>`;

  cont.querySelectorAll("circle[data-i]").forEach(el => {
    const p = pts[+el.dataset.i];
    const ver = ev => mostrarTT(
      `<b>${esc(p.titulo)}</b>
       <span class="k">Corte</span> <b style="display:inline">${nf(p.tasa,2)}%</b>
       &nbsp;<span class="k">Plazo</span> ${nf(p.plazo,1)} a&ntilde;os<br>
       <span class="k">Vence</span> ${fechaAr(p.vencimiento)}<br>
       <span class="k">Adjudicado</span> ${montoCorto(p.adjudicado, p.moneda)}<br>
       <span class="k">${esc(p.emisor)}</span>`, ev);
    el.addEventListener("mouseenter", ver);
    el.addEventListener("mousemove", ver);
    el.addEventListener("mouseleave", ocultarTT);
  });
}

/* ---------------- barras por semana ---------------- */
function lunesDe(iso){
  const d = new Date(iso+"T00:00:00");
  const dif = (d.getDay()+6)%7;          // lunes = 0
  d.setDate(d.getDate()-dif);
  return d.toISOString().slice(0,10);
}

function barras(){
  const cont = $("#barras");
  /* Solo las licitaciones de verdad: un aviso de la CNV y su licitacion en A3
     son la misma emision, y contar las dos infla la semana al doble. */
  const conFecha = DATOS.filter(d => d.inicio && d.fuente !== "cnv");
  if(!conFecha.length){ cont.innerHTML = `<div class="vacio">Sin datos.</div>`; return; }

  const porSem = {};
  conFecha.forEach(d => {
    const s = lunesDe(d.inicio);
    (porSem[s] = porSem[s] || {})[d.categoria] = (porSem[s][d.categoria]||0)+1;
  });
  const semanas = Object.keys(porSem).sort().slice(-14);
  const usadas = CATS.filter(c => semanas.some(s => porSem[s][c]));

  $("#legBarras").innerHTML = usadas.map(c =>
    `<span><i class="sw" style="background:${color(c)}"></i>${esc(nombreCat(c))}</span>`).join("");

  const W = 1100, H = 280, m = {t:14,r:18,b:38,l:42};
  const iw = W-m.l-m.r, ih = H-m.t-m.b;
  const maxT = Math.max(...semanas.map(s =>
    Object.values(porSem[s]).reduce((a,b)=>a+b,0)));
  const paso = iw/semanas.length;
  const ancho = Math.min(46, paso*0.66);
  const Y = v => m.t + ih - (v/maxT)*ih;

  let g = "";
  const nY = Math.min(5, maxT);
  for(let i=0;i<=nY;i++){
    const v = Math.round(maxT*i/nY), y = Y(v);
    g += `<line x1="${m.l}" y1="${y.toFixed(1)}" x2="${W-m.r}" y2="${y.toFixed(1)}"
           stroke="var(--grid)" stroke-width="1"/>
          <text x="${m.l-8}" y="${(y+4).toFixed(1)}" text-anchor="end" font-size="11"
           fill="var(--muted)">${v}</text>`;
  }

  semanas.forEach((s,i) => {
    const cx = m.l + paso*i + paso/2;
    let acum = 0;
    usadas.forEach(c => {
      const n = porSem[s][c] || 0;
      if(!n) return;
      const yTop = Y(acum+n), yBot = Y(acum);
      // 2px de separacion entre segmentos apilados, del color de la superficie.
      const alto = Math.max(1, yBot-yTop-2);
      g += `<rect x="${(cx-ancho/2).toFixed(1)}" y="${yTop.toFixed(1)}"
             width="${ancho.toFixed(1)}" height="${alto.toFixed(1)}" rx="2"
             fill="${color(c)}" data-s="${s}" data-c="${esc(c)}" data-n="${n}"
             style="cursor:pointer"/>`;
      acum += n;
    });
    g += `<text x="${cx.toFixed(1)}" y="${H-20}" text-anchor="middle" font-size="10.5"
           fill="var(--muted)">${fechaCorta(s)}</text>`;
  });
  g += `<line x1="${m.l}" y1="${m.t+ih}" x2="${W-m.r}" y2="${m.t+ih}"
         stroke="var(--axis)" stroke-width="1"/>
        <text x="${m.l}" y="${H-6}" font-size="10" fill="var(--muted)">semana del</text>`;

  cont.innerHTML = `<svg viewBox="0 0 ${W} ${H}" role="img"
    aria-label="Cantidad de licitaciones por semana y categoria">${g}</svg>`;

  cont.querySelectorAll("rect[data-s]").forEach(el => {
    const {s,c,n} = el.dataset;
    const total = Object.values(porSem[s]).reduce((a,b)=>a+b,0);
    const ver = ev => mostrarTT(
      `<b>Semana del ${fechaAr(s)}</b>
       <span class="k">${esc(c)}</span> <b style="display:inline">${n}</b><br>
       <span class="k">Total de la semana</span> ${total}`, ev);
    el.addEventListener("mouseenter", ver);
    el.addEventListener("mousemove", ver);
    el.addEventListener("mouseleave", ocultarTT);
  });
}

/* ---------------- tabla ---------------- */
/* [clave, encabezado, tipo, ancho] -- los anchos son fijos (table-layout:fixed)
   para que el titulo tenga lugar y las columnas cortas no se partan. */
/* col, encabezado, tipo, ancho, etiqueta larga para la ficha del celular
   (cuando falta, se usa el encabezado). */
const COLS = [
  ["inicio","Fecha","f","86px"], ["categoria","Categor&iacute;a","c","136px"],
  ["titulo","T&iacute;tulo","t","auto"], ["estado","Estado","e","104px"],
  ["moneda","Mon.","s","76px","Moneda"], ["licitado","A licitar","m","104px"],
  ["adjudicado","Adjudicado","m","110px"],
  ["ratio","Adj./Lic.","r","82px","Adjudicado / licitado"],
  ["tasa","Corte","p","84px","Tasa de corte"],
  ["vencimiento","Vence","f","92px","Vencimiento"],
];
let orden = {col:"inicio", asc:false};

function opciones(sel, valores, etiqueta){
  sel.innerHTML = `<option value="">${etiqueta}</option>` +
    valores.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join("");
}

function filtradas(){
  const q = $("#q").value.trim().toLowerCase();
  const c = $("#fcat").value, e = $("#fest").value;
  const mo = $("#fmon").value, fu = $("#ffue").value;
  return DATOS.filter(d =>
    (!c || d.categoria === c) && (!e || d.estado === e) &&
    (!mo || d.moneda === mo) && (!fu || d.fuente === fu) &&
    (!q || `${d.titulo} ${d.emisor} ${d.colocador} ${d.tipo}`.toLowerCase().includes(q))
  );
}

/* data-l es la etiqueta que en el celular reemplaza al encabezado de columna:
   la ficha muestra "Corte  38,20%" en vez de un numero suelto. La clase "vac"
   marca el dato que no existe, para poder esconderlo en la ficha. */
function celda(d, col, tipo, etiqueta){
  const v = d[col];
  const td = (clases, html, vacio) =>
    `<td data-l="${etiqueta}" class="${clases}${vacio ? " vac" : ""}">${html}</td>`;

  switch(tipo){
    case "f": return td("num", fechaAr(v), !v);
    case "c": return td("", `<span class="cat"><i class="sw" style="background:${color(v)}"></i>${esc(nombreCat(v))}</span>`, !v);
    case "t": {
      const doc = d.enlace
        ? `<a class="doc" href="${esc(d.enlace)}" target="_blank" rel="noopener">ver publicación ↗</a>`
        : "";
      return td("tit", `${esc(d.titulo)}<div class="em">${esc(d.emisor)}</div>${doc}`);
    }
    case "e": {
      const k = v==="Activa" ? "b-act" : v==="Finalizada" ? "b-fin"
              : v==="Anunciada" ? "b-anu" : "b-can";
      const txt = v==="Cancelada/Suspendida" ? "Cancelada" : v;
      return td("nw", `<span class="badge ${k}">${esc(txt)}</span>`, !v);
    }
    case "s": return td("nw", esc(monCorta(v)), !v);
    case "m": return td("num", montoCorto(v, d.moneda), v==null);
    case "r": return td("num", v==null?"-":nf(v*100,0)+"%", v==null);
    case "p": return td("num", d.desierta ? '<span class="b-des">desierta</span>'
                        : v==null ? "-" : nf(v,2)+"%", v==null && !d.desierta);
    default:  return td("", esc(v), !v);
  }
}

function pintar(){
  const filas = filtradas().sort((a,b) => {
    const va = a[orden.col], vb = b[orden.col];
    if(va == null && vb == null) return 0;
    if(va == null) return 1;          // los vacios siempre al final
    if(vb == null) return -1;
    const r = typeof va === "number" && typeof vb === "number"
      ? va - vb : String(va).localeCompare(String(vb), "es");
    return orden.asc ? r : -r;
  });

  const tabla = $("#tabla");
  let cg = tabla.querySelector("colgroup");
  if(!cg){
    cg = document.createElement("colgroup");
    cg.innerHTML = COLS.map(([,,,w]) => `<col style="width:${w}">`).join("");
    tabla.prepend(cg);
  }

  tabla.tHead.innerHTML = "<tr>" + COLS.map(([c,t,tp]) =>
    `<th data-c="${c}" class="${"mrp".includes(tp)||tp==="f"?"num":""}">${t}` +
    (orden.col===c ? `<span class="ord"> ${orden.asc?"&#9650;":"&#9660;"}</span>` : "") +
    `</th>`).join("") + "</tr>";

  tabla.tBodies[0].innerHTML = filas.map(d =>
    "<tr>" + COLS.map(([c,t,tp,,largo]) => celda(d,c,tp,largo||t)).join("") + "</tr>"
  ).join("");

  $("#vacio").hidden = filas.length > 0;
  tabla.hidden = filas.length === 0;

  // Que el selector del celular refleje un orden elegido por clic, y al reves.
  const sel = $("#fordenar");
  if(sel) sel.value = `${orden.col}|${orden.asc ? "asc" : "desc"}`;

  tabla.tHead.querySelectorAll("th").forEach(th =>
    th.addEventListener("click", () => {
      const c = th.dataset.c;
      orden = {col:c, asc: orden.col===c ? !orden.asc : true};
      pintar();
    }));
}

/* ---------------- arranque ---------------- */
$("#fcat").innerHTML = `<option value="">Todas las categor&iacute;as</option>` +
  CATS.filter(c => DATOS.some(d => d.categoria === c))
      .map(c => `<option value="${esc(c)}">${esc(nombreCat(c))}</option>`).join("");
opciones($("#fest"), [...new Set(DATOS.map(d=>d.estado))].filter(Boolean).sort(), "Todos los estados");
opciones($("#fmon"), [...new Set(DATOS.map(d=>d.moneda))].filter(Boolean).sort(), "Todas las monedas");

const NOMBRE_FUENTE = {a3:"A3 Mercados", cnv:"Aviso CNV"};
const fuentesPresentes = [...new Set(DATOS.map(d=>d.fuente))].filter(Boolean).sort();
$("#ffue").innerHTML = `<option value="">Todas las fuentes</option>` +
  fuentesPresentes.map(f =>
    `<option value="${esc(f)}">${esc(NOMBRE_FUENTE[f]||f)}</option>`).join("");
$("#ffue").hidden = fuentesPresentes.length < 2;

/* En el celular no hay encabezados donde hacer clic, asi que el orden sale de
   este selector. En pantalla grande esta oculto y manda el clic en el th; los
   dos escriben sobre el mismo estado, asi que no se contradicen. */
$("#fordenar").innerHTML = COLS.flatMap(([c,t,,,largo]) => (t = largo||t, [
  `<option value="${c}|desc">${t}: mayor a menor</option>`,
  `<option value="${c}|asc">${t}: menor a mayor</option>`,
])).join("");
$("#fordenar").value = `${orden.col}|${orden.asc ? "asc" : "desc"}`;
$("#fordenar").addEventListener("input", () => {
  const [col, dir] = $("#fordenar").value.split("|");
  orden = {col, asc: dir === "asc"};
  pintar();
});

["#q","#fcat","#fest","#fmon","#ffue"].forEach(s =>
  $(s).addEventListener("input", pintar));
$("#fmonG").addEventListener("input", scatter);
$("#fmonC").addEventListener("input", curva);

$("#btnTema").addEventListener("click", () => {
  const oscuro = document.documentElement.dataset.theme === "dark" ||
    (!document.documentElement.dataset.theme &&
     matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = oscuro ? "light" : "dark";
  scatter(); curva(); barras();
});

/* ---------------- calculadora de bonos ----------------
   Misma matematica que bonos.py, portada para que el panel recalcule sin
   volver a generar el archivo. Si se toca una, hay que tocar la otra:
   autotest.py compara las dos contra los mismos casos. */
function flujosBono(tasaTNA, anios, m, graciaAnios){
  const n = Math.max(1, Math.round(anios * m));
  const tasaPeriodo = (tasaTNA / 100) / m;
  let amort;
  if (graciaAnios < 0) {                       // bullet
    amort = Array(n).fill(0); amort[n-1] = 100;
  } else {
    const primera = Math.min(n, Math.round(graciaAnios * m) + 1);
    const cuotas = n - primera + 1;
    amort = Array(n).fill(0);
    for (let i = primera - 1; i < n; i++) amort[i] = 100 / cuotas;
    amort[n-1] += 100 - amort.reduce((a,b)=>a+b, 0);
  }
  const fl = []; let saldo = 100;
  for (let i = 1; i <= n; i++){
    const renta = saldo * tasaPeriodo, a = amort[i-1];
    saldo -= a;
    fl.push({anios: i/m, total: renta + a, amort: a});
  }
  return fl;
}
const vpBono = (fl, y) => fl.reduce((s,f) => s + f.total / Math.pow(1 + y/100, f.anios), 0);

function tirBono(fl, precio){
  if (precio <= 0 || !fl.length) return null;
  if (fl.reduce((s,f)=>s+f.total,0) <= precio) return null;
  let bajo = -99, alto = 10000;
  if ((vpBono(fl,bajo)-precio) * (vpBono(fl,alto)-precio) > 0) return null;
  for (let k = 0; k < 200; k++){
    const medio = (bajo + alto) / 2;
    if (vpBono(fl, medio) - precio > 0) bajo = medio; else alto = medio;
  }
  return (bajo + alto) / 2;
}
const durBono = (fl, y, p) =>
  fl.reduce((s,f) => s + f.anios * f.total / Math.pow(1 + y/100, f.anios), 0) / p;
const convBono = (fl, y, p) =>
  fl.reduce((s,f) => s + f.anios*(f.anios+1)*f.total / Math.pow(1 + y/100, f.anios+2), 0) / p;
const vidaProm = fl => {
  const cap = fl.reduce((s,f)=>s+f.amort, 0);
  return cap > 0 ? fl.reduce((s,f)=>s+f.anios*f.amort, 0) / cap : 0;
};

function calcular(){
  const tasa = parseFloat($("#cTasa").value), anios = parseFloat($("#cAnios").value);
  const precio = parseFloat($("#cPrecio").value), m = parseInt($("#cFrec").value, 10);
  const gracia = parseInt($("#cAmort").value, 10);
  const salida = $("#calcSalida");

  if (!(tasa >= 0) || !(anios > 0) || !(precio > 0)) {
    salida.innerHTML = '<div class="nota">Complet&aacute; tasa, plazo y precio.</div>';
    return;
  }
  if (gracia >= 0 && Math.round(gracia*m) + 1 > Math.round(anios*m)) {
    salida.innerHTML = '<div class="nota">La gracia no deja ninguna cuota antes del vencimiento.</div>';
    return;
  }
  const fl = flujosBono(tasa, anios, m, gracia);
  const y = tirBono(fl, precio);
  if (y === null) {
    salida.innerHTML = '<div class="nota">Con esos datos no hay una TIR positiva.</div>';
    return;
  }
  const d = durBono(fl, y, precio), dm = d / (1 + y/100), cx = convBono(fl, y, precio);
  const tna = (Math.pow(1 + y/100, 1/m) - 1) * m * 100;

  const num = (v, d) => nf(v, d);
  const dato = (v, et) => `<div class="calcdato"><b>${v}</b><span>${et}</span></div>`;
  let html = '<div class="calcgrid">' +
    dato(num(y,2) + "%", "TIR efectiva anual") +
    dato(num(tna,2) + "%", "TNA equivalente") +
    dato(num(d,2), "Duration (a&ntilde;os)") +
    dato(num(dm,2), "Duration modificada") +
    dato(num(cx,1), "Convexidad") +
    dato(num(vidaProm(fl),2), "Vida promedio") +
    '</div>';

  html += '<table><thead><tr><th>Si la tasa</th><th class="num">TIR</th>' +
          '<th class="num">Precio</th><th class="num">Cambio</th></tr></thead><tbody>';
  for (const bp of [-300,-200,-100,-50,50,100,200,300]){
    const p2 = vpBono(fl, y + bp/100);
    html += `<tr><td>${bp > 0 ? "sube" : "baja"} ${Math.abs(bp)} bp</td>` +
            `<td class="num">${num(y + bp/100, 2)}%</td>` +
            `<td class="num">${num(p2, 2)}</td>` +
            `<td class="num">${num((p2/precio - 1)*100, 2)}%</td></tr>`;
  }
  html += '</tbody></table>';
  html += '<div class="nota">Convenci&oacute;n 30/360. La duration modificada es Macaulay / (1 + TIR), ' +
          'con TIR efectiva anual, que es como se lee ac&aacute;; una terminal que compone semestralmente ' +
          'informa un n&uacute;mero algo mayor.' +
          (gracia < 0 ? ' Bullet: si el bono amortiza antes, la duration real es menor.' : '') +
          '</div>';
  salida.innerHTML = html;
}
["#cTasa","#cAnios","#cPrecio","#cFrec","#cAmort"].forEach(s =>
  $(s).addEventListener("input", calcular));

tiles(); scatter(); curva(); barras(); pintar(); calcular();
</script>
</body>
</html>
"""
