"""Console script for aioxmppd."""
import click
import sys
import yaml
from aioxmppd.aioxmppd import AioxmppServer


def CommandWithConfigFile(config_file_param_name):
    class CustomCommandClass(click.Command):
        def invoke(self, ctx):
            config_file = ctx.params[config_file_param_name]
            if config_file is not None:
                with open(config_file) as f:
                    config_data = yaml.safe_load(f)
                    for param, value in ctx.params.items():
                        if value is None and param in config_data:
                            ctx.params[param] = config_data[param]

            return super(CustomCommandClass, self).invoke(ctx)

    return CustomCommandClass


@click.command(cls=CommandWithConfigFile("config_file"))
@click.option("--log_level", default="INFO", help="Sets the logging level")
@click.option("--log_file", default="aioxmppd.log", help="Sets the logging filename")
@click.option(
    "--log_rotation", default=None, help="Sets the logging file rotation mode"
)
@click.option("--hostname", default="localhost", help="Server hostname")
@click.option(
    "--client_port", default=5222, help="Port for client-to-server connections"
)
@click.option(
    "--server_port", default=5269, help="Port for client-to-server connections"
)
@click.option(
    "-c",
    "--config_file",
    type=click.Path(exists=True),
    help="Loads configuration from a yaml file",
)
def main(
    log_level, log_file, log_rotation, hostname, client_port, server_port, config_file
):
    config = {
        "logger": {"level": log_level, "filename": log_file, "rotation": log_rotation},
        "host": {
            "hostname": hostname,
            "ports": {"client": client_port, "server": server_port},
        },
    }
    server = AioxmppServer(config)
    server.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())  # pragma: no cover
