"""Point d'entrée : python -m edumoov_mcp (ou la commande `edumoov-mcp` une fois installé)."""
from .server import mcp


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
