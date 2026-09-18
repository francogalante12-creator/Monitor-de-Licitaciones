#!/usr/bin/env python3
"""
Monitor de licitaciones del mercado primario argentino.

Consulta la grilla de licitaciones de A3 Mercados (ex MAE), compara contra la
corrida anterior, avisa por mail lo que cambio y deja la planilla y el
dashboard actualizados.

Uso tipico:
    python bot.py                  # corrida normal
    python bot.py --init           # primera corrida: llena el estado, no manda mail
    python bot.py --dry-run        # muestra que mandaria, sin enviar ni guardar
    python bot.py --resumen        # manda el resumen aunque no haya novedades
    python bot.py --guardar-crudo  # ademas deja el JSON crudo de la API

Codigos de salida:
    0  ok (con o sin novedades)
    1  error de configuracion o de escritura
    2  no se pudo consultar ninguna fuente
    3  hubo novedades pero fallo el envio del mail
    4  corrida parcial: alguna fuente no respondio, las demas si

El 4 existe para que una fuente caida se note. Si la CNV deja de responder,
el bot sigue avisando lo de A3 y todo parece normal -- salvo que se dejo de
anticipar. Mejor que el programador de tareas lo marque en amarillo.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import config as cfg_mod
import contexto
import estado as est
import fuentes as fuentes_mod
import informe
import modelo
import notificar
import red
import salidas
import version
from formato import fecha_hora
from modelo import normalizar_todos

log = logging.getLogger("bot")


def configurar_logging(verboso: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verboso else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def filtrar_por_moneda(registros: list[dict], cfg: dict) -> tuple[list[dict], dict]:
    """Deja solo las licitaciones en las monedas configuradas.

    Se aplica antes que todo lo demas -- antes de detectar novedades, antes de
    la planilla y del dashboard -- asi el monitor entero habla de un solo
    mundo. Con 'monedas' vacio no filtra nada.

    El caso delicado es la CNV: sus avisos no traen campo de moneda, y el
    titulo la dice solo a veces. Descartar lo que no se puede clasificar
    apagaria justamente la fuente que avisa con dias de anticipacion, asi que
    por defecto esos pasan y el mail los marca como 'moneda a confirmar'.
    Quien prefiera el filtro estricto pone moneda_desconocida: ignorar.
    """
    permitidas = set(cfg.get("monedas") or [])
    if not permitidas:
        # Sin filtro no hay nada que aclarar. Hay que borrar la marca, no solo
        # dejar de ponerla: el estado guardado la arrastra desde cuando el
        # filtro SI estaba, y el mail seguiria diciendo "moneda a confirmar"
        # para siempre.
        for reg in registros:
            reg.pop("moneda_a_confirmar", None)
        return registros, {}

    admitir_desconocida = cfg.get("moneda_desconocida", "avisar") == "avisar"

    salida, descartados = [], {}
    for reg in registros:
        # El estado guardado por una version anterior no trae el campo. Se
        # recalcula en vez de darlo por desconocido, que dejaria pasar todo el
        # historico viejo en pesos.
        if "moneda_clase" not in reg:
            reg["moneda_clase"] = modelo.clase_moneda(
                reg.get("moneda_monto"), reg.get("moneda"),
                reg.get("titulo"), reg.get("observaciones"),
            )
        clase = reg.get("moneda_clase", "")
        if clase in permitidas:
            reg.pop("moneda_a_confirmar", None)
            salida.append(reg)
        elif not clase and admitir_desconocida:
            # Entra solo porque estamos dejando pasar lo no clasificable. El
            # mail lo aclara. Sin filtro de moneda la aclaracion no viene al
            # caso, y por eso la marca la pone el filtro y no el modelo.
            reg["moneda_a_confirmar"] = True
            salida.append(reg)
        else:
            etiqueta = clase or "sin identificar"
            descartados[etiqueta] = descartados.get(etiqueta, 0) + 1
    return salida, descartados


def filtrar_para_aviso(eventos: list[dict], cfg: dict) -> list[dict]:
    """Aplica los filtros de configuracion a los eventos.

    La planilla siempre guarda todo; el mail solo lleva lo que Franco pidio
    ver. Un emisor de la lista de destacados pasa el filtro de categoria y de
    monto siempre: si aparece Genneia, se avisa aunque sea una clase chica.
    """
    categorias = set(cfg["categorias_aviso"])
    eventos_ok = set(cfg["eventos_aviso"])
    minimo = float(cfg["monto_minimo_ars"] or 0)
    destacados = [d.strip().lower() for d in cfg["emisores_destacados"] if d.strip()]

    seleccion = []
    for ev in eventos:
        if ev["evento"] not in eventos_ok:
            continue

        lic = ev["licitacion"]
        texto = f"{lic.get('emisor', '')} {lic.get('titulo', '')}".lower()
        es_destacado = any(d in texto for d in destacados)

        if not es_destacado:
            if lic.get("categoria") not in categorias:
                continue
            # El minimo solo aplica a montos en pesos: un monto en dolares
            # comparado contra un umbral en pesos no significa nada.
            if minimo and str(lic.get("moneda_monto", "")).upper().startswith("ARS"):
                if (lic.get("monto_licitar") or 0) < minimo:
                    continue

        ev = dict(ev)
        ev["destacado"] = es_destacado
        seleccion.append(ev)

    return seleccion


def _chequear_red(cfg: dict) -> int:
    """Prueba fuente por fuente si esta maquina llega, sin tocar nada.

    Es lo primero que hay que correr en una maquina nueva: dice si el problema
    es de red antes de que uno se ponga a mirar configuracion de mail.
    """
    print("=" * 68)
    print(f"Chequeo de red - {version.linea()}")
    print("=" * 68)
    print(f"Salida: {red.proxy_visible()}\n")

    problemas = 0
    for clave in cfg["fuentes"]:
        fuente = fuentes_mod.FUENTES.get(clave)
        if fuente is None:
            print(f"[?] '{clave}' no es una fuente conocida.")
            problemas += 1
            continue

        print(f"--- {fuente.nombre} ({clave})")
        try:
            registros = fuente.traer()
        except Exception as exc:                       # noqa: BLE001
            problemas += 1
            print(f"[FALLA] {exc}\n")
            print("Chequeo de conectividad:")
            for linea in red.chequear_conectividad(fuente.url):
                print(f"  {linea}")
            print()
        else:
            print(f"[ok]    Responde. {len(registros)} registros.\n")

    if problemas:
        print(f"{problemas} de {len(cfg['fuentes'])} fuentes no responden desde "
              "esta maquina.")
        return 2

    print("Todas las fuentes responden. La maquina esta lista para correr el bot.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help="ruta a config.yaml")
    p.add_argument("--init", action="store_true",
                   help="llena el estado sin mandar mail (primera corrida)")
    p.add_argument("--dry-run", action="store_true",
                   help="no manda mail ni guarda estado; imprime el resumen")
    p.add_argument("--resumen", action="store_true",
                   help="manda el mail de novedades aunque no haya ninguna")
    p.add_argument("--informe", action="store_true",
                   help="manda el mail de informe: el panorama del mercado, "
                        "escrito para reenviar a quien no usa el bot")
    p.add_argument("--guardar-crudo", action="store_true",
                   help="guarda el JSON crudo de la API para diagnostico")
    p.add_argument("--chequear-red", action="store_true",
                   help="prueba si esta maquina llega a cada fuente, y nada mas")
    p.add_argument("--probar-mail", action="store_true",
                   help="prueba solo las credenciales del correo, sin consultar "
                        "las fuentes ni mandar nada")
    p.add_argument("--version", action="store_true",
                   help="muestra la version de esta copia y termina")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    if args.version:
        print(version.detalle())
        return 0

    configurar_logging(args.verbose)
    log.debug("%s", version.linea())
    ahora = datetime.now(timezone.utc)

    try:
        cfg = cfg_mod.cargar(args.config)
    except (cfg_mod.ConfigInvalida, OSError) as exc:
        log.error("Configuracion invalida: %s", exc)
        return 1

    red.configurar_proxy(cfg["proxy"])

    if args.chequear_red:
        return _chequear_red(cfg)

    if args.probar_mail:
        listo, motivo = cfg_mod.puede_mandar_mail(cfg)
        if not listo:
            print(f"[FALLA] {motivo}.")
            return 3
        return 0 if notificar.probar_credenciales(cfg) else 3

    # 1. Traer. Si una fuente falla pero otra anda, se sigue con lo que haya.
    try:
        crudos, errores_fuente = fuentes_mod.traer_todo(cfg["fuentes"])
    except fuentes_mod.NingunaFuenteDisponible as exc:
        log.error("%s", exc)
        return 2

    for err in errores_fuente:
        log.warning("Fuente con problemas: %s", err)

    if args.guardar_crudo:
        destino = Path(cfg["archivo_estado"]).parent / f"crudo_{ahora:%Y%m%d_%H%M%S}.json"
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(json.dumps(crudos, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("JSON crudo guardado en %s.", destino)

    actuales = normalizar_todos(crudos)
    log.info("Traje %d licitaciones de la ventana vigente.", len(actuales))

    actuales, fuera_de_moneda = filtrar_por_moneda(actuales, cfg)
    if fuera_de_moneda:
        detalle = ", ".join(f"{n} en {m}" for m, n in sorted(fuera_de_moneda.items()))
        log.info("Filtro de moneda (%s): quedan %d, descarto %d (%s).",
                 ", ".join(cfg["monedas"]), len(actuales),
                 sum(fuera_de_moneda.values()), detalle)

    # 2. Comparar.
    previas = est.cargar(cfg["archivo_estado"])

    # El historico guardado puede venir de una configuracion con otras monedas.
    # Si no se limpia, esas licitaciones se quedan para siempre en la planilla
    # y en el dashboard, porque el estado es la unica memoria larga que hay
    # (la API solo devuelve 30 dias). Al cambiar 'monedas' se depura solo.
    conservadas, _ = filtrar_por_moneda(list(previas.values()), cfg)
    if len(conservadas) != len(previas):
        log.info("Saco del historico %d licitaciones que no estan en las "
                 "monedas configuradas.", len(previas) - len(conservadas))
        previas = {r["clave"]: r for r in conservadas}

    primera_corrida = not previas

    if primera_corrida and not args.init:
        log.warning(
            "No hay estado previo. Trato esta corrida como inicial para no mandar "
            "un mail con las %d licitaciones de la ventana. Volve a correr para "
            "empezar a recibir novedades.", len(actuales),
        )

    eventos = est.detectar_novedades(previas, actuales)

    # Contexto de mercado: donde queda cada tasa de corte contra sus
    # comparables. Se calcula sobre el historico completo, no solo sobre la
    # ventana que devuelve la API, que son 30 dias y quedarian cortos.
    historico_ctx = list({**previas, **{r["clave"]: r for r in actuales}}.values())
    for ev in eventos:
        ev["contexto"] = contexto.calcular(
            ev["licitacion"], historico_ctx, ahora,
            ventana_dias=int(cfg["contexto_dias"]),
        )

    del_aviso = filtrar_para_aviso(eventos, cfg)

    log.info(
        "Eventos detectados: %d (%d pasan el filtro de aviso).",
        len(eventos), len(del_aviso),
    )
    for ev in del_aviso:
        log.info("  [%s] %s", ev["evento"].upper(), ev["licitacion"]["titulo"])

    # 3. Escribir salidas. Se escriben siempre, aun sin novedades: la planilla
    #    tiene que reflejar el estado del mercado, no solo los cambios.
    fusionado = est.fusionar(previas, actuales)
    historico = sorted(
        fusionado.values(),
        key=lambda r: (r.get("fecha_inicio") or datetime.min.replace(tzinfo=timezone.utc)),
        reverse=True,
    )
    para_planilla = [
        r for r in historico if r.get("categoria") in set(cfg["categorias_planilla"])
    ]

    if not args.dry_run:
        # Cada salida va en su propio try: que falle el xlsx no puede impedir
        # que salga el mail, que es lo que de verdad importa de esta corrida.
        def _escribir(nombre, fn, *a):
            try:
                fn(*a)
            except OSError as exc:
                errores_fuente.append(f"No pude escribir {nombre}: "
                                      f"{exc.strerror or exc}")
                log.error("No pude escribir %s: %s", nombre, exc)
            except Exception as exc:                       # noqa: BLE001
                errores_fuente.append(f"Fallo al generar {nombre}: {exc!r}")
                log.exception("Fallo al generar %s.", nombre)

        _escribir("la planilla CSV", salidas.escribir_csv,
                  para_planilla, cfg["archivo_csv"])
        _escribir("la planilla XLSX", salidas.escribir_xlsx,
                  para_planilla, cfg["archivo_xlsx"])
        try:
            import dashboard
            _escribir("el dashboard", dashboard.escribir,
                      para_planilla, cfg["archivo_dashboard"], ahora,
                      cfg["url_publica"])
        except ImportError:
            log.debug("dashboard.py no disponible: salteo el HTML.")

        # Copia a carpetas adicionales (tipicamente un share de red).
        if cfg["copiar_a"]:
            errores_fuente.extend(salidas.copiar_a(
                [cfg["archivo_dashboard"], cfg["archivo_xlsx"], cfg["archivo_csv"]],
                cfg["copiar_a"],
            ))

    # 4. Avisar.
    salida = 0
    hay_que_avisar = bool(del_aviso) or args.resumen
    silenciar = args.init or primera_corrida or args.dry_run

    # 4b. El informe es independiente de las novedades: manda el panorama
    #     completo aunque no haya cambiado nada desde la corrida anterior.
    if args.informe:
        datos_informe = informe.armar_datos(
            historico, ahora, dias=int(cfg["informe_dias"]))
        if args.dry_run:
            print("\n" + "=" * 70)
            print(informe.cuerpo_texto(datos_informe, ahora))
            print("=" * 70 + "\n")
        elif not informe.hay_algo_para_contar(datos_informe):
            log.warning("No hay nada para informar en los ultimos %s dias: "
                        "no mando el informe.", cfg["informe_dias"])
        else:
            listo, motivo = cfg_mod.puede_mandar_mail(cfg)
            if not listo:
                log.error("No puedo mandar el informe: %s.", motivo)
                salida = 3
            else:
                rutas_inf = {
                    "dashboard": cfg["archivo_dashboard"],
                    "xlsx": cfg["archivo_xlsx"],
                    "csv": cfg["archivo_csv"],
                }
                adj_inf = [rutas_inf[a] for a in cfg["adjuntar_informe"]
                           if a in rutas_inf]
                if not notificar.enviar_informe(datos_informe, cfg, ahora, adj_inf):
                    salida = 3

    # Claves cuyo aviso no salio. No se guardan en el estado, asi que la
    # proxima corrida las vuelve a detectar en vez de darlas por avisadas.
    sin_avisar: set[str] = set()

    if hay_que_avisar and not silenciar:
        listo, motivo = cfg_mod.puede_mandar_mail(cfg)
        if not listo:
            log.error("Hay %d novedades pero no puedo mandar el mail: %s.",
                      len(del_aviso), motivo)
            sin_avisar = {e["licitacion"]["clave"] for e in del_aviso}
            salida = 3
        else:
            rutas = {
                "dashboard": cfg["archivo_dashboard"],
                "xlsx": cfg["archivo_xlsx"],
                "csv": cfg["archivo_csv"],
            }
            adjuntos = [rutas[a] for a in cfg["adjuntar"] if a in rutas]
            if not notificar.enviar(del_aviso, cfg, ahora,
                                    avisos=errores_fuente, adjuntos=adjuntos):
                sin_avisar = {e["licitacion"]["clave"] for e in del_aviso}
                salida = 3
    elif args.dry_run and del_aviso:
        print("\n" + "=" * 70)
        print(notificar.cuerpo_texto(del_aviso, ahora))
        print("=" * 70 + "\n")
    elif not del_aviso:
        log.info("Sin novedades para avisar.")

    # 5. Guardar estado. Lo que no se pudo avisar NO se guarda: si se guardara,
    #    quedaria como ya conocido y la novedad no se avisaria nunca -- el bot
    #    perderia avisos en silencio, que es la peor falla posible en algo que
    #    existe justamente para avisar.
    if sin_avisar:
        log.warning(
            "%d novedades quedan pendientes porque no salio el mail. No las "
            "doy por avisadas: la proxima corrida las vuelve a detectar.",
            len(sin_avisar),
        )
        fusionado = {k: v for k, v in fusionado.items()
                     if k not in sin_avisar or k in previas}
        # Para las que ya existian, se conserva la version previa, asi el
        # cambio se vuelve a detectar en la corrida siguiente.
        for clave in sin_avisar & previas.keys():
            fusionado[clave] = previas[clave]

    if not args.dry_run:
        try:
            est.guardar(cfg["archivo_estado"], fusionado)
        except OSError as exc:
            log.error("No pude guardar el estado: %s", exc)
            return 1

    log.info("Corrida terminada %s (hora Argentina). Historico: %d licitaciones.",
             fecha_hora(ahora), len(fusionado))

    # Una fuente caida no invalida la corrida, pero tiene que notarse.
    if errores_fuente and salida == 0:
        salida = 4
    return salida


if __name__ == "__main__":
    sys.exit(main())
