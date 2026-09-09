FROM mcr.microsoft.com/playwright:v1.63.0-noble
WORKDIR /tests
COPY knowledge-docker/tests/web/package.json knowledge-docker/tests/web/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY knowledge-docker/tests/web/ ./
CMD ["npm", "test"]
