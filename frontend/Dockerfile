# The browser app for `docker compose up`: built once, then served by nginx, which also
# forwards /api to the API, so the browser sees one origin for pages and API, as with Vite's
# dev server (vite.config.ts). For working on the app, `npm run dev` gives hot reload instead.

FROM node:24.11.1-alpine3.22 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# The same build CI runs: type-check, then bundle into dist/.
RUN npm run build

FROM nginx:1.29.3-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
