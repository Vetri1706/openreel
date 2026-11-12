FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --create-home reel
WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip install --no-cache-dir . && mkdir /data && chown reel:reel /data
USER reel
EXPOSE 8787
CMD ["openreel", "studio", "--host", "0.0.0.0", "--data-dir", "/data"]
