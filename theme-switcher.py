#!/usr/bin/env python3

import dbus
import os
import subprocess
import yaml
from dbus.mainloop.glib import DBusGMainLoop
from dataclasses import dataclass
from enum import IntEnum
from gi.repository import GLib
from typing import Optional


HOME = os.getenv("HOME", None)
if HOME is None:
    raise RuntimeError
CONFIG_DIR = os.path.join(os.getenv("XDG_CONFIG_HOME",
                                    os.path.join(HOME, ".config")),
                          "theme-switcher")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.yaml")


class theme(IntEnum):
    light = 0
    dark = 1


@dataclass
class Delimiters:
    r"""Defines delimiters used for parsing configuration sections.

    Attributes:
        begin: The string marking the beginning of a configuration section.
            Example: ``<<< theme-switcher <<<``.
        separator: The string separating light and dark theme configurations.
            Example: ``======``.
        end: The string marking the end of a configuration section.
            Example: ``>>> theme-switcher >>>``.
    """
    begin: str
    separator: str
    end: str


@dataclass
class Commands:
    """Stores the commands to be executed when switching between themes.

    Attributes:
        dark_to_light: The list of commands to execute when switching from
                       dark mode to light mode.
        light_to_dark: The list of commands to execute when switching from
                       light mode to dark mode.
    """
    dark_to_light: list[str]
    light_to_dark: list[str]


@dataclass
class AppConfig:
    """Represents a configuration file for an application.

    Attributes:
        name: The identifier for the configuration file.
            Example: ``kitty``
        path: The file system path to the configuration file.
            Example: ``$XDG_CONFIG_HOME/kitty/kitty.conf``
        comment_token: The character(s) used for commenting in this file format.
            Example: ``#``
    """
    name: str
    path: str
    comment_token: str


@dataclass
class ExtensionSetting:
    """Represents a single setting for a GNOME Shell extension.

    Attributes:
        path: The DConf path for the setting relative to extension base path.
            Example: ``panel/blur``
        light: The value to be set when in light theme mode.
            Example: ``false``
        dark: The value to be set when in dark theme mode.
            Example: ``true``
    """
    path: str
    light: Optional[str]
    dark: Optional[str]


@dataclass
class Extension:
    name: str
    settings: list[ExtensionSetting]


@dataclass
class Config:
    """Main configuration class that holds all theme switching settings.

    Attributes:
        delimiters: The delimiters used in configuration files
        commands: The commands to execute during theme switches
        config_files: The list of configuration files to modify
        extensions: The list of GNOME Shell extensions to configure
    """
    delimiters: Delimiters
    commands: Commands
    config_files: list[AppConfig]
    extensions: list[Extension]

    @classmethod
    def from_dict(cls, data: dict):
        """Creates a Config instance from a dictionary.

        Args:
            data: A dictionary containing configuration data from YAML file.

        Returns:
            A new Config instance populated with the provided data.
        """
        delimiters = Delimiters(**data['delimiters'])
        commands = Commands(**data['commands'])
        
        config_files = [AppConfig(**cf) for cf in data['config_files']]
        
        extensions = []
        for ext in data['extensions']:
            settings = [ExtensionSetting(**setting) for setting in ext['settings']]
            extensions.append(Extension(name=ext['name'], settings=settings))
        
        return cls(delimiters, commands, config_files, extensions)


def load_config() -> Config:
    r"""Loads configuration from ``CONFIG_FILE``."""
    with open(CONFIG_FILE, 'r') as file:
        config_dict = yaml.safe_load(file)
    return Config.from_dict(config_dict)


def comment(line: str, comment_token: str) -> str:
    r"""Comment a line with the given comment token.

    .. TODO::
       Add support for closing comment token.

       Currently, only config files with a comment token on the beginning of
       the line are supported.

    Args:
        line: The line to be commented.
        comment_token: The comment token.

    Returns:
        The commented line.
    """
    if line.startswith(comment_token):
        return line
    return f"{comment_token} {line}"


def uncomment(line: str, comment_token: str) -> str:
    r"""Uncomment a line with the given comment token.

    .. TODO::
       Add support for closing comment token.

       Currently, only config files with a comment token on the beginning of
       the line are supported.

    Args:
        line: The line to be uncommented.
        comment_token: The comment token.

    Returns:
        The uncommented line.
    """
    if not line.startswith(comment_token):
        return line
    return line[len(comment_token):].lstrip()


def modify_config_file(config: Config, file: str, comment_token: str, t: theme):
    r"""Comment/uncomment lines in a config file depending on the theme

    .. TODO::
       Add support for closing comment token.

       Currently, only config files with a comment token on the beginning of
       the line are supported.

    Args:
        config: The loaded configuration.
        file: The path to the config file.
        comment_token: The comment token.
        t: The theme.
    """
    path = os.path.expandvars(file)

    with open(file, "r") as f:
        lines = f.readlines()

    section: Optional[theme] = None
    for i, line in enumerate(lines):
        cleaned_line = line.replace(comment_token, "").strip()

        if cleaned_line == config.delimiters.begin:
            section = theme.light
            continue
        if section == theme.light and cleaned_line.startswith(config.delimiters.separator):
            section = theme.dark
            continue
        if cleaned_line == config.delimiters.end:
            break

        if t == theme.dark:
            if section == theme.light:
                lines[i] = comment(lines[i], comment_token)
            else:
                lines[i] = uncomment(lines[i], comment_token)
        else:
            if section == theme.dark:
                lines[i] = comment(lines[i], comment_token)
            else:
                lines[i] = uncomment(lines[i], comment_token)

    with open(path, "w") as f:
        f.writelines(lines)


def run_command(command: str) -> tuple[int, str]:
    """Runs a shell command and returns its exit code and output."""
    process = subprocess.run(command, shell=True, text=True, capture_output=True)
    return process.returncode, process.stdout + process.stderr


def apply_extension_settings(config: Config, mode: theme):
    """Applies theme-specific settings to GNOME Shell extensions.

    Args:
        config: The loaded configuration.
        mode: The current theme mode ("light" or "dark").
    """
    for extension in config.extensions:
        for setting in extension.settings:
            if mode == theme.light:
                value = getattr(setting, "light", None)
            else:
                value = getattr(setting, "dark", None)
            if value is not None:
                path = f"/org/gnome/shell/extensions/{extension.name}/{setting.path}"
                command = f'dconf write {path} "{value}"'
                run_command(command)


def toggle_theme(config: Config, namespace: str, key: str, value: int):
    """Toggle theme based on system appearance changes.

    Args:
        config: The loaded configuration.
        namespace: The DBus namespace of the setting that changed.
        key: The specific setting key that changed.
        value: The new value (0 for light, 1 for dark).
    """
    APPEARANCE_NAMESPACE = "org.freedesktop.appearance"
    COLOR_SCHEME_KEY = "color-scheme"

    if namespace == APPEARANCE_NAMESPACE and key == COLOR_SCHEME_KEY:
        mode: Optional[theme] = theme.light if value == 0 else theme.dark if value == 1 else None
        if mode is None:
            return

        for config_file in config.config_files:
            path = os.path.expandvars(config_file.path)
            if not os.path.exists(path):
                continue
            modify_config_file(config, path, config_file.comment_token, mode)

        if mode == theme.light:
            commands = config.commands.dark_to_light
        else:
            commands = config.commands.light_to_dark
        
        for command in commands:
            run_command(command)
        
        apply_extension_settings(config, mode)


def main():
    config = load_config()
    DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    settings = bus.get_object("org.freedesktop.portal.Desktop",
                              "/org/freedesktop/portal/desktop")
    settings.connect_to_signal(
        "SettingChanged",
        lambda n, k, v: toggle_theme(config, n, k, v),
        dbus_interface="org.freedesktop.portal.Settings"
    )
    loop = GLib.MainLoop()
    loop.run()


if __name__ == "__main__":
    main()
