FROM python:3.14-slim
WORKDIR /app
COPY pyproject.toml .
# Dependencies come from pyproject.toml, the single place where versions are pinned.
RUN python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']))" \
      > requirements.txt \
    && pip install --no-cache-dir -r requirements.txt
COPY *.py constitution.md ./
ENV SOCIETY_DB_DIR=/data PYTHONUNBUFFERED=1
