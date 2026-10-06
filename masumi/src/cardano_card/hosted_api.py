"""Public registration endpoint; execution stays on the private demo runtime."""

import os

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .models import INPUT_SCHEMA


app = FastAPI(title="Cardano Card Preprod", docs_url=None, redoc_url=None, openapi_url=None)
MESSAGE = "Preprod registration endpoint. New jobs are disabled until the hosted escrow backend is connected."


@app.middleware("http")
async def response_headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/")
async def index():
    return {"service": "Cardano Card", "network": "Preprod", "accepting_jobs": False,
            "message": MESSAGE}


@app.get("/healthz")
async def health():
    return {"status": "ok", "network": "Preprod", "accepting_jobs": False}


@app.get("/availability")
async def availability():
    return {"status": "unavailable", "type": "masumi-agent", "network": "Preprod",
            "accepting_jobs": False, "message": MESSAGE}


@app.get("/input_schema")
async def input_schema():
    return INPUT_SCHEMA


@app.post("/start_job")
@app.post("/provide_input")
@app.get("/status")
async def disabled_execution():
    # Do not parse/store request bodies or instantiate an escrow/card provider.
    return JSONResponse(status_code=503, content={"error": "execution_disabled", "message": MESSAGE})


def main():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")), access_log=False)


if __name__ == "__main__":
    main()
