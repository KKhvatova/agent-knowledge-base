"""`kb` command-line interface. Commands are added as the POC grows."""

import typer

app = typer.Typer(help="Agent Knowledge Base POC", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Agent Knowledge Base POC."""


@app.command()
def version() -> None:
    """Print the POC version."""
    typer.echo("agent-knowledge-base 0.1.0")


if __name__ == "__main__":
    app()
