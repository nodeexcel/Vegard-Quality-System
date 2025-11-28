import argparse
import uvicorn
from app.config import settings

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the FastAPI application")
    parser.add_argument("--host", type=str, default=None, help="Host to bind to (default: from .env)")
    parser.add_argument("--port", type=int, default=None, help="Port to bind to (default: from .env)")
    
    args = parser.parse_args()
    
    host = args.host if args.host is not None else settings.HOST
    port = args.port if args.port is not None else settings.PORT
    
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=settings.ENVIRONMENT == "development",
        limit_concurrency=1000,
        limit_max_requests=10000,
        timeout_keep_alive=5
    )

