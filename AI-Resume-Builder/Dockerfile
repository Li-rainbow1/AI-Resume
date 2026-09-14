ARG NODE_IMAGE=node:22-alpine
ARG NGINX_IMAGE=nginx:alpine


# 构建阶段只生成前端静态资源。
FROM ${NODE_IMAGE} AS build

WORKDIR /app

# 先复制依赖清单，提升 Docker 层缓存命中率。
COPY package.json package-lock.json ./

RUN npm ci

# 再复制前端源码并执行构建。
COPY . .

RUN npm run build-only

# 运行阶段使用 Nginx 托管静态资源并代理后端。
FROM ${NGINX_IMAGE}

COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
