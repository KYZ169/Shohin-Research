from fastapi import FastAPI

app = FastAPI(title="resale-radar")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
