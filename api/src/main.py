#!/usr/bin/env python3
"""
CryptoMacro Analyst Bot — REST API
Phase 6 (Weeks 11-12) — DEL-5, DEL-6

FastAPI server providing endpoints for:
- /api/health — System health status
- /api/regime — Current regime state
- /api/alerts — Recent alerts with filtering
- /api/features — Latest computed features per asset
- /api/onchain — On-chain flow data
- /api/analysis — LLM analysis reports
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI
import uvicorn
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="CryptoMacro Analyst Bot API",
    version="0.1.0",
    description="REST API for dashboard and integrations"
)


@app.get("/")
async def root():
    """API root endpoint."""
    return {
        "name": "CryptoMacro Analyst Bot API",
        "version": "0.1.0",
        "status": "running (skeleton mode)"
    }


@app.get("/api/health")
async def health():
    """Health check endpoint (stub)."""
    # TODO (DEL-5): Implement full health model from OPS-1
    return {"status": "ok", "mode": "skeleton"}


def main() -> None:
    """Start API server."""
    logger.info("CryptoMacro API starting...")
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
