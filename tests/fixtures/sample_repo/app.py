"""Entry point for the sample fixture repo."""
import os


def main() -> None:
    print("hello", os.getenv("PORT"))


if __name__ == "__main__":
    main()
