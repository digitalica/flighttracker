FROM node:20-slim

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY src/ src/
COPY tsconfig.json ./
RUN npm run build && npm prune --omit=dev

ENV DB_PATH=/data/flighttracker.db
EXPOSE 5000

COPY templates/ templates/
COPY static/ static/

CMD ["node", "dist/server.js"]
