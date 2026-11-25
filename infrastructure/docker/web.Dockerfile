FROM node:24-alpine

WORKDIR /app

RUN apk add --no-cache curl \
    && corepack enable

COPY infrastructure/docker/web-entrypoint.sh /usr/local/bin/web-entrypoint.sh
RUN chmod +x /usr/local/bin/web-entrypoint.sh

EXPOSE 3000

HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=10s \
  CMD curl -fsS http://127.0.0.1:3000/ || exit 1

ENTRYPOINT ["/usr/local/bin/web-entrypoint.sh"]
