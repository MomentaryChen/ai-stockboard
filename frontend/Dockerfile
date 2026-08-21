# Build context is frontend/. The result is a static bundle served by nginx,
# which also proxies /api to the FastAPI container (see nginx/).
FROM node:22-alpine AS build

# corepack pins pnpm to the "packageManager" version in package.json, so the
# image and the developer machine build with the same pnpm.
ENV PNPM_HOME=/pnpm \
    PATH=/pnpm:$PATH \
    COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN corepack enable

WORKDIR /app

# Dependency layer. pnpm-workspace.yaml carries the allowBuilds approval that
# lets esbuild's postinstall link its platform binary -- without it vite build
# fails. The store is a BuildKit cache mount, so repeat builds skip the download.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN --mount=type=cache,id=pnpm-store,target=/pnpm/store \
    pnpm install --frozen-lockfile

# devDependencies are needed here: `pnpm build` is `tsc -b && vite build`.
COPY . .
RUN pnpm build

FROM nginx:1.27-alpine

# conf.d/00-maps.conf is http-context only (map, log_format) and loads before
# default.conf, which the entrypoint below picks out of server-conf/ depending
# on whether a certificate was mounted. The partials under includes/ contain
# `location` blocks and must therefore stay *out* of conf.d, which nginx reads
# as http context.
COPY nginx/conf.d/00-maps.conf /etc/nginx/conf.d/00-maps.conf
COPY nginx/conf.d/http.conf nginx/conf.d/https.conf /etc/nginx/server-conf/
COPY nginx/includes/ /etc/nginx/includes/

# nginx's entrypoint ignores a *.sh here that is not executable, and a Windows
# checkout does not carry the execute bit -- so set it in the image rather than
# trusting the mode that arrived in the build context.
COPY nginx/entrypoint/20-select-server-conf.sh /docker-entrypoint.d/
RUN chmod +x /docker-entrypoint.d/20-select-server-conf.sh

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80
EXPOSE 443

# /healthz rather than / because in TLS mode every other path on port 80 is a
# redirect to 443, and wget following it into a self-signed certificate would
# report the container unhealthy for a reason that has nothing to do with it.
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/healthz >/dev/null || exit 1
