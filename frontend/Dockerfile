# ---- build stage ----
FROM node:22-alpine AS build
WORKDIR /app
# Public sub-path the SPA is served under (must match nginx.conf prefix)
ARG VITE_BASE=/transcribe/
ENV VITE_BASE=$VITE_BASE
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

# ---- serve stage ----
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
