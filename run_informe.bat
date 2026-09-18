@echo off
REM ---------------------------------------------------------------------
REM Informe diario - lanzador para el Programador de tareas.
REM Es el mail que se reenvia: el panorama del mercado, no las novedades.
REM Conviene programarlo una vez por dia, despues del cierre de la rueda
REM (17:00 hora Argentina), cuando ya estan los resultados del dia.
REM
REM Editar estas dos lineas con las rutas de esta maquina.
REM ---------------------------------------------------------------------
set PYTHON=C:\Users\fgalante\AppData\Local\Python\pythoncore-3.14-64\python.exe
set CARPETA=C:\Users\fgalante\licitaciones-bot

REM pushd en lugar de cd: cd falla si la carpeta esta en una ruta UNC.
pushd "%CARPETA%" || exit /b 1

if not exist "datos" mkdir "datos"

REM Se corre el bot normal primero para que los datos esten frescos, y
REM despues se manda el informe con lo que quedo en el estado.
echo [%DATE% %TIME%] --- informe diario >> datos\bot.log
"%PYTHON%" bot.py --informe >> datos\bot.log 2>&1
set CODIGO=%ERRORLEVEL%

if %CODIGO% NEQ 0 (
  echo [%DATE% %TIME%] el informe termino con codigo %CODIGO% >> datos\bot.log
)

popd
exit /b %CODIGO%
