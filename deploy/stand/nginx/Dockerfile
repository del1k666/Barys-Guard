# Контекст сборки — корень репозитория (см. deploy/stand/docker-compose.yml).
FROM node:22-alpine AS console

WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM nginx:1.27-alpine

COPY --from=console /web/dist /usr/share/nginx/html
COPY deploy/stand/nginx/stand.conf /etc/nginx/conf.d/default.conf
COPY deploy/stand/nginx/proxy-headers.conf /etc/nginx/conf.d/proxy-headers.conf
