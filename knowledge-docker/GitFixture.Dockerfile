FROM python:3.12.12-slim-bookworm
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources && apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=20 update -qq && apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=20 install -y --no-install-recommends git=1:2.39.5-0+deb12u3 && rm -rf /var/lib/apt/lists/*
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /fixture
COPY knowledge-docker/tests/git_fixture/server.py ./server.py
USER 65532:65532
EXPOSE 8080
CMD ["python", "/fixture/server.py"]