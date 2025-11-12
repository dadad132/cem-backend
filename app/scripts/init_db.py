import anyio

from app.core.database import init_models


async def main():
    await init_models()


if __name__ == "__main__":
    anyio.run(main)
