FROM node:24-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY package.json package-lock.json ./
RUN npm ci
COPY next.config.mjs ./
COPY app ./app
COPY components ./components
COPY public ./public
RUN npm run build

FROM caddy:2-alpine
ENV PORT=8080
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /app/out /srv
EXPOSE 8080
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
