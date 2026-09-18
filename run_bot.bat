@echo off
REM ---------------------------------------------------------------------
REM Monitor de licitaciones - lanzador para el Programador de tareas.
REM Editar estas dos lineas con las rutas de esta maquina.
REM ---------------------------------------------------------------------
set PYTHON=C:\Users\fgalante\AppData\Local\Python\pythoncore-3.14-64\python.exe
set CARPETA=C:\Users\fgalante\licitaciones-bot

REM Credenciales. Mejor definirlas como variables de entorno del usuario
REM (Panel de control > Cuentas > Variables de entorno) y borrar estas dos
REM lineas: un .bat con la contrasena adentro queda en texto plano.
REM set SMTP_USUARIO=franco.galante@crecersgr.com.ar
REM set SMTP_PASSWORD=xxxxxxxxxxxxxxxx

REM pushd en lugar de cd: cd falla si la carpeta esta en una ruta UNC.
pushd "%CARPETA%" || exit /b 1

REM Sin esto, la primera corrida falla al redirigir el log: la carpeta datos
REM todavia no existe porque la crea el propio bot, despues de arrancar.
if not exist "datos" mkdir "datos"

echo [%DATE% %TIME%] --- arranca >> datos\bot.log
"%PYTHON%" bot.py >> datos\bot.log 2>&1
set CODIGO=%ERRORLEVEL%

REM 0 = ok. 4 = alguna fuente no respondio, el resto anduvo.
if %CODIGO% NEQ 0 (
  echo [%DATE% %TIME%] bot.py termino con codigo %CODIGO% >> datos\bot.log
)

popd
exit /b %CODIGO%
