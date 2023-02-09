# Recoll WebUI


This is an Dockerfile for recoll + an update of (https://framagit.org/medoc92/recollwebui) that is an updated clone of Koniu's original version on GitHub (https://github.com/koniu/recoll-webui), which has not been updated lately, and is now slightly obsolete.

**Recoll WebUI** is a Python-based web interface for **Recoll** text search
tool for Unix/Linux.

.. image:: http://i.imgur.com/n8qTnBg.png

* WebUI homepage: https://github.com/koniu/recoll-webui
* Recoll homepage: http://www.lesbonscomptes.com/recoll


## To build first

```sh
mkdir somefolder
cd somefolder
git clone https://github.com/ameisehaufen/kmrecollwebui.git
cp kmrecollwebui/Dockerfile-recollwebui .

docker build -t kolohals/recollweb:latest --build-arg TIMEZONE=America/Sao_Paulo -f Dockerfile-recollwebui .
```

## Create Container

```sh
app="recollwebui" && \
appDir=/AppFolder/"${app}" && \
dataDir=/dataFolder && \
mkdir -p "${appDir}"
touch "${appDir}"/"${app}".conf
docker stop "${app}"; docker rm "${app}"
[ -d "${appDir}" ] && \
docker run -d \
  --name=${app} \
  -p 58131:8080 \
  -e USER_UID=1000 \
  -e USER_GID=1000 \
  -e TZ=America/Sao_Paulo \
  -e RECOLL_INDEX_DIR="$dataDir" \
  --restart=unless-stopped \
  -e DISPLAY=${DISPLAY} -v /tmp/.X11-unix:/tmp/.X11-unix:ro \
  --log-driver json-file \
  --log-opt max-size=50m \
  -v "$dataDir":/data \
  -v "${appDir}"/"${app}".conf:/home/recolluser/.recoll \
kolohals/${app}:latest
```
