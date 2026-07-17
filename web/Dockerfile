# Build from the REPO ROOT, not web/: the site reads ../docs/*.md at build time.
#   docker build -f web/Dockerfile -t tuto-web .
FROM node:22-alpine
RUN npm install -g pnpm@11
WORKDIR /app/web
COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY docs/ /app/docs/
COPY web/ ./
RUN pnpm build
EXPOSE 5297
CMD ["pnpm", "start"]
