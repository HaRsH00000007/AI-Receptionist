# =============================================================================
# Frontend image — Next.js, standalone output.
#
# `output: "standalone"` traces the exact module graph the server needs, so the
# runtime stage carries neither node_modules nor the build toolchain.
# =============================================================================

FROM node:24-alpine AS deps
WORKDIR /app
# npm ci, not npm install: the lockfile is the input, and a build that silently
# resolves a different tree than CI did is not a build worth shipping.
COPY package.json package-lock.json ./
RUN npm ci

FROM node:24-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Baked into the browser bundle at build time. Public by definition — a
# provider key must never reach a NEXT_PUBLIC_ variable.
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:24-alpine AS runtime
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs \
 && adduser --system --uid 1001 nextjs

COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000

CMD ["node", "server.js"]
