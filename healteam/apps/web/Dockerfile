# HealTeam Web — Dockerfile (W1)
#
# Build context: healteam/ root (see ../../docker-compose.yml).

# ---- deps ------------------------------------------------------------------
FROM node:20-alpine AS deps
WORKDIR /build
COPY apps/web/package.json apps/web/pnpm-lock.yaml* ./
RUN corepack enable && \
    if [ -f pnpm-lock.yaml ]; then pnpm install --frozen-lockfile; else npm install; fi

# ---- builder ---------------------------------------------------------------
FROM node:20-alpine AS builder
WORKDIR /build
COPY --from=deps /build/node_modules ./node_modules
COPY apps/web ./
ENV NEXT_TELEMETRY_DISABLED=1
RUN corepack enable && \
    if [ -f pnpm-lock.yaml ]; then pnpm build; else npm run build; fi

# ---- runner ----------------------------------------------------------------
FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000

# Non-root user.
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001

COPY --from=builder /build/public ./public
COPY --from=builder --chown=nextjs:nodejs /build/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /build/.next/static ./.next/static

USER nextjs
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://127.0.0.1:3000/ || exit 1

CMD ["node", "server.js"]
