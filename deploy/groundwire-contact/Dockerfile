FROM python:3.12-alpine
RUN adduser -D -H app
WORKDIR /app
COPY app.py .
USER app
ENV PYTHONUNBUFFERED=1
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/healthz || exit 1
CMD ["python", "app.py"]
