"""CLI entry point: uv run synthetic-edi-gen --count 100 --seed 42."""

import cyclopts

from synthetic_edi_gen.generate import generate


def main():
    cyclopts.run(generate)


if __name__ == "__main__":
    main()
