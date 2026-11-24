FROM node:24-bookworm-slim AS frontend
WORKDIR /build/frontend
RUN npm install -g pnpm@11.10.0
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY accounts/ accounts/
COPY chat/ chat/
COPY config/ config/
COPY dashboard/ dashboard/
COPY llm/ llm/
COPY memory/ memory/
COPY world/ world/
COPY manage.py ./
COPY frontend/src/design/assets/ frontend/src/design/assets/
COPY frontend/src/assets/dohyun.webp frontend/src/assets/dohyun.webp
COPY --from=frontend /build/frontend/dist/assets/ frontend/dist/assets/
RUN DJANGO_SECRET_KEY=static-build POSTGRES_PASSWORD=unused python manage.py collectstatic --noinput \
    && useradd --create-home wassup
COPY deploy/start deploy/release deploy/
USER wassup
CMD ["sh", "deploy/start"]
