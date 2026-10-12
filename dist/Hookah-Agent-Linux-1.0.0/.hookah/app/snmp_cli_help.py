"""Descriptive CLI help shared by the Agent and Manager."""

import argparse


def show_command_help(parser, topics=()):
    selected = parser
    for topic in topics:
        commands = next((action for action in selected._actions if isinstance(action, argparse._SubParsersAction)), None)
        if commands is None or topic not in commands.choices:
            raise ValueError(f"Unknown help topic: {topic}")
        selected = commands.choices[topic]
    print(selected.format_help())
