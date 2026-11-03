#!/bin/sh
# Arranca TDT local y abre el navegador. Antes para la instancia que ya esté en marcha: solo puede haber una.
# Ajustes de esta máquina (rutas de whisper-server, etc.): iniciar.local.sh, que no se sube a git.
# Ejemplo:  export TDT_WHISPER_BIN=/ruta/a/whisper-server
cd "$(dirname "$0")" || exit 1
[ -f ./iniciar.local.sh ] && . ./iniciar.local.sh

# Lo primero: si ya hay un TDT local en marcha (el servidor de este proyecto, no otra cosa), se para y se espera a
# que suelte el puerto. Se filtra por el ejecutable (`comm`): `pgrep -f` también encontraría la shell que lanza esto
# si su línea de comandos menciona el patrón, y la mataría.
parar() {
  pids=$(ps -eo pid=,comm=,args= 2>/dev/null | awk '
    $2 ~ /^python/ && /-m tdtlocal/ {print $1; next}
    $2 ~ /^tdt-local-serve/ {print $1}
  ')
  [ -z "$pids" ] && return 0
  echo "Parando TDT local ($(echo $pids | tr '\n' ' '))…"
  kill $pids 2>/dev/null
  i=0
  while [ "$i" -lt 20 ]; do
    vivo=0
    for p in $pids; do
      if [ "$p" != "$$" ] && kill -0 "$p" 2>/dev/null; then vivo=1; fi
    done
    [ "$vivo" -eq 0 ] && return 0
    sleep 0.25 2>/dev/null || sleep 1
    i=$((i + 1))
  done
  kill -9 $pids 2>/dev/null
}
parar

exec python3 -m tdtlocal --open "$@"
