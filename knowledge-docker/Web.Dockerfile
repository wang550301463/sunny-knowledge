FROM node:20.20.2-bookworm-slim AS build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.28.0-alpine
COPY knowledge-docker/web/nginx.conf /etc/nginx/nginx.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 8080
ENTRYPOINT ["nginx", "-g", "daemon off;"]
