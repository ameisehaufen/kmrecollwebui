# Recollcmd + WebUI + Docker

This is an Dockerfile for recoll + an update of (<https://framagit.org/medoc92/recollwebui>) that is also an updated clone of Koniu's original version on GitHub (<https://github.com/koniu/recoll-webui>), which has not been updated lately, and is now slightly obsolete.

**Recoll WebUI** is a Python-based web interface for **Recoll** text search tool for Unix/Linux.

![RecollImg](http://i.imgur.com/n8qTnBg.png)

* WebUI homepage: <https://github.com/koniu/recoll-webui>
* Recoll homepage: <http://www.lesbonscomptes.com/recoll>

## Build docker image

Only if you do not want to user the dockerhub image

```sh
git clone https://github.com/ameisehaufen/kmrecollwebui.git
docker build -t kolohals/recollweb:latest  --build-arg TIMEZONE=America/Sao_Paulo -f Dockerfile-recollwebui .
```

## Create Container

kolohals/recollweb:latest

```sh
app="recoll"
appDir=$HOME/DockerPersistentFiles/"${app}"
mkdir -p "${appDir}"/config
dataDirHost="/data"
confDir=/home/recolluser/.recoll
dataDirContainer=/data
docker stop "${app}"; docker rm "${app}"
[ -d "${appDir}" ] && \
docker run -d \
  --name=${app} \
  -p 58131:8080 \
  -e USER_UID=1000 \
  -e USER_GID=1000 \
  -e TZ=America/Sao_Paulo \
  --restart=unless-stopped \
  -e RECOLL_INDEX_DIR=$dataDirContainer \
  -e RECOLL_CONFDIR=$confDir \
  --log-driver json-file \
  --log-opt max-size=50m \
  -v "$dataDirHost":"$dataDirContainer" \
  -v "${appDir}"/config:"$confDir" \
kolohals/recollweb:latest && \
sleep 3 && \
docker exec "$app" runuser -l recolluser -c 'recollindex -c /home/recolluser/.recoll/'
```

## Add firefox extended support

```sh
firefox_profile="$(cat ~/.mozilla/firefox/profiles.ini | grep Path | cut -d '=' -f2 | grep default | tail -n 1)"
echo $firefox_profile
cat >> ~/.mozilla/firefox/"$firefox_profile"/prefs.js << EOL
user_pref("capability.policy.policynames", "localfilelinks");
user_pref("capability.policy.localfilelinks.sites", "http://razorcrest:58131 http://razorcrest");
user_pref("capability.policy.localfilelinks.checkloaduri.enabled", "allAccess");
EOL
```

## To index files

```sh
#!/bin/sh
docker exec "$app" runuser -l recolluser -c 'recollindex -c /home/recolluser/.recoll/'
```
