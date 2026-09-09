from uvicorn.server import Server

from backend.settings import get_settings

if __name__ == "__main__":
    Server(config=get_settings().to_uvicorn_config()).run()
