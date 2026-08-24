"""
DATA ENGINE — Dashboard Backend entry-point
Re-exports the FastAPI app from api.py so uvicorn can find it as
``services.dashboard_backend.main:app`` (standard naming convention).
"""

from services.dashboard_backend.api import app  # noqa: F401
