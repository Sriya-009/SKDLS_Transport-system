FROM node:18-alpine as build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --silent || true
COPY frontend/ ./
RUN npm run build --silent || true

FROM nginx:stable-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY deployment/nginx/transport-system.conf /etc/nginx/conf.d/default.conf
EXPOSE 80 443
CMD ["nginx", "-g", "daemon off;"]
