# 构建上下文是仓库根目录（见 deploy/compose.yaml），这样能同时拿到 web/ 和 deploy/nginx/
FROM node:22.13.1-alpine AS build
WORKDIR /src
COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund
COPY web/ .
ARG VITE_SHOW_CONTENT=false
ARG VITE_GRAFANA_URL=http://localhost:3000
ENV VITE_SHOW_CONTENT=$VITE_SHOW_CONTENT VITE_GRAFANA_URL=$VITE_GRAFANA_URL
RUN npm run build

FROM nginx:1.27.3-alpine
COPY --from=build /src/dist /usr/share/nginx/html
COPY deploy/nginx/default.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
