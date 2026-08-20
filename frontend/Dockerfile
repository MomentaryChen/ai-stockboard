# Build context is frontend/. The result is a static bundle served by nginx,
# which also proxies /api to the FastAPI container (see nginx.conf).
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

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
