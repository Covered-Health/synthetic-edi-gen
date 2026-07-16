import cyclopts

from synthetic_edi_gen.daily_feed import daily_feed
from synthetic_edi_gen.generate import generate

app = cyclopts.App(
    name="synthetic-edi-gen",
    help="Generate synthetic EDI 835/837 healthcare data.",
)
app.default(generate)
app.command(daily_feed, name="daily-feed")


def main():
    app()


if __name__ == "__main__":
    main()
