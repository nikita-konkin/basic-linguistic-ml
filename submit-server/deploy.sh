#!/usr/bin/env bash
# Первое развёртывание приёма работ на сервере: сертификат, контейнер, nginx.
#
#     sudo bash ~/projects/mlg-submit/deploy.sh
#
# Ожидает, что рядом лежит проект с nginx и certbot (../ssl-nginx-docker), а
# в его docker-compose.yaml уже есть сервис mlg-submit (compose-service.yaml).
# Каждый шаг проверяется; повторный запуск безопасен: то, что уже сделано,
# пропускается. nginx.conf правится только когда сертификат уже получен, с
# резервной копией и проверкой `nginx -t`; при ошибке копия возвращается.
set -euo pipefail

DOMAIN=submit.konkin-nikita.ru
HERE=$(cd "$(dirname "$0")" && pwd)
STACK=$(cd "$HERE/../ssl-nginx-docker" && pwd)
CONF=$STACK/nginx/nginx.conf
LIVE=$STACK/certbot/conf/live/$DOMAIN
STAMP=$(date +%Y%m%d-%H%M%S)

cd "$STACK"

echo "1/5 DNS"
ip=$(getent hosts "$DOMAIN" | awk '{print $1; exit}')
if [ -z "$ip" ]; then
    echo "    $DOMAIN пока не находится в DNS — подождите и запустите снова"
    exit 1
fi
echo "    $DOMAIN → $ip"

echo "2/5 Сертификат"
if [ -f "$LIVE/fullchain.pem" ]; then
    echo "    уже есть"
else
    # Почта для Let's Encrypt — та же, что у остальных сертификатов в compose.
    email=$(grep -o -m1 -- '--email [^ ]*' docker-compose.yaml | awk '{print $2}')
    if [ -z "$email" ]; then
        echo "    не нашёл --email в docker-compose.yaml"
        exit 1
    fi
    docker run --rm \
        -v "$STACK/certbot/conf:/etc/letsencrypt" \
        -v "$STACK/certbot/www:/var/www/certbot" \
        certbot/certbot certonly --webroot -w /var/www/certbot \
        -d "$DOMAIN" --email "$email" --agree-tos --no-eff-email --non-interactive
fi
if [ ! -f "$LIVE/fullchain.pem" ]; then
    echo "    сертификата нет — nginx не трогаю"
    exit 1
fi

echo "3/5 Контейнер mlg-submit"
mkdir -p "$HERE/data"
chown 1000:1000 "$HERE/data"
docker compose up -d --build mlg-submit
# Спрашиваем сам сервис: статус здоровья Docker появляется только после
# первой проверки, а она бывает не сразу.
ready=no
for _ in $(seq 1 30); do
    if docker exec mlg-submit python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8095/health', timeout=3)" 2>/dev/null; then
        ready=yes
        break
    fi
    sleep 2
done
if [ "$ready" != yes ]; then
    echo "    сервис не отвечает. Состояние: $(docker inspect -f '{{.State.Status}}' mlg-submit 2>/dev/null)"
    docker logs --tail 20 mlg-submit || true
    exit 1
fi
echo "    работает"

echo "4/5 nginx"
if grep -q "server_name $DOMAIN;" "$CONF"; then
    echo "    блок для $DOMAIN уже есть"
else
    cp -p "$CONF" "$CONF.bak-$STAMP"
    # Блок вставляется перед последней «}» — той, что закрывает http.
    # Файл переписывается на месте: он смонтирован в контейнер по отдельности.
    python3 - "$CONF" "$HERE/nginx-submit.conf" <<'PY'
import sys
conf, block = sys.argv[1], open(sys.argv[2], encoding="utf-8").read()
text = open(conf, encoding="utf-8").read().rstrip()
if not text.endswith("}"):
    sys.exit("nginx.conf не заканчивается на «}»")
with open(conf, "w", encoding="utf-8") as handle:
    handle.write(text[:-1].rstrip() + "\n\n" + block.rstrip() + "\n\n}\n")
PY
    if docker compose exec -T nginx nginx -t; then
        docker compose exec -T nginx nginx -s reload
        echo "    добавлен; копия прежнего: $CONF.bak-$STAMP"
    else
        cp -p "$CONF.bak-$STAMP" "$CONF"
        echo "    nginx -t не прошёл — nginx.conf возвращён как был"
        exit 1
    fi
fi

echo "5/5 Проверка"
sleep 2
# Сначала через nginx на этой машине: сертификат и прокси.
if ! curl -fsS --max-time 10 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/health" >/dev/null; then
    echo "    nginx не отдаёт https://$DOMAIN/health — см. docker logs nginx и docker logs mlg-submit"
    exit 1
fi
echo "    nginx и сертификат в порядке"
# Затем по внешнему адресу; из домашней сети это может не сработать, если
# роутер не умеет обращаться к своему же внешнему адресу.
if curl -fsS --max-time 10 "https://$DOMAIN/health" >/dev/null; then
    echo "    готово: https://$DOMAIN"
else
    echo "    по внешнему адресу отсюда не открылось — проверьте https://$DOMAIN/health с телефона через мобильный интернет"
fi
