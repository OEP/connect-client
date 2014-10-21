#!/bin/sh

if [ -z "$base" -o -z "$modlib" -o "$base" = "-h" -o "$base" = "--help" ]; then
	echo >&2 "usage: $0 software-install-directory modulefiles-directory"
	exit 2
fi

## Setup stuff.
## Actual installation steps are below.
## All these variables and functions are exposed to install.sub files.

from=$(dirname "$0")
base="$1"
modlib="$2"

copyfiles () {
	rsync -a --exclude install.sub "$@"
}

status () {
	echo "[install]" "$@"
}

subinstall () {
	(
		cd "$1" &&
		. ./install.sub
	)
}


## Installation procedure:

cd $(dirname "$0")
mkdir -p "$base" 2>/dev/null
mkdir -p "$base/bin" 2>/dev/null
mkdir -p "$modlib/connect" 2>/dev/null

if [ ! -d "$base" ]; then
	echo >&2 "Cannot create $base - cannot install."
	exit 10
fi

# Install mofulefiles
subinstall modules

# Install bosco (connect client)
subinstall bosco

# Install connect scripts
status Installing Connect user commands
subinstall connect

# tutorial has no install.sub because it's a subrepo
status ... tutorial command
copyfiles scripts/tutorial/tutorial "$base/bin/"

