FROM python:3.12-slim

WORKDIR /app

# 安装 uv
RUN pip install --no-cache-dir uv

# 拷贝依赖配置
COPY pyproject.toml ./

# 同步依赖 (排除 dev)
RUN uv pip install --system --no-cache -r <(uv pip compile pyproject.toml)

# 拷贝代码
COPY src/ ./src/

ENV PYTHONPATH=/app/src
EXPOSE 8766

CMD ["python", "-m", "stock_mcp", "--config", "/app/config.toml", "--transport", "http", "--host", "0.0.0.0", "--port", "8766"]
