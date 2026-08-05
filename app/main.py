from fastapi import FastAPI

from app.api.routers import lottery_entries, manual_review_tasks, opportunities, watchlists

app = FastAPI(title="resale-radar")
app.include_router(watchlists.router)
app.include_router(lottery_entries.router)
app.include_router(opportunities.router)
app.include_router(manual_review_tasks.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
