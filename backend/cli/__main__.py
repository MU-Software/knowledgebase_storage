import typer

from backend.cli.worker import worker

cli = typer.Typer(no_args_is_help=True, help="knowledgebase-storage management commands")


@cli.callback()
def main() -> None:
    pass


cli.command()(worker)

if __name__ == "__main__":
    cli()
